"""
trace_corruption.py — Find WHERE bad formula values enter the pipeline.

Replaces the hardcoded-TARGET_IDS version. Nothing is hardcoded: the script
scans all three files, unions every material_id whose formula looks invalid
in ANY of them, then reports that id's value and dtype at each stage.

Pipeline stages:
  1. full_dataset_Bandgap_0_to_5.xlsx            (original Excel)
  2. full_dataset_Bandgap_0_to_5.csv             (CSV conversion of #1)
  3. full_dataset_Bandgap_0_to_5_featurized.csv  (matminer output, KG source)

The dtype column is the real evidence. A pandas Timestamp in the .xlsx means
Excel retyped the cell itself -- corruption at rest, not a display artifact.
A clean str in an earlier stage means that stage predates the corruption and
can serve as ground truth.

Read-only. Pure pandas -- no matminer/pymatgen import.

Run from repo root:
    python maintenance\\trace_corruption.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

FILES = {
    "1_original_xlsx":  DATA_DIR / "full_dataset_Bandgap_0_to_5.xlsx",
    "2_csv_conversion": DATA_DIR / "full_dataset_Bandgap_0_to_5.csv",
    "3_featurized_csv": DATA_DIR / "full_dataset_Bandgap_0_to_5_featurized.csv",
}

ID_COL = "material_id"
FORMULA_COL = "formula"

# A composition string is element symbols + optional counts, optionally with
# parenthesised groups: Ga2O3N5Cl7, MgCr2(SiO4)3. Anything else is suspect.
VALID_FORMULA = r"^(?:[A-Z][a-z]?\d*|\((?:[A-Z][a-z]?\d*)+\)\d*)+$"


def load(path: Path) -> pd.DataFrame:
    """Load without coercing formula, so native dtypes stay visible."""
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, dtype={ID_COL: str})
    return pd.read_csv(path, dtype={ID_COL: str}, low_memory=False)


def suspect_mask(series: pd.Series) -> pd.Series:
    """True where the formula is missing or not a plausible composition."""
    as_str = series.astype(str)
    is_null = series.isna()
    # non-string objects (Timestamp, datetime) are inherently suspect
    not_str = series.map(lambda v: not isinstance(v, str)) & ~is_null
    bad_shape = ~as_str.str.match(VALID_FORMULA, na=False)
    return is_null | not_str | bad_shape


def main():
    frames, masks = {}, {}

    print("=" * 78)
    print("STAGE SCAN — suspect formulas discovered per file")
    print("=" * 78)
    for label, path in FILES.items():
        if not path.exists():
            print(f"  {label:<20} FILE NOT FOUND at {path}")
            continue
        df = load(path)
        if ID_COL not in df.columns or FORMULA_COL not in df.columns:
            print(f"  {label:<20} missing required column. Has: {list(df.columns)}")
            continue
        m = suspect_mask(df[FORMULA_COL])
        frames[label] = df.set_index(ID_COL)
        masks[label] = set(df.loc[m, ID_COL].dropna())
        dtypes = df.loc[m, FORMULA_COL].map(lambda v: type(v).__name__).value_counts()
        print(f"  {label:<20} rows {len(df):>8,}   suspect {int(m.sum()):>4,}"
              f"   dtypes {dict(dtypes)}")

    if not frames:
        print("\nNo files loaded — check paths.")
        return

    all_ids = sorted(set().union(*masks.values()))
    print(f"\n  UNION of suspect material_ids across all stages: {len(all_ids)}")

    # ── Per-id trace ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("PER-ID TRACE — value and dtype at each stage")
    print("=" * 78)
    verdicts = []
    for mid in all_ids:
        print(f"\n  {mid}")
        stage_vals = {}
        for label, df in frames.items():
            if mid not in df.index:
                print(f"    {label:<20} <id not present in this file>")
                stage_vals[label] = ("<absent>", None)
                continue
            row = df.loc[mid]
            if isinstance(row, pd.DataFrame):   # duplicate id (shouldn't happen)
                row = row.iloc[0]
            val = row[FORMULA_COL]
            ok = not suspect_mask(pd.Series([val])).iloc[0]
            flag = "OK " if ok else "BAD"
            print(f"    {label:<20} {flag}  value={val!r:<26} "
                  f"dtype={type(val).__name__}")
            stage_vals[label] = (val, ok)

        # classify
        clean_stages = [s for s, (_, ok) in stage_vals.items() if ok]
        if clean_stages:
            src = min(clean_stages)
            verdicts.append((mid, "RECOVERABLE", src, stage_vals[src][0]))
        else:
            verdicts.append((mid, "NO CLEAN SOURCE", None, None))

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    rec = [v for v in verdicts if v[1] == "RECOVERABLE"]
    lost = [v for v in verdicts if v[1] != "RECOVERABLE"]
    print(f"  recoverable from an earlier stage : {len(rec)}")
    print(f"  no clean value anywhere           : {len(lost)}")

    if rec:
        print("\n  Recoverable:")
        for mid, _, src, val in rec:
            print(f"    {mid:<14} <- {src:<18} value={val!r}")
    if lost:
        print("\n  No clean source (genuine gaps or corrupted everywhere):")
        for mid, _, _, _ in lost:
            print(f"    {mid}")

    print("\n" + "=" * 78)
    print("  Where a stage shows dtype=Timestamp/datetime, Excel retyped the")
    print("  cell at rest. Where an EARLIER stage is clean, that stage is")
    print("  usable ground truth for the CSV cleanup.")


if __name__ == "__main__":
    main()
