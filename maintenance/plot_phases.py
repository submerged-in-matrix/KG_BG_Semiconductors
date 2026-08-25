"""
plot_phases.py — Figure explaining phase multiplicity in the KG's source data.

Reads the two reports produced by:
    python -m maintenance.polymorphism_check       -> polymorphism_summary.csv
    python -m maintenance.polymorphism_tolerance   -> polymorphism_tolerance_report.csv

Terminology used here (and it matters):
  material : a composition. "ZnS" is one material.
  phase    : a structurally distinct form of that material (polymorph).
             ZnS has several. Materials Project holds many computed
             entries per phase.
  entry    : one row in the source data = one MP calculation.

The KG models formula, band gap, crystal system, centrosymmetry. It has
no property that distinguishes one phase from another, which is what
these panels quantify.

Run from repo root:
    python maintenance\\plot_phases.py
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # no display needed; writes a file
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from utils.csv_io import read_csv_safe

ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_DIR = ROOT / "maintenance"
SUMMARY = MAINTENANCE_DIR / "polymorphism_summary.csv"
TOL_REPORT = MAINTENANCE_DIR / "polymorphism_tolerance_report.csv"
OUT_PNG = MAINTENANCE_DIR / "phases_overview.png"

TOLERANCES = [0.0, 1e-9, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 5e-2, 1e-1]

INK = "#1a1a1a"
ACCENT = "#2b6cb0"
WARN = "#b7791f"
MUTED = "#a0aec0"


def main():
    for p in (SUMMARY, TOL_REPORT):
        if not p.exists():
            print(f"MISSING: {p}")
            print("Run polymorphism_check.py and polymorphism_tolerance.py first.")
            return

    summ = read_csv_safe(SUMMARY, low_memory=False)
    tol = read_csv_safe(TOL_REPORT, low_memory=False)
    for c in ("vol_per_atom_rel_spread", "smallest_vpa_gap_rel",
              "n_entries", "n_reduced_compositions", "angle_spread_deg"):
        if c in tol.columns:
            tol[c] = pd.to_numeric(tol[c], errors="coerce")

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle("Phase multiplicity in the source data — and what the KG can see",
                 fontsize=14, color=INK, y=0.98)

    # ── A. entries per material ────────────────────────────────────────
    ax = axes[0][0]
    counts = tol["n_entries"].dropna().astype(int)
    bins = np.arange(2, min(counts.max(), 60) + 2)
    ax.hist(counts, bins=bins, color=ACCENT, edgecolor="white", linewidth=0.4)
    ax.set_yscale("log")
    ax.set_xlabel("MP entries per material (composition)")
    ax.set_ylabel("number of materials (log)")
    ax.set_title(f"A. {len(counts):,} materials have >1 entry\n"
                 f"(max {counts.max():,}; x-axis clipped at 60)",
                 fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # ── B. how small are the smallest differences ──────────────────────
    ax = axes[0][1]
    gaps = tol["smallest_vpa_gap_rel"].dropna()
    gaps = gaps[gaps > 0]
    if len(gaps):
        srt = np.sort(gaps)
        cdf = np.arange(1, len(srt) + 1) / len(srt) * 100
        ax.plot(srt, cdf, color=ACCENT, linewidth=2)
        ax.set_xscale("log")
        for thresh, lbl in [(1e-6, "1e-6"), (1e-4, "1e-4"), (1e-2, "1e-2")]:
            pct = (gaps < thresh).mean() * 100
            ax.axvline(thresh, color=MUTED, linestyle=":", linewidth=1)
            ax.annotate(f"{lbl}\n{pct:.1f}%", xy=(thresh, 50),
                        fontsize=7, color=INK, ha="center",
                        bbox=dict(boxstyle="round,pad=0.25", fc="white",
                                  ec=MUTED, linewidth=0.5))
    ax.set_xlabel("relative gap between the two closest entries (volume/atom)")
    ax.set_ylabel("cumulative % of materials")
    ax.set_title("B. Most closest-pairs differ by ~1% — real, not noise",
                 fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # ── C. tolerance sweep ─────────────────────────────────────────────
    ax = axes[1][0]
    total = len(tol)
    ys = []
    for t in TOLERANCES:
        one = ((tol["n_reduced_compositions"].fillna(1) <= 1)
               & (tol["vol_per_atom_rel_spread"].fillna(np.inf) <= t)).sum()
        ys.append(100 * one / total if total else 0)
    xs = [max(t, 1e-10) for t in TOLERANCES]
    ax.plot(xs, ys, "o-", color=ACCENT, linewidth=2, markersize=4)
    ax.set_xscale("log")
    ax.axvspan(1e-10, 1e-4, color=MUTED, alpha=0.18)
    ax.annotate("flat: result insensitive\nto threshold here",
                xy=(1e-7, max(ys) * 0.55), fontsize=8, color=INK)
    ax.set_xlabel("tolerance on volume/atom relative spread")
    ax.set_ylabel("% of materials collapsing to a single phase")
    ax.set_title("C. Only a few % are spurious splits at any sane tolerance",
                 fontsize=10, loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    # ── D. what the KG can actually distinguish ────────────────────────
    ax = axes[1][1]
    if "structurally_distinct_but_same_label" in summ.columns:
        flag = summ["structurally_distinct_but_same_label"].astype(str).str.lower()
        collapsed = int((flag == "true").sum())
        distinct_ok = len(summ) - collapsed
        vals = [distinct_ok, collapsed]
        labels = [f"distinguishable\n{distinct_ok:,}",
                  f"NOT distinguishable\n{collapsed:,}"]
        cols = [ACCENT, WARN]
        bars = ax.bar(labels, vals, color=cols, edgecolor="white")
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{100*v/len(summ):.1f}%",
                    ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_ylabel("number of materials")
        ax.set_title("D. Can crystal_system + centrosymmetry tell the\n"
                     "phases apart? For 28.8%, no.",
                     fontsize=10, loc="left")
        ax.spines[["top", "right"]].set_visible(False)

    fig.text(0.5, 0.005,
             "Phase identity judged by volume per atom (invariant to cell "
             "setting and supercelling) plus reduced composition. "
             "Angles excluded — they change with cell setting, not phase. "
             "Approximation, not a crystallographic match: pymatgen "
             "StructureMatcher is the correct tool.",
             ha="center", fontsize=7.5, color="#4a5568", wrap=True)

    fig.tight_layout(rect=[0, 0.03, 1, 0.96])
    fig.savefig(OUT_PNG, dpi=160)
    print(f"Written: {OUT_PNG}")


if __name__ == "__main__":
    main()
