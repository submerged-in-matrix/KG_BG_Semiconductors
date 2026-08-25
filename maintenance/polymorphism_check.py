"""
polymorphism_check.py — Are crystal_system + is_centrosymmetric enough to
tell structurally distinct entries apart within one formula?

This script does NOT decide what counts as a "polymorph" (that's a
judgment call for you). It measures one narrower, mechanical thing:

  For formulas with more than one entry in the parent CSV, are those
  entries' structures the same or different? And when they're different,
  do they still get labeled identically by crystal_system + is_centro-
  symmetric -- the only two structural features that made it into the
  KG?

Two things worth being upfront about (this is not a proper crystallographic
match, and grouping is on exact formula text):

  1. "Same formula" means exact string match on the parent CSV's formula
     column. Fe2O4 and FeO2 are NOT grouped together even though they're
     the same stoichiometry at a different cell multiplier -- that's a
     real distinction (different Z / supercell), and merging it would
     hide the thing this script exists to measure.

  2. "Same structure" is judged two ways, reported separately, not
     collapsed into one answer:
       raw_signature        : hash of the structure text as printed,
                               whitespace-normalized only. Extremely
                               strict -- catches EVERY difference,
                               including harmless reformatting.
       canonical_signature   : lattice params rounded to `ROUND_DECIMALS`,
                               sites sorted (species, x, y, z) and
                               rounded, then hashed. Tolerant of site
                               ordering and float noise, still purely
                               geometric.
     Neither is a real structure match (no space-group symmetry check --
     that needs pymatgen's SpacegroupAnalyzer, which this script
     deliberately avoids to sidestep the local matminer/pandas conflict).
     Two structures can be crystallographically identical (same space
     group) yet get flagged "different" here if described in a
     differently-oriented cell. Treat "distinct" counts as an upper
     bound, not ground truth.

Outputs (maintenance/):
  polymorphism_detail.csv   — one row per multi-entry-formula material
  polymorphism_summary.csv  — one row per multi-entry formula

Read-only. Uses read_csv_safe throughout (the 'NaN' = sodium nitride
formula must survive grouping intact, same as everywhere else in this
project).

Run from repo root:
    python maintenance\\polymorphism_check.py
"""

import hashlib
import re
from pathlib import Path

import pandas as pd

from utils.csv_io import read_csv_safe

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MAINTENANCE_DIR = ROOT / "maintenance"

MASTER_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5.csv"       # parent, pre-featurization
FEAT_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5_featurized.csv"

ID_COL = "material_id"
FORMULA_COL = "formula"
STRUCT_COL = "structure"

ROUND_DECIMALS = 3   # tolerance for the canonical signature

_re_abc = re.compile(r"abc\s*:\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)", re.I)
_re_angles = re.compile(r"angles\s*:\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)", re.I)
_re_sites_header = re.compile(r"Sites\s*\(\d+\)", re.I)
_re_site_row = re.compile(
    r"^\s*\d+\s+([A-Za-z][a-z]?)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)")


def parse_structure(text):
    """Pull lattice params + site list out of the pretty-printed block."""
    if not isinstance(text, str) or not text.strip():
        return None
    m_abc = _re_abc.search(text)
    m_ang = _re_angles.search(text)
    if not (m_abc and m_ang):
        return None
    a, b, c = map(float, m_abc.groups())
    alpha, beta, gamma = map(float, m_ang.groups())

    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if _re_sites_header.search(ln))
    except StopIteration:
        return None

    sites = []
    for ln in lines[start + 1:]:
        m = _re_site_row.match(ln)
        if m:
            sp, x, y, z = m.groups()
            sites.append((sp, float(x), float(y), float(z)))
    if not sites:
        return None

    return {"a": a, "b": b, "c": c, "alpha": alpha, "beta": beta,
            "gamma": gamma, "sites": sites, "raw": text}


def raw_signature(text: str) -> str:
    normalized = "\n".join(ln.strip() for ln in text.strip().splitlines() if ln.strip())
    return hashlib.sha1(normalized.encode()).hexdigest()[:16]


def canonical_signature(parsed: dict, decimals: int = ROUND_DECIMALS) -> str:
    lattice = tuple(round(parsed[k], decimals)
                    for k in ("a", "b", "c", "alpha", "beta", "gamma"))
    sites = tuple(sorted(
        (sp, round(x, decimals), round(y, decimals), round(z, decimals))
        for sp, x, y, z in parsed["sites"]
    ))
    payload = repr((lattice, sites))
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def main():
    print("Loading parent CSV...")
    master = read_csv_safe(MASTER_CSV, dtype={ID_COL: str, FORMULA_COL: str},
                           low_memory=False)
    if STRUCT_COL not in master.columns:
        print(f"'{STRUCT_COL}' column not found. Columns: {list(master.columns)}")
        return
    print(f"  {len(master):,} rows, {master[FORMULA_COL].nunique():,} distinct formulas")

    # ── entries-per-composition, full distribution ──────────────────────
    print("\n" + "=" * 74)
    print("1. ENTRIES PER COMPOSITION (parent CSV)")
    print("=" * 74)
    counts = master[FORMULA_COL].value_counts()
    dist = counts.value_counts().sort_index()
    print(f"  distinct formulas       : {len(counts):,}")
    print(f"  formulas with 1 entry   : {int((counts == 1).sum()):,}")
    print(f"  formulas with >1 entry  : {int((counts > 1).sum()):,}")
    print(f"  max entries for one formula : {int(counts.max()):,} "
          f"({counts.idxmax()!r})")
    print("\n  distribution (n_entries -> n_formulas), first 15:")
    for n, cnt in dist.head(15).items():
        print(f"    {n:>4} entries: {cnt:>6,} formula(s)")

    multi = counts[counts > 1].index
    if len(multi) == 0:
        print("\nNo formula has more than one entry — nothing further to check.")
        return

    # ── parse structures for every row in a multi-entry formula ─────────
    print("\n" + "=" * 74)
    print("2. STRUCTURE COMPARISON WITHIN EACH MULTI-ENTRY FORMULA")
    print("=" * 74)
    sub = master[master[FORMULA_COL].isin(multi)].copy()
    print(f"  parsing structures for {len(sub):,} rows across "
          f"{len(multi):,} formulas...")

    parsed = sub[STRUCT_COL].map(parse_structure)
    unparsed = parsed.isna().sum()
    if unparsed:
        print(f"  WARNING: {unparsed} structure(s) could not be parsed "
              f"(unexpected format) — excluded from signatures")

    sub["_parsed"] = parsed
    sub = sub[sub["_parsed"].notna()].copy()
    sub["raw_sig"] = sub[STRUCT_COL].map(raw_signature)
    sub["canon_sig"] = sub["_parsed"].map(canonical_signature)
    sub["n_sites"] = sub["_parsed"].map(lambda p: len(p["sites"]))
    for k in ("a", "b", "c", "alpha", "beta", "gamma"):
        sub[k] = sub["_parsed"].map(lambda p, k=k: p[k])
    sub = sub.drop(columns=["_parsed"])

    # ── join featurized crystal_system / is_centrosymmetric ─────────────
    print("\nLoading featurized CSV for crystal_system / is_centrosymmetric...")
    feat = read_csv_safe(FEAT_CSV, dtype={ID_COL: str}, low_memory=False)
    feat_cols = [c for c in ("crystal_system", "is_centrosymmetric", "band_gap")
                 if c in feat.columns]
    sub = sub.merge(feat[[ID_COL] + feat_cols], on=ID_COL, how="left")

    detail_path = MAINTENANCE_DIR / "polymorphism_detail.csv"
    MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
    sub.to_csv(detail_path, index=False)
    print(f"Detail written: {detail_path} ({len(sub):,} rows)")

    # ── per-formula summary ──────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("3. PER-FORMULA SUMMARY")
    print("=" * 74)
    rows = []
    for formula, g in sub.groupby(FORMULA_COL):
        n_entries = len(g)
        n_raw = g["raw_sig"].nunique()
        n_canon = g["canon_sig"].nunique()
        if "crystal_system" in g.columns and "is_centrosymmetric" in g.columns:
            label_pairs = g[["crystal_system", "is_centrosymmetric"]].apply(tuple, axis=1)
            n_labels = label_pairs.nunique()
        else:
            n_labels = None
        # Supercells are the SAME phase, not polymorphs. If every entry's
        # site count reduces to the same primitive cell size, differing
        # coordinates are a cell-multiplier artifact, not a structural
        # difference -- canonical_signature cannot see this on its own.
        site_counts = sorted(set(g["n_sites"]))
        if len(site_counts) > 1:
            from math import gcd
            base = 0
            for v in site_counts:
                base = gcd(base, int(v))
            base = base or 1
            all_multiples = all(int(v) % base == 0 for v in site_counts)
        else:
            all_multiples = False

        n_bg = g["band_gap"].nunique(dropna=True) if "band_gap" in g.columns else None

        rows.append({
            "formula": formula,
            "n_entries": n_entries,
            "n_distinct_raw": n_raw,
            "n_distinct_canonical": n_canon,
            "n_distinct_cs_centro_labels": n_labels,
            "n_sites_values": ",".join(str(int(v)) for v in site_counts),
            "cells_are_supercell_multiples": all_multiples,
            "n_distinct_bandgap": n_bg,
            # the actual question: structures differ but label doesn't
            "structurally_distinct_but_same_label":
                (n_canon > 1) and (n_labels == 1) if n_labels is not None else None,
            # identical structure, differing band gap -> unexplainable by
            # structure; needs manual review (NOT necessarily an error:
            # different calculation settings would also produce this)
            "bandgap_varies_same_structure":
                (n_canon == 1) and (n_bg is not None) and (n_bg > 1),
        })
    summary = pd.DataFrame(rows).sort_values("n_entries", ascending=False)
    summary_path = MAINTENANCE_DIR / "polymorphism_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"Summary written: {summary_path} ({len(summary):,} formulas)")

    same_structure = (summary["n_distinct_canonical"] == 1).sum()
    diff_structure = (summary["n_distinct_canonical"] > 1).sum()
    print(f"\n  multi-entry formulas total              : {len(summary):,}")
    print(f"  ...all entries geometrically identical   : {same_structure:,}"
          f"   <- same structure repeated (dup entries, not polymorphs)")
    print(f"  ...entries geometrically distinct        : {diff_structure:,}"
          f"   <- genuinely different structures under one formula")

    if "structurally_distinct_but_same_label" in summary.columns:
        collapsed = summary["structurally_distinct_but_same_label"].fillna(False).sum()
        print(f"\n  THE FINDING:")
        print(f"  formulas where structures differ but crystal_system+centro")
        print(f"  is IDENTICAL across all of them : {int(collapsed):,} "
              f"/ {diff_structure:,} distinct-structure formulas"
              f"{f' ({100*collapsed/diff_structure:.1f}%)' if diff_structure else ''}")
        print(f"  -> these two features cannot distinguish those entries at all.")

    print("\n  Worst offenders (most distinct structures, same label):")
    worst = summary[summary["structurally_distinct_but_same_label"] == True] \
        .sort_values("n_distinct_canonical", ascending=False).head(10)
    if worst.empty:
        print("    none")
    else:
        print(worst[["formula", "n_entries", "n_distinct_canonical",
                     "n_distinct_cs_centro_labels"]].to_string(index=False))

    # ── supercell caveat on the "distinct structure" count ──────────────
    if "cells_are_supercell_multiples" in summary.columns:
        sc = summary[(summary["n_distinct_canonical"] > 1) &
                     (summary["cells_are_supercell_multiples"] == True)]
        print(f"\n  CAVEAT on the {diff_structure:,} 'distinct structure' formulas:")
        print(f"  {len(sc):,} of them have site counts that are exact multiples")
        print(f"  of a common primitive cell — i.e. supercells of ONE phase,")
        print(f"  not polymorphs. canonical_signature counts them as distinct")
        print(f"  because the coordinates genuinely differ; crystallographically")
        print(f"  they are the same structure. Subtract these before quoting")
        print(f"  a polymorph count.")
        if not sc.empty:
            print("\n  Examples:")
            print(sc[["formula", "n_entries", "n_distinct_canonical",
                      "n_sites_values"]].head(8).to_string(index=False))

    # ── band gap differing with identical structure ─────────────────────
    if "bandgap_varies_same_structure" in summary.columns:
        bg = summary[summary["bandgap_varies_same_structure"] == True]
        print(f"\n  MANUAL REVIEW — identical structure, differing band gap:")
        print(f"  {len(bg):,} formula(s), {int(bg['n_entries'].sum()):,} entries")
        print(f"  Structure cannot explain the difference. This is NOT")
        print(f"  automatically an error — Materials Project entries can come")
        print(f"  from different functionals/calculation settings, which would")
        print(f"  produce exactly this. But it is not resolvable from the data")
        print(f"  in these CSVs alone, so it is surfaced, not judged.")
        if not bg.empty:
            print("\n  Worst by band-gap spread:")
            print(bg.sort_values("n_distinct_bandgap", ascending=False)
                  [["formula", "n_entries", "n_distinct_canonical",
                    "n_distinct_bandgap"]].head(10).to_string(index=False))

    print("\n" + "=" * 74)
    print("Done. This is a measurement, not a verdict — inspect")
    print("polymorphism_detail.csv for the raw lattice/site numbers behind")
    print("any formula before deciding what it means for the KG.")
    print("\nReminder: canonical_signature is a LOWER BOUND on structural")
    print("identity. It is blind to origin choice and cell setting, so it")
    print("still calls some identical structures distinct. pymatgen's")
    print("StructureMatcher is the correct tool and would lower the")
    print("'distinct structure' counts further; omitted here to avoid the")
    print("local matminer/pandas conflict.")


if __name__ == "__main__":
    main()
