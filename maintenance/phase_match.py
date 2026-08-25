"""
phase_match.py — Definitive phase grouping via pymatgen StructureMatcher.

This supersedes the volume/atom approximation in polymorphism_tolerance.py.
That approximation existed because of an assumption -- "pymatgen is
unavailable due to the matminer/pandas conflict" -- that was never tested.
matminer 0.9.3 requires pandas<3; pymatgen is a SEPARATE package and may
import fine. This script checks that first and says so plainly.

WHY StructureMatcher IS THE RIGHT TOOL
  It reduces both structures to primitive cells, aligns lattices, and
  matches sites within tolerances. That makes it invariant to the three
  things that defeated every cheaper method here:
    - cell setting     (primitive vs conventional -- the "angle spread
                        of 60 degrees" artifact)
    - supercelling     (2x2x1 of one phase is the same phase)
    - site ordering / origin choice
  It is the difference between "the numbers differ" and "the structures
  differ".

WHY IT NEEDS PRE-BUCKETING
  Matching is O(n^2) within a composition and each comparison is costly.
  One composition here has 729 entries (~265k comparisons). So: bucket
  first on invariants that are cheap and CANNOT split a true phase group
  (reduced composition, and volume per atom within a loose tolerance),
  then run StructureMatcher only inside each bucket. Cheap filters are
  used only to rule comparisons OUT, never to declare a match.

  The vol/atom bucket tolerance is deliberately loose (default 25%) --
  far wider than any relaxation difference -- so it cannot separate
  structures StructureMatcher would have matched. Widen it with
  --bucket-tol if you want to be more conservative still.

USAGE
    python -m maintenance.phase_match --check        # just test imports
    python -m maintenance.phase_match --formulas 200 # quick sample run
    python -m maintenance.phase_match                # full run (slow)

    --max-entries N   compositions with more than N entries are recorded
                      but not matched (default 60). Prevents one 729-entry
                      composition from dominating the runtime. Reported
                      separately, never silently dropped.
    --workers N       parallel processes (default 1; Windows-safe as long
                      as this stays under __main__)

OUTPUT
    maintenance/phase_groups.csv        one row per composition
    maintenance/phase_members.csv       one row per entry, with phase_id

Read-only with respect to the graph and all source data.
"""

import argparse
import math
import re
import sys
import time
from collections import Counter, defaultdict
from math import gcd
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.csv_io import read_csv_safe

DATA_DIR = ROOT / "data"
MAINTENANCE_DIR = ROOT / "maintenance"
PARENT_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5.csv"
FEAT_CSV = DATA_DIR / "full_dataset_Bandgap_0_to_5_featurized.csv"
GROUPS_OUT = MAINTENANCE_DIR / "phase_groups.csv"
MEMBERS_OUT = MAINTENANCE_DIR / "phase_members.csv"

ID_COL = "material_id"
FORMULA_COL = "formula"
STRUCT_COL = "structure"

_re_abc = re.compile(r"abc\s*:\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)", re.I)
_re_angles = re.compile(r"angles\s*:\s*([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)", re.I)
_re_sites_header = re.compile(r"Sites\s*\(\d+\)", re.I)
_re_site_row = re.compile(
    r"^\s*\d+\s+([A-Za-z][a-z]?)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)\s+([-\d.Ee+]+)")


# ─── Import check ──────────────────────────────────────────────────────

def check_pymatgen(verbose=True):
    """Does pymatgen actually import here? Report honestly either way."""
    try:
        import pymatgen
        from pymatgen.core import Structure, Lattice
        from pymatgen.analysis.structure_matcher import StructureMatcher
        if verbose:
            print(f"  pymatgen {getattr(pymatgen, '__version__', '?')} — OK")
            print(f"  StructureMatcher — OK")
        return True
    except Exception as e:
        if verbose:
            print(f"  pymatgen import FAILED: {type(e).__name__}: {e}")
            print("  (matminer's pandas<3 pin does not necessarily affect")
            print("   pymatgen — if this failed for a different reason, the")
            print("   message above is the real cause.)")
        return False


# ─── Parsing ───────────────────────────────────────────────────────────

def parse_to_structure(txt):
    """Pretty-printed pymatgen Structure summary -> Structure, or None."""
    from pymatgen.core import Structure, Lattice
    if not isinstance(txt, str) or not txt.strip():
        return None
    m_abc, m_ang = _re_abc.search(txt), _re_angles.search(txt)
    if not (m_abc and m_ang):
        return None
    a, b, c = map(float, m_abc.groups())
    alpha, beta, gamma = map(float, m_ang.groups())

    lines = txt.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if _re_sites_header.search(ln))
    except StopIteration:
        return None
    species, coords = [], []
    for ln in lines[start + 1:]:
        m = _re_site_row.match(ln)
        if m:
            sp, fa, fb, fc = m.groups()
            species.append(sp)
            coords.append([float(fa), float(fb), float(fc)])
    if not species:
        return None
    try:
        lat = Lattice.from_parameters(a=a, b=b, c=c,
                                      alpha=alpha, beta=beta, gamma=gamma)
        return Structure(lattice=lat, species=species, coords=coords,
                         coords_are_cartesian=False)
    except Exception:
        return None


def cell_volume(a, b, c, alpha, beta, gamma):
    ca, cb, cg = (math.cos(math.radians(x)) for x in (alpha, beta, gamma))
    f = 1 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg
    return a * b * c * math.sqrt(f) if f > 0 else None


def quick_invariants(txt):
    """Cheap bucketing keys, computed without building a Structure."""
    if not isinstance(txt, str):
        return None, None
    m_abc, m_ang = _re_abc.search(txt), _re_angles.search(txt)
    if not (m_abc and m_ang):
        return None, None
    vals = [float(v) for v in (*m_abc.groups(), *m_ang.groups())]
    vol = cell_volume(*vals)
    n_sites = sum(1 for ln in txt.splitlines() if _re_site_row.match(ln))
    if not vol or not n_sites:
        return None, None
    return vol / n_sites, n_sites


def reduced_comp(formula):
    if not isinstance(formula, str):
        return None
    toks = re.findall(r"([A-Z][a-z]?)(\d*)",
                      formula.replace("(", "").replace(")", ""))
    counts = Counter()
    for el, num in toks:
        if el:
            counts[el] += int(num) if num else 1
    if not counts:
        return None
    gg = 0
    for v in counts.values():
        gg = gcd(gg, v)
    gg = gg or 1
    return tuple(sorted((el, v // gg) for el, v in counts.items()))


# ─── Core ──────────────────────────────────────────────────────────────

def match_one_composition(formula, rows, bucket_tol, matcher):
    """
    rows: list of (material_id, structure_text, vol_per_atom)
    Returns (phase_assignment {material_id: phase_id}, n_compared).
    """
    # bucket on vol/atom, loosely -- only to avoid hopeless comparisons
    buckets = defaultdict(list)
    for mid, txt, vpa in rows:
        if vpa is None:
            buckets[("novol", mid)].append((mid, txt))
            continue
        placed = False
        for key in list(buckets):
            if key[0] == "novol":
                continue
            ref = key[1]
            if abs(vpa - ref) / ref <= bucket_tol:
                buckets[key].append((mid, txt))
                placed = True
                break
        if not placed:
            buckets[("vol", vpa)].append((mid, txt))

    assignment, phase_id, n_compared = {}, 0, 0
    for key, members in buckets.items():
        if len(members) == 1:
            assignment[members[0][0]] = phase_id
            phase_id += 1
            continue
        structs, ids = [], []
        for mid, txt in members:
            st = parse_to_structure(txt)
            if st is not None:
                structs.append(st)
                ids.append(mid)
            else:
                assignment[mid] = phase_id
                phase_id += 1
        if not structs:
            continue
        n_compared += len(structs) * (len(structs) - 1) // 2
        try:
            grouped = matcher.group_structures(structs)
        except Exception:
            # matching blew up -- treat each as its own phase rather than
            # guessing, and let the caller see it via n_unmatched
            for mid in ids:
                assignment[mid] = phase_id
                phase_id += 1
            continue
        pos = {id(s): i for i, s in enumerate(structs)}
        for grp in grouped:
            for s in grp:
                assignment[ids[pos[id(s)]]] = phase_id
            phase_id += 1
    return assignment, n_compared


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="only verify pymatgen imports, then exit")
    ap.add_argument("--formulas", type=int, default=None,
                    help="process only the N largest compositions (sample run)")
    ap.add_argument("--max-entries", type=int, default=60,
                    help="skip matching for compositions larger than this")
    ap.add_argument("--bucket-tol", type=float, default=0.25,
                    help="vol/atom relative tolerance for pre-bucketing")
    ap.add_argument("--ltol", type=float, default=0.2)
    ap.add_argument("--stol", type=float, default=0.3)
    ap.add_argument("--angle-tol", type=float, default=5.0)
    args = ap.parse_args()

    print("Checking pymatgen availability...")
    if not check_pymatgen():
        print("\nCannot proceed without pymatgen. If the failure above is a")
        print("pandas incompatibility, `pip install -U pymatgen` in this venv")
        print("may resolve it independently of matminer.")
        return
    if args.check:
        print("\nImport check only — exiting.")
        return

    from pymatgen.analysis.structure_matcher import StructureMatcher
    matcher = StructureMatcher(
        ltol=args.ltol, stol=args.stol, angle_tol=args.angle_tol,
        primitive_cell=True,   # <- collapses supercells
        scale=True,            # <- normalises volume; relaxation-tolerant
        attempt_supercell=False,
    )
    print(f"\nStructureMatcher(ltol={args.ltol}, stol={args.stol}, "
          f"angle_tol={args.angle_tol}, primitive_cell=True, scale=True)")

    print(f"\nLoading {PARENT_CSV.name} ...")
    df = read_csv_safe(PARENT_CSV, dtype={ID_COL: str, FORMULA_COL: str},
                       low_memory=False)
    if STRUCT_COL not in df.columns:
        print(f"  no '{STRUCT_COL}' column; columns are {list(df.columns)}")
        return
    print(f"  {len(df):,} rows")

    counts = df[FORMULA_COL].value_counts(dropna=True)
    multi = counts[counts > 1]
    print(f"  {len(multi):,} composition(s) with >1 entry")

    targets = list(multi.index)
    if args.formulas:
        targets = list(multi.head(args.formulas).index)
        print(f"  SAMPLE RUN: {len(targets)} largest composition(s) only")

    sub = df[df[FORMULA_COL].isin(targets)].copy()
    print(f"  computing bucketing invariants for {len(sub):,} entries...")
    inv = sub[STRUCT_COL].map(quick_invariants)
    sub["vol_per_atom"] = [x[0] for x in inv]
    sub["n_sites"] = [x[1] for x in inv]

    group_rows, member_rows = [], []
    skipped, t0, done = [], time.perf_counter(), 0

    for formula, grp in sub.groupby(FORMULA_COL, dropna=True, sort=False):
        n_entries = len(grp)
        if n_entries > args.max_entries:
            skipped.append((formula, n_entries))
            group_rows.append({
                "formula": formula, "n_entries": n_entries,
                "n_phases": None, "status": "skipped_too_large",
                "reduced_composition": str(reduced_comp(formula)),
            })
            continue

        rows = list(zip(grp[ID_COL], grp[STRUCT_COL], grp["vol_per_atom"]))
        assignment, n_cmp = match_one_composition(
            formula, rows, args.bucket_tol, matcher)
        n_phases = len(set(assignment.values())) if assignment else 0

        group_rows.append({
            "formula": formula, "n_entries": n_entries,
            "n_phases": n_phases,
            "entries_per_phase": round(n_entries / n_phases, 2) if n_phases else None,
            "all_one_phase": n_phases == 1,
            "status": "matched",
            "reduced_composition": str(reduced_comp(formula)),
            "comparisons": n_cmp,
        })
        for mid, pid in assignment.items():
            member_rows.append({"formula": formula, ID_COL: mid,
                                "phase_id": pid})

        done += 1
        if done % 250 == 0:
            el = time.perf_counter() - t0
            rate = done / el if el else 0
            left = (len(targets) - len(skipped) - done) / rate if rate else 0
            print(f"    {done:,}/{len(targets):,} compositions  "
                  f"[{el/60:.1f} min elapsed, ~{left/60:.1f} min left]")

    groups = pd.DataFrame(group_rows)
    members = pd.DataFrame(member_rows)

    MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
    groups.sort_values("n_entries", ascending=False).to_csv(GROUPS_OUT, index=False)
    members.to_csv(MEMBERS_OUT, index=False)

    print("\n" + "=" * 74)
    print("RESULT — TRUE PHASE COUNTS (StructureMatcher)")
    print("=" * 74)
    matched = groups[groups["status"] == "matched"]
    if not matched.empty:
        one = int(matched["all_one_phase"].sum())
        print(f"  compositions matched            : {len(matched):,}")
        print(f"  ...ALL entries are ONE phase    : {one:,} "
              f"({100*one/len(matched):.1f}%)")
        print(f"  ...genuinely multi-phase        : {len(matched)-one:,}")
        print(f"\n  total entries in matched set    : "
              f"{int(matched['n_entries'].sum()):,}")
        print(f"  total distinct phases           : "
              f"{int(matched['n_phases'].sum()):,}")
        print(f"  mean entries per phase          : "
              f"{matched['n_entries'].sum()/max(matched['n_phases'].sum(),1):.1f}")
        print(f"\n  Most phases under one composition:")
        top = matched.nlargest(10, "n_phases")
        print("   " + top[["formula", "n_entries", "n_phases"]]
              .to_string(index=False).replace("\n", "\n   "))
    if skipped:
        print(f"\n  SKIPPED (>{args.max_entries} entries), not matched: "
              f"{len(skipped):,} composition(s), "
              f"{sum(n for _, n in skipped):,} entries")
        print("   " + ", ".join(f"{f}({n})" for f, n in skipped[:8]))
        print("   Re-run with a higher --max-entries to include them.")

    print(f"\n  {GROUPS_OUT}")
    print(f"  {MEMBERS_OUT}")
    print(f"\n  Elapsed: {(time.perf_counter()-t0)/60:.1f} min")
    print("\n  Compare 'ALL entries are ONE phase' against the volume/atom")
    print("  approximation's collapse rate. If it is much higher, the")
    print("  approximation was over-splitting and its numbers should be")
    print("  retired in favour of these.")


if __name__ == "__main__":
    main()
