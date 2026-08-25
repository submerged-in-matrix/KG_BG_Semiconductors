"""
verify_null_formula.py — Is the formula really missing, or is it literal
text that pandas' default NA-token list swallowed (e.g. "NaN" = sodium
nitride, Na+N, colliding with pandas' float-NaN sentinel)?

pandas treats a fixed list of strings as missing-by-default regardless of
dtype= -- including exactly "NaN", "NA", "N/A", "null", "None", etc. Every
prior script in this session (find_key.py, trace_corruption.py,
fix_formula_corruption.py) read these files with plain pd.read_csv/
read_excel, so ALL of them share this blind spot, not just the delete
step.

This script re-reads with keep_default_na=False (raw text, nothing
auto-converted) and cross-checks against the `structure` column, which
lists literal element sites and cannot be affected by this pandas
behavior. If the parsed atom composition doesn't match "NaN" (or whatever
the raw formula text says), it's still a genuine gap.

No pymatgen/matminer import (avoids the local env conflict) -- pulls
species directly out of the pretty-printed structure text via regex.

Read-only.

Run from repo root:
    python maintenance\\verify_null_formula.py
"""

import re
from collections import Counter
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
STRUCT_COL = "structure"

re_sites_header = re.compile(r"Sites\s*\(\d+\)", re.I)
re_site_row = re.compile(r"^\s*\d+\s+([A-Za-z][a-z]?)\s+[-\d.Ee+]+\s+[-\d.Ee+]+\s+[-\d.Ee+]+")


def load_raw(path: Path) -> pd.DataFrame:
    """Load with NOTHING auto-converted to NaN. Raw text stays raw text."""
    kwargs = dict(dtype=str, keep_default_na=False, na_values=[])
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, **kwargs)
    return pd.read_csv(path, low_memory=False, **kwargs)


def composition_from_structure(struct_text) -> Counter:
    """Pull element counts directly from the pretty-printed Sites block."""
    if not isinstance(struct_text, str) or not struct_text.strip():
        return Counter()
    lines = struct_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if re_sites_header.search(ln))
    except StopIteration:
        return Counter()
    species = []
    for ln in lines[start + 1:]:
        m = re_site_row.match(ln)
        if m:
            species.append(m.group(1))
    return Counter(species)


def pretty_formula(counts: Counter) -> str:
    return "".join(f"{el}{n if n > 1 else ''}" for el, n in sorted(counts.items()))


def main():
    print("Re-reading all three files RAW (keep_default_na=False) — this")
    print("shows literal cell content, not pandas' guess at missingness.\n")

    frames = {}
    for label, path in FILES.items():
        if not path.exists():
            print(f"  {label}: NOT FOUND")
            continue
        frames[label] = load_raw(path)

    # ── 1. Raw formula text for anything blank OR literally "NaN"-like ──
    print("=" * 78)
    print("1. RAW FORMULA TEXT (bypassing pandas auto-NA)")
    print("=" * 78)
    suspect_ids = set()
    for label, df in frames.items():
        if FORMULA_COL not in df.columns:
            continue
        blank = df[FORMULA_COL].str.strip() == ""
        na_like = df[FORMULA_COL].str.strip().str.lower().isin(
            {"nan", "na", "n/a", "null", "none"})
        flagged = df[blank | na_like]
        print(f"\n  {label}: {len(flagged)} row(s) blank-or-NA-like")
        for _, row in flagged.iterrows():
            mid = row[ID_COL]
            raw = row[FORMULA_COL]
            suspect_ids.add(mid)
            kind = "TRULY BLANK" if raw.strip() == "" else f"LITERAL TEXT {raw!r}"
            print(f"    {mid:<14} raw_formula={raw!r:<10}  -> {kind}")

    if not suspect_ids:
        print("\nNo blank-or-NA-like formulas found anywhere. Nothing to check.")
        return

    # ── 2. Cross-check against structure-derived composition ────────────
    print("\n" + "=" * 78)
    print("2. STRUCTURE-DERIVED COMPOSITION (ground truth, regex on Sites block)")
    print("=" * 78)
    struct_source = next(
        (l for l in FILES if l in frames and STRUCT_COL in frames[l].columns), None)
    if struct_source is None:
        print("  No file has a 'structure' column visible — cannot cross-check.")
        return

    df = frames[struct_source]
    print(f"  using structure column from: {struct_source}\n")
    for mid in sorted(suspect_ids):
        rows = df[df[ID_COL] == mid]
        if rows.empty:
            print(f"  {mid}: not present in {struct_source}")
            continue
        struct_text = rows.iloc[0][STRUCT_COL]
        counts = composition_from_structure(struct_text)
        formula = pretty_formula(counts) if counts else "<could not parse>"
        raw_formula = {label: frames[label][frames[label][ID_COL] == mid][FORMULA_COL].iloc[0]
                       for label in frames if FORMULA_COL in frames[label].columns
                       and mid in frames[label][ID_COL].values}
        print(f"  {mid}")
        print(f"    raw formula per file : {raw_formula}")
        print(f"    site counts           : {dict(counts)}")
        print(f"    derived composition   : {formula}")
        if not counts:
            print(f"    structure text (first 300 chars):")
            print(f"      {str(struct_text)[:300]!r}")
        print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print("  If derived composition is non-empty (e.g. 'N1Na1'), the row has")
    print("  a real material and a real (if awkwardly-notated) formula --")
    print("  it is NOT a gap and must not be dropped.")
    print("  If site counts come back empty and raw formula is truly blank")
    print("  (not the text 'NaN'), it's a genuine gap.")


if __name__ == "__main__":
    main()
