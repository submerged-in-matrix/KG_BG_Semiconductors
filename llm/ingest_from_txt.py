"""
ingest_from_txt.py — LLM -> KG ingestion (idempotent, dedupe, mandatory provenance).

CHANGE (this version): identity key is material_id, not formula.

Verified against the source data (find_key.py):
  - material_id: 150,987 rows, 150,987 distinct, 0 nulls -> a true key.
  - formula:     102,416 distinct values over the same 150,987 rows ->
                 NOT unique. ~20,721 formulas cover more than one
                 material_id, including genuine polymorphs (13,397 cases
                 with >1 distinct crystal_system under that formula).

Formula-as-identity was silently merging those onto one graph node. Because
hasCrystalSystem/hasCentrosymmetric were guarded (first value wins) but
hasBandGap was not, merged nodes ended up with ONE crystal system paired
against MANY band gaps that didn't all belong to it — e.g. a node reporting
crystal_system=hexagonal while carrying 140 band gaps, most of which came
from trigonal/other entries sharing that formula. Confirmed via
inspect_graph.py.

material_id is now the primary key. formula becomes a plain property
(still written, still indexed for lookup, just no longer used to decide
whether two rows are "the same material"). formula is retained as a
fallback key ONLY for the rare case where no material_id is available at
all (e.g. hand-typed text with nothing for _fabricate_material_id to use);
that fallback inherits the old non-uniqueness risk and is not a fix for it.

Other retained design from before:
- No rdfs:label on materials (formula/material_id serve that role)
- No BNode provenance node + ex:statedIn + ex:hasProvenanceId
- ex:hasSourceId is a MANDATORY direct literal on every material
- ingestTime / ingestIndex use g.set (single-valued, not accumulating)

NEW: hasBandGap is now guarded the same way hasCrystalSystem/
hasCentrosymmetric already were (first value wins, not accumulated).
Under material_id identity this should rarely trigger — each node maps to
one source row — but it's a safety net against re-ingesting the same id
twice (e.g. re-running the bulk CSV load without a fresh graph).
"""

from collections import defaultdict

from env.modules import *
from ontology.core import *
from ontology.ingest_meta import ingestIndex, ingestTime
from data.mint_entities import _slugify, mint_entity
from utils.llm_schema import RowOut

hasSourceId = EX.hasSourceId

# Canonical provenance values
SOURCE_CSV_BASE     = "CSV_base"        # the featurized CSV the KG was built from
SOURCE_CSV_EXTERNAL = "CSV_external"    # another CSV parsed via parse-lora
SOURCE_ANONYMOUS    = "anonymous_text"  # free text with no identifiable source
# a URL is used verbatim as its own source_id


# ─── Lookup indices for dedupe ─────────────────────────────────────────────────
def _index_materials():
    """
    by_id       : material_id -> node        (1:1, material_id is unique)
    by_formula  : formula -> [node, ...]      (NOT 1:1 -- kept for the
                  no-id fallback path and for formula-based lookups
                  elsewhere; a formula can legitimately map to many nodes)
    """
    by_formula = defaultdict(list)
    by_id = {}
    for m in g.subjects(RDF.type, Material):
        for f in g.objects(m, hasFormula):
            by_formula[str(f)].append(m)
        mid = g.value(m, hasExternalId)
        if mid:
            by_id[str(mid)] = m
    return by_formula, by_id


MAT_BY_FORMULA, MAT_BY_ID = _index_materials()


def add_once(s, p, o):
    if (s, p, o) not in g:
        g.add((s, p, o))


def _mint_material_iri(label: str | None, material_id: str | None,
                       formula: str | None, idx: int):
    """
    Mint an IRI. Priority: material_id (unique) > formula (not unique,
    fallback only) > synthetic Material_<idx> via mint_entity.
    """
    if material_id and str(material_id).strip():
        iri = EX[_slugify(str(material_id))]
        g.add((iri, RDF.type, Material))
        return iri
    if formula and str(formula).strip():
        iri = EX[_slugify(str(formula))]
        g.add((iri, RDF.type, Material))
        return iri
    return mint_entity(label, Material, "Material", idx)


def get_or_create_material(material_id: str | None,
                           formula: str | None,
                           label: str | None,
                           idx: int):
    f = (str(formula).strip() if formula else None)
    mid = (str(material_id).strip() if material_id else None)

    # ── Primary path: material_id is the identity key ──────────────────
    if mid:
        if mid in MAT_BY_ID:
            m = MAT_BY_ID[mid]
        else:
            m = _mint_material_iri(label, mid, f, idx)
            add_once(m, hasExternalId, Literal(mid, datatype=XSD.string))
            MAT_BY_ID[mid] = m
        if f:
            add_once(m, hasFormula, Literal(f, datatype=XSD.string))
            if m not in MAT_BY_FORMULA[f]:
                MAT_BY_FORMULA[f].append(m)
        return m

    # ── Fallback: no material_id at all -> best-effort formula key ─────
    # formula is NOT unique; this branch can still merge distinct
    # materials sharing a formula. Only reached when the caller supplied
    # no id whatsoever (normal ingest paths always have one, either real
    # or fabricated upstream).
    if f:
        existing = MAT_BY_FORMULA.get(f)
        if existing:
            return existing[0]
        m = _mint_material_iri(label, None, f, idx)
        add_once(m, hasFormula, Literal(f, datatype=XSD.string))
        MAT_BY_FORMULA[f].append(m)
        return m

    return _mint_material_iri(label, None, None, idx)


def _resolve_source_id(source_id: str | None) -> str:
    """source_id is mandatory — fall back to the anonymous marker."""
    s = str(source_id).strip() if source_id is not None else ""
    return s if s else SOURCE_ANONYMOUS


def _has_triple(s, p):
    return any(True for _ in g.triples((s, p, None)))


def ingest_normalized_row(nr: RowOut, idx: int = 0,
                          source_id: str | None = None,
                          source_label: str | None = None):   # accepted, ignored
    """
    Ingest one validated row. source_label is accepted for backward compatibility
    but no longer written to the graph.
    """
    m = get_or_create_material(
        material_id=getattr(nr, "material_id", None),
        formula=getattr(nr, "formula", None),
        label=nr.material,
        idx=idx,
    )

    if getattr(nr, "material_id", None):
        add_once(m, hasExternalId, Literal(str(nr.material_id), datatype=XSD.string))

    # NEW: guarded like hasCrystalSystem/hasCentrosymmetric below. Under
    # material_id identity this should almost never fire on the base CSV
    # load (one row per id); it exists as a safety net, not the fix for
    # the old polymorph-collapse issue (that's fixed by the identity
    # change above, not by this guard).
    if nr.band_gap_eV is not None and not _has_triple(m, hasBandGap):
        add_once(m, hasBandGap, Literal(float(nr.band_gap_eV), datatype=XSD.float))

    if getattr(nr, "crystal_system", None) and not _has_triple(m, hasCrystalSystem):
        add_once(m, hasCrystalSystem,
                 Literal(str(nr.crystal_system).strip().lower(), datatype=XSD.string))

    if getattr(nr, "is_centrosymmetric", None) is not None and not _has_triple(m, hasCentrosymmetric):
        add_once(m, hasCentrosymmetric,
                 Literal(bool(nr.is_centrosymmetric), datatype=XSD.boolean))

    # MANDATORY provenance, direct literal (no BNode hop)
    add_once(m, hasSourceId, Literal(_resolve_source_id(source_id), datatype=XSD.string))

    # ingest metadata: single-valued
    g.set((m, ingestTime,  Literal(datetime.now(timezone.utc).isoformat(),
                                   datatype=XSD.dateTime)))
    g.set((m, ingestIndex, Literal(int(idx), datatype=XSD.integer)))

    return m
