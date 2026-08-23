"""
trace_corruption.py — Trace exactly which file first shows the corrupted
formula for a set of material_ids, across the three stages of the pipeline:

  1. full_dataset_Bandgap_0_to_5.xlsx              (original Excel file)
  2. full_dataset_Bandgap_0_to_5.csv                (CSV conversion of #1)
  3. full_dataset_Bandgap_0_to_5_featurized.csv     (matminer-featurized output)

For each material_id, prints the formula value AND its pandas dtype at each
stage. If a stage already shows a Timestamp/date-like dtype (not just a
date-shaped string), the corruption happened at or before that file was
written — i.e. Excel converted the cell type itself, not just the display.

Adjust DATA_DIR / filenames below if yours differ.
"""

from pathlib import Path
import pandas as pd

DATA_DIR = Path("data")  # run from project root

FILES = {
    "1_original_xlsx":    DATA_DIR / "full_dataset_Bandgap_0_to_5.xlsx",
    "2_csv_conversion":   DATA_DIR / "full_dataset_Bandgap_0_to_5.csv",
    "3_featurized_csv":   DATA_DIR / "full_dataset_Bandgap_0_to_5_featurized.csv",
}

TARGET_IDS = ["mp-23232", "mp-1079437", "mp-1009221", "mp-1101938"]

ID_COL = "material_id"
FORMULA_COL = "formula"


def load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        # dtype=str forces pandas to stringify whatever it read, but if Excel
        # already stored the cell as a real date type, you'll still see a
        # date string here -- that's the tell that corruption happened in
        # Excel itself, not in this read step.
        return pd.read_excel(path, dtype={ID_COL: str})
    return pd.read_csv(path, dtype={ID_COL: str})


for label, path in FILES.items():
    print("=" * 70)
    print(label, "->", path)
    print("=" * 70)

    if not path.exists():
        print(f"  FILE NOT FOUND at {path} -- update the path in this script.")
        continue

    df = load(path)

    if ID_COL not in df.columns:
        print(f"  Column '{ID_COL}' not found. Available columns: {list(df.columns)}")
        continue
    if FORMULA_COL not in df.columns:
        print(f"  Column '{FORMULA_COL}' not found. Available columns: {list(df.columns)}")
        continue

    sub = df[df[ID_COL].isin(TARGET_IDS)]
    if sub.empty:
        print("  None of the target material_ids found in this file.")
        continue

    for _, row in sub.iterrows():
        val = row[FORMULA_COL]
        print(f"  {row[ID_COL]:<14} value={val!r:<30} dtype={type(val).__name__}")

    print()
