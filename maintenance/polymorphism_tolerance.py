"""
polymorphism_tolerance.py — WHERE do same-formula entries differ, and by
HOW MUCH?

polymorphism_check.py answered "are they byte-identical after rounding?"
and got 20,722 / 20,722 distinct, 0 identical. That result is about the
metric, not the data: two relaxations of the same phase differ in the 3rd
decimal of a lattice parameter, and a supercell changes every coordinate
while being the same structure. Exact-signature comparison flags both as
"different" and therefore can never report a match.

This script does not pick a tolerance. It shows the DISTRIBUTION of
differences so a tolerance can be chosen from evidence, and localizes
which quantity each group differs in.

INVARIANTS USED (chosen because they survive supercelling):
  reduced_composition - element ratios reduced by gcd. A 2x2x1 supercell
                        has the same reduced composition; a different
                        stoichiometry does not.
  volume_per_atom     - cell volume / n_sites. Invariant under supercell
                        (both scale together), sensitive to genuinely
                        different packing. Computed from the triclinic
                        formula, so valid for all 7 systems.
  angles              - alpha/beta/gamma. Unchanged by axis-aligned
                        supercells; changed by a different lattice type.

  Raw a/b/c and fractional coordinates are deliberately NOT compared
  directly: both change under supercelling for reasons that have nothing
  to do with phase.

WHAT IT REPORTS
  1. Per group: the SMALLEST non-zero difference found in each invariant
     (the "tiniest difference anywhere" -- if a group's closest pair
     differs by 1e-7 in volume/atom, calling them distinct phases is a
     rounding artifact, not chemistry).
  2. A tolerance sweep: how many groups collapse to "one phase" at each
     candidate threshold. The shape of that curve is the evidence for
     where to set it.
  3. Which invariant each group differs in -- composition vs volume vs
     angles -- so "different" is never just a boolean.

INPUT: maintenance/polymorphism_detail.csv (written by
polymorphism_check.py -- already has parsed lattice params and n_sites,
so structures are not re-parsed here).

Read-only.

Run from repo root:
    python maintenance\\polymorphism_tolerance.py
"""

import math
from collections import Counter
from math import gcd
from pathlib import Path

import numpy as np
import pandas as pd

from utils.csv_io import read_csv_safe

ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_DIR = ROOT / "maintenance"
DETAIL = MAINTENANCE_DIR / "polymorphism_detail.csv"
REPORT = MAINTENANCE_DIR / "polymorphism_tolerance_report.csv"

ID_COL = "material_id"
FORMULA_COL = "formula"

# Relative tolerances swept for volume-per-atom agreement.
TOLERANCES = [0.0, 1e-9, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 5e-2, 1e-1]
ANGLE_TOL_DEG = 0.5   # degrees, for the "same lattice type" check


def cell_volume(a, b, c, alpha, beta, gamma):
    """Triclinic cell volume — general, valid for every crystal system."""
    try:
        ca, cb, cg = (math.cos(math.radians(x)) for x in (alpha, beta, gamma))
        f = 1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg
        if f <= 0:
            return None
        return a * b * c * math.sqrt(f)
    except (TypeError, ValueError):
        return None


def reduced_composition_from_formula(formula):
    """
    Element ratios reduced by gcd. Parsed from the formula string, which
    is already the reduced formula in this dataset -- so this mainly
    normalizes notation. Returns None if unparseable.
    """
    import re
    if not isinstance(formula, str):
        return None
    toks = re.findall(r"([A-Z][a-z]?)(\d*)", formula.replace("(", "").replace(")", ""))
    counts = Counter()
    for el, num in toks:
        if not el:
            continue
        counts[el] += int(num) if num else 1
    if not counts:
        return None
    g = 0
    for v in counts.values():
        g = gcd(g, v)
    g = g or 1
    return tuple(sorted((el, v // g) for el, v in counts.items()))


def smallest_nonzero_gap(values):
    """Smallest non-zero difference between any two values in the list."""
    vals = sorted(v for v in values if v is not None and not pd.isna(v))
    if len(vals) < 2:
        return None
    gaps = [b - a for a, b in zip(vals, vals[1:]) if b - a > 0]
    return min(gaps) if gaps else 0.0


def main():
    if not DETAIL.exists():
        print(f"MISSING: {DETAIL}")
        print("Run `python -m maintenance.polymorphism_check` first — this")
        print("script reuses its parsed lattice output rather than "
              "re-parsing 69k structures.")
        return

    print(f"Loading {DETAIL.name} ...")
    df = read_csv_safe(DETAIL, dtype={ID_COL: str, FORMULA_COL: str},
                       low_memory=False)
    print(f"  {len(df):,} rows, {df[FORMULA_COL].nunique():,} formulas")

    needed = ["a", "b", "c", "alpha", "beta", "gamma", "n_sites"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"  Detail file is missing column(s): {missing}")
        print(f"  Available: {list(df.columns)}")
        return

    for col in needed:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    print("Computing invariants (volume, volume/atom, reduced composition)...")
    df["volume"] = [cell_volume(*row) for row in
                    df[["a", "b", "c", "alpha", "beta", "gamma"]].itertuples(index=False)]
    df["vol_per_atom"] = df["volume"] / df["n_sites"].replace(0, np.nan)
    df["reduced_comp"] = df[FORMULA_COL].map(reduced_composition_from_formula)

    n_bad_vol = df["vol_per_atom"].isna().sum()
    if n_bad_vol:
        print(f"  WARNING: {n_bad_vol:,} row(s) have no computable "
              f"volume/atom — excluded from volume verdicts")

    # ── Per-group difference localization ──────────────────────────────
    print("\n" + "=" * 78)
    print("1. WHERE DOES EACH GROUP DIFFER, AND BY HOW LITTLE?")
    print("=" * 78)

    rows = []
    for formula, grp in df.groupby(FORMULA_COL, dropna=True):
        vpa = grp["vol_per_atom"].dropna()
        vpa_min, vpa_max = (vpa.min(), vpa.max()) if len(vpa) else (None, None)
        vpa_rel_spread = ((vpa_max - vpa_min) / vpa_min
                          if vpa_min and vpa_min > 0 else None)
        vpa_tiniest = smallest_nonzero_gap(list(vpa))
        vpa_tiniest_rel = (vpa_tiniest / vpa_min
                           if vpa_tiniest and vpa_min and vpa_min > 0 else None)

        angle_spread = max(
            (grp[k].max() - grp[k].min()) for k in ("alpha", "beta", "gamma")
        ) if len(grp) > 1 else 0.0

        n_comp = grp["reduced_comp"].nunique(dropna=True)
        site_vals = sorted({int(v) for v in grp["n_sites"].dropna()})
        base = 0
        for v in site_vals:
            base = gcd(base, v)
        base = base or 1
        supercell_family = len(site_vals) > 1 and all(v % base == 0 for v in site_vals)

        # which invariant actually separates them
        differs_in = []
        if n_comp > 1:
            differs_in.append("composition")
        if vpa_rel_spread is not None and vpa_rel_spread > 1e-6:
            differs_in.append("volume/atom")
        # Reported, never used as a phase criterion. A large angle spread
        # with matching vol/atom is the signature of the same lattice in a
        # different cell setting (primitive vs conventional) -- values
        # clustering near 30/60/90 deg are the tell.
        if angle_spread > ANGLE_TOL_DEG:
            differs_in.append("angles(setting artifact)"
                              if (vpa_rel_spread is not None
                                  and vpa_rel_spread <= 1e-4)
                              else "angles")
        if not differs_in:
            differs_in.append("nothing above threshold")

        rows.append({
            "formula": formula,
            "n_entries": len(grp),
            "n_sites_values": ",".join(str(v) for v in site_vals),
            "supercell_family": supercell_family,
            "n_reduced_compositions": n_comp,
            "vol_per_atom_min": vpa_min,
            "vol_per_atom_max": vpa_max,
            "vol_per_atom_rel_spread": vpa_rel_spread,
            "smallest_vpa_gap_rel": vpa_tiniest_rel,
            "angle_spread_deg": angle_spread,
            "differs_in": "+".join(differs_in),
        })

    summary = pd.DataFrame(rows)

    print("\n  Which invariant separates each group:")
    print(summary["differs_in"].value_counts().to_string())

    print(f"\n  Groups whose cells form a supercell family: "
          f"{int(summary['supercell_family'].sum()):,}")

    # ── The "tiniest difference" distribution ──────────────────────────
    print("\n" + "=" * 78)
    print("2. HOW SMALL ARE THE SMALLEST DIFFERENCES?")
    print("=" * 78)
    print("  Relative gap between the two CLOSEST entries in each group")
    print("  (volume/atom). If this is ~1e-6, those two are the same")
    print("  structure at different convergence, not different phases.\n")

    tiny = summary["smallest_vpa_gap_rel"].dropna()
    if len(tiny):
        for q in [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]:
            print(f"    {int(q*100):>3}th percentile : {tiny.quantile(q):.3e}")
        print(f"\n    exactly 0 (identical vol/atom) : "
              f"{int((tiny == 0).sum()):,} group(s)")
        print(f"    below 1e-6                     : "
              f"{int((tiny < 1e-6).sum()):,} group(s)")
        print(f"    below 1e-4                     : "
              f"{int((tiny < 1e-4).sum()):,} group(s)")
        print(f"    below 1e-2                     : "
              f"{int((tiny < 1e-2).sum()):,} group(s)")

    # ── Tolerance sweep ────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("3. TOLERANCE SWEEP — how many groups collapse to ONE phase?")
    print("=" * 78)
    print("  A group counts as one phase at tolerance t if all entries")
    print("  share a reduced composition AND their volume/atom relative")
    print("  spread is <= t. Angles are deliberately EXCLUDED: vol/atom is")
    print("  already cell-setting invariant, angles are not.\n")
    print(f"    {'tolerance':>12}  {'1 phase':>9}  {'>1 phase':>9}  {'% collapsed':>12}")
    total = len(summary)
    for t in TOLERANCES:
        # Angles are NOT part of this test. Volume per atom is already
        # invariant to cell setting (conventional vs primitive scale
        # volume and atom count together), whereas angles change with the
        # setting while the phase does not. Including them made this sweep
        # undercount collapses by ~230 groups -- pure notation artifact.
        one_phase = (
            (summary["n_reduced_compositions"] <= 1)
            & (summary["vol_per_atom_rel_spread"].fillna(np.inf) <= t)
        ).sum()
        pct = 100 * one_phase / total if total else 0
        print(f"    {t:>12.0e}  {int(one_phase):>9,}  "
              f"{total - int(one_phase):>9,}  {pct:>11.1f}%")

    print("\n  Read the curve, not a single number: a flat region means the")
    print("  result is insensitive to the exact threshold there (a safe")
    print("  place to sit); a steep jump means many groups differ by around")
    print("  that magnitude and the choice matters.")

    # ── Extremes worth eyeballing ──────────────────────────────────────
    print("\n" + "=" * 78)
    print("4. GROUPS MOST LIKELY TO BE SPURIOUS SPLITS")
    print("=" * 78)
    spurious = summary[
        (summary["n_reduced_compositions"] <= 1)
        & (summary["vol_per_atom_rel_spread"].fillna(np.inf) < 1e-4)
    ].sort_values("n_entries", ascending=False)
    print(f"  {len(spurious):,} group(s): same composition, volume/atom "
          f"agreeing to <1e-4")
    if not spurious.empty:
        print(spurious[["formula", "n_entries", "n_sites_values",
                        "vol_per_atom_rel_spread", "angle_spread_deg"]]
              .head(10).to_string(index=False))

    print("\n" + "=" * 78)
    print("5. GROUPS THAT LOOK LIKE GENUINELY DIFFERENT PHASES")
    print("=" * 78)
    genuine = summary[
        (summary["vol_per_atom_rel_spread"].fillna(0) > 1e-2)
        | (summary["n_reduced_compositions"] > 1)
    ].sort_values("vol_per_atom_rel_spread", ascending=False)
    print(f"  {len(genuine):,} group(s): volume/atom differs by >1% or "
          f"composition differs")
    if not genuine.empty:
        print(genuine[["formula", "n_entries", "vol_per_atom_rel_spread",
                       "angle_spread_deg", "differs_in"]]
              .head(10).to_string(index=False))

    MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
    summary.sort_values("n_entries", ascending=False).to_csv(REPORT, index=False)
    print(f"\n  Full per-formula report: {REPORT}")

    print("\n" + "=" * 78)
    print("CAVEATS")
    print("=" * 78)
    print("  - Volume/atom is necessary but NOT sufficient for phase")
    print("    identity: two genuinely different structures can coincide")
    print("    in packing density. Agreement is evidence, not proof.")
    print("  - Angles are compared as raw min/max spread; a general")
    print("    (non-axis-aligned) supercell changes them and will show as")
    print("    a difference here even though the phase is the same.")
    print("  - reduced_composition is parsed from the formula STRING, so")
    print("    it inherits whatever notation the source used.")
    print("  - The correct tool remains pymatgen StructureMatcher, which")
    print("    handles settings, origins and supercells properly. This is")
    print("    a defensible approximation built from what's already parsed,")
    print("    not a replacement for it.")


if __name__ == "__main__":
    main()