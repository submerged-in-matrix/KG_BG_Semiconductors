"""
utils/kg_remediate.py — Acts on a kg_validate.py report.

Reads maintenance/validation_report.csv and, per row's classification:

  recoverable   -> patch the single bad triple in-place using the
                   ground-truth CSV value
  unrecoverable -> purge the whole material (all its triples) — no
                   ground truth exists to recover it
  unverifiable  -> no automatic action; printed for manual review only

Safe by default: `python -m utils.kg_remediate` is a dry run — it loads
the graph, previews what WOULD change, and writes nothing. Pass --commit
to actually modify the graph and persist it:

    python -m utils.kg_remediate           # preview only
    python -m utils.kg_remediate --commit  # patch + purge + save to disk

On --commit, the current mse_kg_full.ttl is backed up to .ttl.bak BEFORE
any write, and a remediation log is saved to maintenance/ for audit trail.

CHANGED (this version): the report read goes through
utils.csv_io.read_csv_safe. The report's csv_value column can legitimately
contain the string 'NaN' (sodium nitride) — plain pd.read_csv would turn
that ground-truth value into a missing value and this script would then
write an empty formula into the graph while logging it as a successful
patch. See utils/csv_io.py.
"""

import shutil
import sys
from pathlib import Path

import pandas as pd
from rdflib import Literal, URIRef, XSD

from ontology.core import g, EX
from utils.csv_io import read_csv_safe

ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_DIR = ROOT / "maintenance"
# kg_validate.py now writes a fresh, dated file every run and updates this
# copy to point at the newest one — see kg_validate.py's _update_latest_pointer.
DEFAULT_REPORT = MAINTENANCE_DIR / "validation_report_latest.csv"
DEFAULT_LOG = MAINTENANCE_DIR / "remediation_log.csv"
TTL_PATH = ROOT / "data" / "mse_kg_full.ttl"

FIELD_PROP = {
    "formula":        EX.hasFormula,
    "bandgap":        EX.hasBandGap,
    "crystal_system": EX.hasCrystalSystem,
    "centro":         EX.hasCentrosymmetric,
}

FIELD_DATATYPE = {
    "formula":        XSD.string,
    "bandgap":        XSD.float,
    "crystal_system": XSD.string,
    "centro":         XSD.boolean,
}


def load_report(path: Path = DEFAULT_REPORT) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"No report at {path}. Run `python -m utils.kg_validate` first."
        )
    return read_csv_safe(path)


def _check_report_matches_graph(report: pd.DataFrame, sample_size: int = 50):
    """
    Guard against acting on a stale report. If the report's IRIs aren't
    present in the currently-loaded graph at all, every remove/purge in
    remediate() silently matches zero triples while every patch's add()
    still fires -- net effect: orphan nodes get created, nothing gets
    fixed, and it looks like success. This happened once already: an
    identity-key rebuild changed every IRI shape, a stale pre-rebuild
    report was still on disk under the old fixed filename, and
    --commit ran against it without any signal that anything was wrong.

    Checks a sample rather than every row -- this only needs to catch
    "the whole report is stale", not validate row-by-row.
    """
    if report.empty:
        return
    sample = report["iri"].dropna().unique()[:sample_size]
    if len(sample) == 0:
        return
    present = sum(1 for iri in sample if (URIRef(iri), None, None) in g)
    if present == 0:
        raise RuntimeError(
            f"None of {len(sample)} sampled IRIs from the report exist in "
            f"the currently loaded graph ({len(g):,} triples). This report "
            f"is almost certainly stale (produced against a different "
            f"version of the graph, e.g. before an identity-key rebuild). "
            f"Re-run `python -m utils.kg_validate` against the CURRENT "
            f"graph before remediating. Refusing to proceed."
        )


def remediate(report: pd.DataFrame = None, dry_run: bool = True) -> dict:
    if report is None:
        report = load_report()

    required_cols = {"iri", "field", "classification", "csv_value"}
    missing = required_cols - set(report.columns)
    if missing:
        raise ValueError(f"Report is missing expected column(s): {missing}")

    _check_report_matches_graph(report)

    counts = {"patched": 0, "purged_materials": 0, "manual_review": 0}
    purged_iris = set()
    log_rows = []

    # --- unrecoverable: purge whole material, once per iri ---
    unrecoverable_iris = report.loc[
        report["classification"] == "unrecoverable", "iri"
    ].unique()

    for iri in unrecoverable_iris:
        m = URIRef(iri)
        trips = list(g.triples((m, None, None)))
        counts["purged_materials"] += 1
        purged_iris.add(iri)
        log_rows.append({"action": "purge", "iri": iri, "field": None,
                         "old_value": None, "new_value": None,
                         "n_triples_removed": len(trips)})
        if not dry_run:
            for t in trips:
                g.remove(t)

    # --- recoverable: patch single field from ground-truth CSV value ---
    recoverable = report[report["classification"] == "recoverable"]
    for _, row in recoverable.iterrows():
        if row["iri"] in purged_iris:
            continue  # already gone via a different unrecoverable field
        # Guard: a blank csv_value here means the ground truth didn't
        # survive the report round-trip. Refuse to write it.
        if pd.isna(row["csv_value"]) or str(row["csv_value"]).strip() == "":
            print(f"  SKIP {row['iri']} field={row['field']}: "
                  f"csv_value is empty in the report — refusing to patch")
            continue
        m = URIRef(row["iri"])
        prop = FIELD_PROP[row["field"]]
        dtype = FIELD_DATATYPE[row["field"]]
        counts["patched"] += 1
        old_vals = [str(o) for o in g.objects(m, prop)]
        log_rows.append({"action": "patch", "iri": row["iri"], "field": row["field"],
                         "old_value": old_vals[0] if old_vals else None,
                         "new_value": row["csv_value"], "n_triples_removed": None})
        if not dry_run:
            for old in list(g.triples((m, prop, None))):
                g.remove(old)
            g.add((m, prop, Literal(row["csv_value"], datatype=dtype)))

    # --- unverifiable: report only, never auto-touched ---
    unverifiable = report[report["classification"] == "unverifiable"]
    counts["manual_review"] = unverifiable["iri"].nunique() if not unverifiable.empty else 0

    verb = "Would" if dry_run else "Did"
    print(f"{verb} patch {counts['patched']} field(s).")
    print(f"{verb} purge {counts['purged_materials']} material(s) (unrecoverable).")
    print(f"{counts['manual_review']} material(s) need manual review "
          f"(unverifiable — external_id not found in ground-truth CSV). Not touched.")

    if log_rows:
        MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(log_rows).to_csv(DEFAULT_LOG, index=False)
        print(f"Remediation log saved: {DEFAULT_LOG}")

    if not dry_run and (counts["patched"] or counts["purged_materials"]):
        print("NOTE: in-memory dedupe caches (MAT_BY_FORMULA / MAT_BY_ID) are now "
              "stale — restart the session before further ingestion.")

    return counts


def persist(ttl_path: Path = TTL_PATH):
    """Backs up the current .ttl, then serializes the in-memory graph over it."""
    if ttl_path.exists():
        backup = ttl_path.with_suffix(ttl_path.suffix + ".bak")
        print(f"Backing up {ttl_path} -> {backup}")
        shutil.copy2(ttl_path, backup)
    print(f"Serializing graph ({len(g):,} triples) to {ttl_path} ...")
    g.serialize(destination=str(ttl_path), format="turtle")
    print("Saved.")


if __name__ == "__main__":
    commit = "--commit" in sys.argv

    if len(g) < 1000:
        print(f"Loading full graph from {TTL_PATH} ...")
        g.parse(TTL_PATH, format="turtle")
        print(f"Loaded. Triples: {len(g):,}")

    counts = remediate(dry_run=not commit)

    if commit:
        if counts["patched"] or counts["purged_materials"]:
            persist()
        else:
            print("Nothing changed — skipping serialization.")
    else:
        print("\nDry run only — nothing written. Re-run with --commit to apply.")