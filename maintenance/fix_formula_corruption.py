"""
fix_formula_corruption.py — v2. Fixes the bug verify_null_formula.py found
in v1 of this same script: plain pd.read_csv() treats the literal text
"NaN" as a missing value regardless of dtype=, so the old repair map
built from the clean CSV silently turned the ground truth into a second
NaN instead of reading it as text. All three "unrecoverable" ids
(mp-1009221, mp-1080032, mp-1179882) were real sodium nitride (Na:N 1:1,
confirmed against the structure column) — they were never gaps.

Ground truth stays full_dataset_Bandgap_0_to_5.csv (the CLEAN parent CSV,
per your instruction — same file already confirmed clean for the 5
date-corrupted ids). Both reads now use keep_default_na=False, na_values=[]
so "NaN" is read as the 3-character string it is, not converted to float
NaN and then converted back.

Patches:
  - full_dataset_Bandgap_0_to_5_featurized.csv   (3 blank cells -> 'NaN')
  - full_dataset_Bandgap_0_to_5.xlsx             (already correct — xlsx
    already reads 'NaN' as text per the raw scan; included only to verify,
    no write expected)

Safe by default:
    python maintenance\\fix_formula_corruption.py            # dry run
    python maintenance\\fix_formula_corruption.py --commit   # writes (after .bak)
"""

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

XLSX = DATA_DIR / "full_dataset_Bandgap_0_to_5.xlsx"
CLEAN_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5.csv"
FEAT_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5_featurized.csv"

ID_COL = "material_id"
FORMULA_COL = "formula"

# Read kwargs shared everywhere in this script. This is the actual fix:
# nothing gets auto-converted to NaN, "NaN" stays the 3-char string "NaN".
RAW = dict(keep_default_na=False, na_values=[])


def is_blank(series: pd.Series) -> pd.Series:
    """True only for genuinely empty cells, now that 'NaN' text is safe."""
    return series.astype(str).str.strip() == ""


def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak")
    print(f"    backup {path.name} -> {bak.name}")
    shutil.copy2(path, bak)


def main():
    commit = "--commit" in sys.argv
    mode = "COMMIT" if commit else "DRY RUN"
    print(f"=== fix_formula_corruption.py v2 [{mode}] ===\n")
    print("Reading with keep_default_na=False — 'NaN' text stays text.\n")

    for p in (XLSX, CLEAN_CSV, FEAT_CSV):
        if not p.exists():
            print(f"MISSING: {p}")
            return

    clean = pd.read_csv(CLEAN_CSV, dtype={ID_COL: str, FORMULA_COL: str},
                        low_memory=False, **RAW)
    feat = pd.read_csv(FEAT_CSV, dtype={ID_COL: str, FORMULA_COL: str},
                       low_memory=False, **RAW)

    # sanity: clean CSV should show the 'NaN' text, not blanks
    clean_nan_text = clean[clean[FORMULA_COL] == "NaN"]
    print(f"Sanity check — literal 'NaN' text rows in clean CSV: "
          f"{len(clean_nan_text)}")
    for _, r in clean_nan_text.iterrows():
        print(f"    {r[ID_COL]:<14} formula={r[FORMULA_COL]!r}")

    blank_mask = is_blank(feat[FORMULA_COL])
    bad = feat[blank_mask]
    clean_idx = clean.set_index(ID_COL)[FORMULA_COL]

    repairs, gaps = {}, []
    for mid in bad[ID_COL]:
        truth = clean_idx.get(mid)
        if isinstance(truth, pd.Series):
            truth = truth.iloc[0]
        if truth is not None and str(truth).strip() != "":
            repairs[mid] = str(truth)
        else:
            gaps.append(mid)

    print(f"\nBlank formula rows in featurized CSV : {len(bad)}")
    print(f"  repairable from clean CSV          : {len(repairs)}")
    for mid, val in repairs.items():
        print(f"      {mid:<14} '' -> {val!r}")
    print(f"  genuine gaps (still blank in clean) : {len(gaps)}")
    for mid in gaps:
        print(f"      {mid}")

    if not repairs:
        print("\nNothing to repair.")
        return

    print(f"\n[1/1] featurized CSV")
    mask = feat[ID_COL].isin(repairs)
    print(f"    {int(mask.sum())} row(s) to update")
    if commit:
        backup(FEAT_CSV)
        feat.loc[mask, FORMULA_COL] = feat.loc[mask, ID_COL].map(repairs)
        feat.to_csv(FEAT_CSV, index=False)
        print(f"    written: {FEAT_CSV.name}")

        # verify the write round-trips correctly (re-read raw, check text)
        recheck = pd.read_csv(FEAT_CSV, dtype={ID_COL: str, FORMULA_COL: str},
                              low_memory=False, **RAW)
        still_bad = recheck[recheck[ID_COL].isin(repairs) &
                            (recheck[FORMULA_COL].astype(str).str.strip() != "NaN")]
        if still_bad.empty:
            print("    verified: all repaired cells now read back as 'NaN' text")
        else:
            print(f"    WARNING: {len(still_bad)} repaired cell(s) did not "
                  f"round-trip correctly — inspect manually")

    print(f"\n[xlsx check] {XLSX.name}")
    xl = pd.read_excel(XLSX, dtype={ID_COL: str, FORMULA_COL: str}, **RAW)
    xl_bad = xl[xl[ID_COL].isin(repairs) & is_blank(xl[FORMULA_COL])]
    if xl_bad.empty:
        print("    already correct (xlsx never lost the text) — no write needed")
    else:
        print(f"    {len(xl_bad)} row(s) unexpectedly blank in xlsx — "
              f"investigate before assuming it's fine")

    print("\n" + "=" * 60)
    if commit:
        print("Done. Re-run verify_null_formula.py to confirm 0 blank rows")
        print("remain and derived composition matches 'NaN' (Na:N 1:1) for")
        print("all three ids.")
    else:
        print("Dry run only. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
