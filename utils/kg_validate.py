"""
utils/kg_validate.py — Semantic validation of KG literal values.

Structural audit (kg_audit.py) answers "who asserted this triple?".
This answers "is the value itself valid?" — e.g. hasFormula containing
a date string like "2026-02-02 00:00:00" instead of a composition.

Ground truth differs by field:
  - formula: full_dataset_Bandgap_0_to_5.csv (the intermediate CSV,
    predates the .xlsx corruption — confirmed via trace_corruption.py
    that this file still holds clean formula strings for entries where
    the .xlsx cell was silently retyped to a datetime by Excel)
  - bandgap / crystal_system / centro: the matminer-featurized CSV,
    since those columns don't exist upstream of featurization

Classification per failure:
  recoverable   - ground-truth CSV has a valid value -> can be patched
  unrecoverable - ground-truth CSV value is also invalid/missing ->
                  no source to recover from, material should be purged
  unverifiable  - external_id not found in the relevant CSV at all ->
                  provenance inconsistency, needs manual review, never
                  auto-deleted

CHANGED (this version):
  1. CSV reads now go through utils.csv_io.read_csv_safe. Plain
     pd.read_csv turns the literal formula 'NaN' (sodium nitride) into a
     missing value, which made this validator report 3 real materials as
     unrecoverable gaps. See utils/csv_io.py for the full explanation.
  2. Note on identity: with material_id as the KG's identity key, every
     material node carries exactly one hasExternalId, so 'unverifiable'
     now means a genuinely unmatched id rather than an artifact of
     several source rows having been merged onto one node. Expect the
     failure counts here to CHANGE after the identity-key rebuild —
     defects that were previously hidden behind merged nodes become
     individually visible. That is the validator working, not a
     regression.

Read-only: this script never writes to `g`. It only reads the graph and
the CSVs, and writes a report to maintenance/. Remediation is a separate
step (kg_remediate.py).
"""

import re
from pathlib import Path

import pandas as pd

from ontology.core import g, EX
from utils.kg_audit import material_subjects, source_of
from utils.csv_io import read_csv_safe

ROOT = Path(__file__).resolve().parent.parent

# Two distinct ground-truth sources — do not merge these.
BASE_CSV   = ROOT / "data" / "full_dataset_Bandgap_0_to_5.csv"            # clean formula
MASTER_CSV = ROOT / "data" / "full_dataset_Bandgap_0_to_5_featurized.csv"  # bandgap/crystal_system/centro

MAINTENANCE_DIR = ROOT / "maintenance"
DEFAULT_REPORT = MAINTENANCE_DIR / "validation_report.csv"

hasExternalId      = EX.hasExternalId
hasFormula         = EX.hasFormula
hasBandGap         = EX.hasBandGap
hasCrystalSystem   = EX.hasCrystalSystem
hasCentrosymmetric = EX.hasCentrosymmetric

# Composition strings look like element-symbol + optional count, repeated:
# Ga2O3N5Cl7, optionally with ONE level of parenthesized polyatomic groups
# and a trailing multiplier: MgCr2(SiO4)3, K3Ba2Pr2(BiO5)3. Dates, empty
# strings, and anything with '-', ':', whitespace still fail this.
# Note: 'NaN' (sodium nitride) correctly matches as Na + N.
_ELEMENT = r"[A-Z][a-z]?\d*"
_GROUP = r"\((?:[A-Z][a-z]?\d*)+\)\d*"
FORMULA_RE = re.compile(rf"^(?:{_ELEMENT}|{_GROUP})+$")

CRYSTAL_SYSTEMS = {"cubic", "tetragonal", "orthorhombic", "hexagonal",
                   "trigonal", "monoclinic", "triclinic"}


# ─── Field validators — each returns (is_valid, reason_if_not) ─────────────

def validate_formula(v):
    if v is None or not FORMULA_RE.match(str(v).strip()):
        return False, f"not a valid composition string: {v!r}"
    return True, None


def validate_bandgap(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False, f"not numeric: {v!r}"
    if f < 0:
        return False, f"negative bandgap: {f}"
    return True, None


def validate_crystal_system(v):
    if v is None or str(v).strip().lower() not in CRYSTAL_SYSTEMS:
        return False, f"not a recognized crystal system: {v!r}"
    return True, None


def validate_centro(v):
    if v is None or str(v).strip().lower() not in ("true", "false"):
        return False, f"not boolean true/false: {v!r}"
    return True, None


# (RDF property, report field name, validator fn)
VALIDATORS = [
    (hasFormula,         "formula",        validate_formula),
    (hasBandGap,         "bandgap",        validate_bandgap),
    (hasCrystalSystem,   "crystal_system", validate_crystal_system),
    (hasCentrosymmetric, "centro",         validate_centro),
]

VALIDATOR_BY_FIELD = {name: fn for _, name, fn in VALIDATORS}

# field -> (which CSV to cross-check against, column name in that CSV)
CSV_SOURCE_FOR_FIELD = {
    "formula":        ("base",   "formula"),
    "bandgap":        ("master", "band_gap"),
    "crystal_system": ("master", "crystal_system"),
    "centro":         ("master", "is_centrosymmetric"),
}


# ─── Graph-side scan ─────────────────────────────────────────────────────

def scan_invalid(materials=None) -> pd.DataFrame:
    """One row per (material, field) validation failure."""
    if materials is None:
        materials = material_subjects()

    rows = []
    for m in materials:
        ext_id = g.value(m, hasExternalId)
        for prop, name, validator in VALIDATORS:
            vals = list(g.objects(m, prop))
            v = str(vals[0]) if vals else None
            ok, reason = validator(v)
            if not ok:
                rows.append({
                    "iri":         str(m),
                    "external_id": str(ext_id) if ext_id is not None else None,
                    "field":       name,
                    "graph_value": v,
                    "reason":      reason,
                    "source_id":   source_of(m),
                    "n_values":    len(vals),   # >1 signals a merge artifact
                })
    return pd.DataFrame(rows)


# ─── CSV cross-check ─────────────────────────────────────────────────────

def load_base_csv(path: Path = BASE_CSV) -> pd.DataFrame:
    return read_csv_safe(path, dtype={"material_id": str, "formula": str})


def load_master_csv(path: Path = MASTER_CSV) -> pd.DataFrame:
    return read_csv_safe(path, dtype={"material_id": str, "formula": str})


def classify(invalid_df: pd.DataFrame,
             base_df: pd.DataFrame = None,
             master_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    For each invalid (material, field), look up the correct ground-truth
    CSV by material_id (formula -> base CSV, everything else -> featurized
    CSV) and decide recoverable / unrecoverable / unverifiable.
    """
    if invalid_df.empty:
        return invalid_df.assign(csv_value=[], classification=[])

    if base_df is None:
        base_df = load_base_csv()
    if master_df is None:
        master_df = load_master_csv()

    indexed = {
        "base":   base_df.set_index("material_id"),
        "master": master_df.set_index("material_id"),
    }

    classifications, csv_values = [], []
    for _, row in invalid_df.iterrows():
        ext_id = row["external_id"]
        field = row["field"]
        which, csv_col = CSV_SOURCE_FOR_FIELD[field]
        csv_by_id = indexed[which]

        if ext_id is None or ext_id not in csv_by_id.index:
            classifications.append("unverifiable")
            csv_values.append(None)
            continue

        csv_val = csv_by_id.loc[ext_id, csv_col]
        if isinstance(csv_val, pd.Series):  # duplicate material_id rows
            csv_val = csv_val.iloc[0]

        ok, _ = VALIDATOR_BY_FIELD[field](csv_val)
        classifications.append("recoverable" if ok else "unrecoverable")
        csv_values.append(csv_val)

    out = invalid_df.copy()
    out["csv_value"] = csv_values
    out["classification"] = classifications
    return out


# ─── Entry point ─────────────────────────────────────────────────────────

def _dated_report_path() -> Path:
    """
    maintenance/validation_report_YYYYMMDD_HHMMSS.csv — every run writes
    its own file. This is a maintenance script whose consumer
    (kg_remediate.py) acts destructively on whatever report it's pointed
    at; a stale, silently-unwritten file on disk was previously
    indistinguishable from a fresh 'nothing to report' run (see: the
    remediate run that acted on yesterday's report against dead
    formula-slug IRIs after the material_id rebuild). A dated filename
    makes "which run produced this" unambiguous without relying on
    remembering to check a timestamp.
    """
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return MAINTENANCE_DIR / f"validation_report_{stamp}.csv"


def run(report_path: Path = None) -> pd.DataFrame:
    invalid = scan_invalid()
    n_materials = invalid["iri"].nunique() if not invalid.empty else 0
    print(f"Found {len(invalid)} field-level failure(s) across {n_materials} material(s).")

    if report_path is None:
        report_path = _dated_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if invalid.empty:
        print("Graph is semantically clean — nothing to report.")
        # Write anyway: an empty, dated file is proof this run happened
        # and found nothing, rather than a report simply not existing
        # (which is indistinguishable from "this script was never run").
        pd.DataFrame(columns=["iri", "external_id", "field", "graph_value",
                              "reason", "source_id", "csv_value",
                              "classification"]).to_csv(report_path, index=False)
        print(f"Empty report saved (proof of a clean run): {report_path}")
        _update_latest_pointer(report_path)
        return invalid

    classified = classify(invalid)
    print("\nBy classification:")
    print(classified["classification"].value_counts().to_string())

    classified.to_csv(report_path, index=False)
    print(f"\nReport saved: {report_path}")
    print("Nothing in the graph was modified — run kg_remediate.py to act on this report.")
    _update_latest_pointer(report_path)
    return classified


def _update_latest_pointer(report_path: Path):
    """
    validation_report_latest.csv — a copy of (not a symlink to) the most
    recent dated report, so kg_remediate.py's default path keeps working
    without the caller having to know today's timestamp. Always a copy:
    a stale copy left behind by a crashed run is easy to spot (its
    contents won't match the newest dated file); a broken symlink is not.
    """
    import shutil
    latest = MAINTENANCE_DIR / "validation_report_latest.csv"
    shutil.copy2(report_path, latest)
    print(f"Latest pointer updated: {latest}")


if __name__ == "__main__":
    # Standalone run: kg_audit.py stays a pure library (imported from a demo
    # that loads the graph), but this script is meant to be invoked directly
    # and repeatedly during cleanup — so it loads the full KG itself if it
    # isn't already populated.
    if len(g) < 1000:
        ttl_path = ROOT / "data" / "mse_kg_full.ttl"
        print(f"Loading full graph from {ttl_path} ...")
        g.parse(ttl_path, format="turtle")
        print(f"Loaded. Triples: {len(g):,}")

    run()