"""
kg_audit.py — Provenance auditing and maintenance for the MSE Knowledge Graph.

Answers questions like:
  - How many triples are ontology scaffolding vs. actual material data?
  - Which materials came from the base CSV, and which were added later?
  - What did my demo runs / URL imports actually put in the graph?
  - How do I roll back a bad ingest without touching the base dataset?

This is what ex:hasSourceId was made mandatory for: every material carries
its origin, so the graph can be audited and selectively rolled back.
"""

import pandas as pd
from rdflib import RDF
from ontology.core import g, EX, Material

hasSourceId = EX.hasSourceId
hasFormula  = EX.hasFormula

# Canonical sources (mirrors llm/ingest_from_txt.py)
SOURCE_CSV_BASE     = "CSV_base"
SOURCE_CSV_EXTERNAL = "CSV_external"
SOURCE_ANONYMOUS    = "anonymous_text"


# ─── Core selectors ─────────────────────────────────────────────────────────────

def material_subjects():
    """All IRIs typed as ex:Material."""
    return set(g.subjects(RDF.type, Material))


def scaffolding_triples():
    """
    Triples that are NOT attached to a material — i.e. the ontology itself:
    class declarations, property declarations, domain/range/comment annotations.
    """
    mats = material_subjects()
    return [t for t in g if t[0] not in mats]


def source_of(m):
    """The source_id of one material (first value if several)."""
    v = g.value(m, hasSourceId)
    return str(v) if v is not None else None


# ─── Reporting ──────────────────────────────────────────────────────────────────

def kg_summary(verbose: bool = True) -> dict:
    """High-level census of the graph."""
    mats = material_subjects()
    scaffold = scaffolding_triples()
    by_source = {}
    orphans = []
    for m in mats:
        s = source_of(m)
        if s is None:
            orphans.append(m)
        by_source[s or "<MISSING>"] = by_source.get(s or "<MISSING>", 0) + 1

    info = {
        "total_triples":       len(g),
        "scaffolding_triples": len(scaffold),
        "material_triples":    len(g) - len(scaffold),
        "materials":           len(mats),
        "by_source":           dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "materials_without_source": len(orphans),
    }

    if verbose:
        print(f"Total triples        : {info['total_triples']:,}")
        print(f"  ontology scaffolding: {info['scaffolding_triples']:,}")
        print(f"  material data       : {info['material_triples']:,}")
        print(f"Materials            : {info['materials']:,}")
        print("\nBy source:")
        for src, cnt in info["by_source"].items():
            print(f"  {src:<24} {cnt:>7,}")
        if orphans:
            print(f"\n  WARNING: {len(orphans)} material(s) have no source_id "
                  f"— they predate the mandatory-provenance change.")
    return info


def materials_by_source(source_id: str | None = None,
                        exclude: str | list[str] | None = None) -> pd.DataFrame:
    """
    Materials filtered by provenance.

    source_id : return only materials from this source
    exclude   : return everything EXCEPT these source(s)

    Example — everything that did NOT come from the base dataset:
        materials_by_source(exclude="CSV_base")
    """
    if isinstance(exclude, str):
        exclude = [exclude]

    props = [(EX.hasFormula,         "formula"),
             (EX.hasExternalId,      "material_id"),
             (EX.hasBandGap,         "bandgap"),
             (EX.hasCrystalSystem,   "crystal_system"),
             (EX.hasCentrosymmetric, "centro"),
             (EX.hasSourceId,        "source_id"),
             (EX.ingestTime,         "ingest_time"),
             (EX.ingestIndex,        "ingest_idx")]

    rows = []
    for m in material_subjects():
        src = source_of(m)
        if source_id is not None and src != source_id:
            continue
        if exclude and src in exclude:
            continue
        rec = {"iri": str(m)}
        for p, name in props:
            vals = [str(o) for o in g.objects(m, p)]
            rec[name] = vals[0] if len(vals) == 1 else (vals or None)
        rows.append(rec)

    df = pd.DataFrame(rows)
    if not df.empty and "ingest_idx" in df.columns:
        df = df.sort_values("ingest_idx", key=lambda s: pd.to_numeric(s, errors="coerce"))
    return df.reset_index(drop=True)


def non_base_materials() -> pd.DataFrame:
    """Everything added after the base CSV build — demos, URLs, external CSVs, raw text."""
    return materials_by_source(exclude=SOURCE_CSV_BASE)


def list_sources() -> pd.DataFrame:
    """One row per distinct source_id with its material count."""
    info = kg_summary(verbose=False)
    return (pd.DataFrame(list(info["by_source"].items()),
                         columns=["source_id", "materials"])
              .sort_values("materials", ascending=False)
              .reset_index(drop=True))


# ─── Maintenance ────────────────────────────────────────────────────────────────

def purge_source(source_id: str, dry_run: bool = True) -> dict:
    """
    Remove every material originating from `source_id`, and all of its triples.

    Ontology scaffolding is never touched — only subjects typed as ex:Material.
    Defaults to dry_run=True; pass dry_run=False to actually delete.

    Typical use: undo a demo or a bad import without rebuilding the whole KG.
        purge_source("fabricated_demo")                 # preview
        purge_source("fabricated_demo", dry_run=False)  # commit
    """
    if source_id == SOURCE_CSV_BASE:
        raise ValueError(
            "Refusing to purge CSV_base — that is the entire base dataset. "
            "Rebuild with `python -m kg.SC_KG` instead if that is what you want."
        )

    targets = [m for m in material_subjects() if source_of(m) == source_id]
    triple_count = 0
    for m in targets:
        trips = list(g.triples((m, None, None)))
        triple_count += len(trips)
        if not dry_run:
            for t in trips:
                g.remove(t)

    verb = "Would remove" if dry_run else "Removed"
    print(f"{verb} {len(targets)} material(s) / {triple_count} triple(s) "
          f"from source '{source_id}'. Triples now: {len(g)}")
    if not dry_run and targets:
        print("NOTE: in-memory dedupe caches (MAT_BY_FORMULA / MAT_BY_ID) are now "
              "stale — restart the session before further ingestion.")
    return {"materials": len(targets), "triples": triple_count, "dry_run": dry_run}


def find_duplicates() -> pd.DataFrame:
    """Materials carrying more than one source_id — i.e. asserted by several sources."""
    rows = []
    for m in material_subjects():
        srcs = [str(o) for o in g.objects(m, hasSourceId)]
        if len(srcs) > 1:
            f = g.value(m, hasFormula)
            rows.append({"iri": str(m), "formula": str(f) if f else None,
                         "sources": srcs, "n_sources": len(srcs)})
    return pd.DataFrame(rows)
