"""
find_key.py — Determine the true unique key of the featurized CSV.

Reads the CSV directly with pandas only. No matminer, no pymatgen, no
env.modules import -- so the local matminer/pandas conflict cannot block it.

The KG's node identity must be a column (or tuple of columns) that is
UNIQUE in this file. If it isn't unique, ingestion collapses rows onto
one node and silently loses whichever values lose the _has_triple race.

Run from repo root:
    python maintenance\\find_key.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "full_dataset_Bandgap_0_to_5_featurized.csv"

# Column names as they exist in the featurized CSV (pre-rename).
ID = "material_id"
FORMULA = "formula"
BANDGAP = "band_gap"
CRYSTAL = "crystal_system"
CENTRO = "is_centrosymmetric"


def report_key(df, cols, label):
    """How unique is this candidate key?"""
    present = [c for c in cols if c in df.columns]
    if len(present) != len(cols):
        print(f"\n  {label}: SKIPPED (missing {set(cols)-set(present)})")
        return None

    sub = df[present]
    n_rows = len(sub)
    n_null = sub.isna().any(axis=1).sum()
    n_unique = len(sub.drop_duplicates())
    dupe_rows = n_rows - n_unique

    verdict = "UNIQUE" if dupe_rows == 0 else f"NOT unique ({dupe_rows:,} surplus rows)"
    print(f"\n  {label}")
    print(f"    columns      : {present}")
    print(f"    rows         : {n_rows:,}")
    print(f"    distinct     : {n_unique:,}")
    print(f"    rows w/ null : {n_null:,}")
    print(f"    verdict      : {verdict}")

    if dupe_rows:
        counts = sub.groupby(present, dropna=False).size().sort_values(ascending=False)
        multi = counts[counts > 1]
        print(f"    keys occurring >1x: {len(multi):,}  "
              f"(max repeat {int(counts.max()):,})")
        print(f"    worst examples:")
        for key, c in multi.head(5).items():
            key = key if isinstance(key, tuple) else (key,)
            pretty = ", ".join(f"{k}={v!r}" for k, v in zip(present, key))
            print(f"      {c:>5,}x  {pretty}")
    return dupe_rows


def main():
    if not CSV.exists():
        print(f"NOT FOUND: {CSV}")
        return

    df = pd.read_csv(CSV, dtype={ID: str, FORMULA: str, CRYSTAL: str},
                     low_memory=False)

    print("=" * 74)
    print("0. FILE SHAPE")
    print("=" * 74)
    print(f"  path : {CSV}")
    print(f"  rows : {len(df):,}   <- compare to graph's 150,984 hasExternalId")
    print(f"  cols : {list(df.columns)}")

    print("\n" + "=" * 74)
    print("1. PER-COLUMN NULLS / DISTINCTNESS")
    print("=" * 74)
    for c in [ID, FORMULA, BANDGAP, CRYSTAL, CENTRO]:
        if c not in df.columns:
            print(f"  {c:<20} MISSING FROM FILE")
            continue
        print(f"  {c:<20} nulls {df[c].isna().sum():>7,}   "
              f"distinct {df[c].nunique(dropna=True):>8,}")

    print("\n" + "=" * 74)
    print("2. CANDIDATE KEYS")
    print("=" * 74)
    report_key(df, [ID], "A. material_id alone")
    report_key(df, [FORMULA], "B. formula alone (CURRENT KG IDENTITY)")
    report_key(df, [FORMULA, CRYSTAL], "C. formula + crystal_system")
    report_key(df, [FORMULA, CRYSTAL, CENTRO],
               "D. formula + crystal_system + is_centrosymmetric")
    report_key(df, [FORMULA, CRYSTAL, CENTRO, BANDGAP],
               "E. formula + crystal_system + centro + band_gap")

    print("\n" + "=" * 74)
    print("3. IF material_id IS UNIQUE: WHAT DOES FORMULA-KEYING COST?")
    print("=" * 74)
    if ID in df.columns and FORMULA in df.columns:
        per_formula = df.groupby(FORMULA, dropna=True).agg(
            n_rows=(ID, "size"),
            n_crystal=(CRYSTAL, "nunique"),
            n_bandgap=(BANDGAP, "nunique"),
        )
        merged = per_formula[per_formula["n_rows"] > 1]
        print(f"  formulas with >1 row        : {len(merged):,}")
        print(f"  rows absorbed by merging    : "
              f"{int(merged['n_rows'].sum() - len(merged)):,}")
        print(f"  ...with >1 crystal_system   : {int((merged['n_crystal'] > 1).sum()):,}"
              f"   <- TRUE POLYMORPHS, structure silently dropped")
        print(f"  ...with 1 crystal_system    : {int((merged['n_crystal'] <= 1).sum()):,}"
              f"   <- same structure, differing band gaps")

    print("\n" + "=" * 74)
    print("4. FORMULA SANITY (are there more corrupted values than the known 3?)")
    print("=" * 74)
    if FORMULA in df.columns:
        f = df[FORMULA]
        date_like = f.str.contains(r"\d{4}-\d{2}-\d{2}", na=False)
        has_space = f.str.contains(r"\s", na=False)
        has_colon = f.str.contains(r":", na=False)
        print(f"  null formula        : {f.isna().sum():,}")
        print(f"  date-like strings   : {int(date_like.sum()):,}")
        print(f"  containing space    : {int(has_space.sum()):,}")
        print(f"  containing colon    : {int(has_colon.sum()):,}")
        bad = df[date_like | has_space | has_colon | f.isna()]
        if not bad.empty:
            print(f"\n  All suspect rows ({len(bad)}):")
            cols = [c for c in [ID, FORMULA, CRYSTAL, BANDGAP] if c in df.columns]
            print("   " + bad[cols].head(20).to_string(index=False).replace("\n", "\n   "))

    print("\n" + "=" * 74)
    print("DECISION RULE")
    print("=" * 74)
    print("  Pick the SMALLEST candidate key that reports UNIQUE with 0 nulls.")
    print("  That becomes the KG's node identity in _mint_material_iri.")
    print("  If material_id is unique -> use it; formula becomes a plain property.")


if __name__ == "__main__":
    main()
