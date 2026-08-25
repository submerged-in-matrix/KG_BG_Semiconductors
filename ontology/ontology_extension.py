"""
ontology_extension.py — Re-extract from Materials Project with the fields
needed to resolve the current KG's known limitations.

PROVENANCE OF THIS SCRIPT
Built directly on the original extraction you ran:

    with MPRester(API_KEY) as mpr:
        docs = mpr.materials.summary.search(
            band_gap=(0.1, 3.5),
            fields=["material_id", "formula_pretty", "band_gap", "structure"],
            num_chunks=7,
            chunk_size=1000
        )

Same endpoint (`materials.summary.search`), same chunking pattern, same
`fields=` mechanism. The ONLY change is the contents of `fields` (plus a
band_gap range matching what actually ended up in your dataset -- the
files are named `Bandgap_0_to_5` and the KG contains 0.0 values, so the
0.1-3.5 range above was evidently widened before the final run; set
BAND_GAP_RANGE below to whatever you actually used).

WHY EACH ADDED FIELD, tied to a specific limitation found in the current
graph -- nothing here is speculative "might be nice" data:

  is_gap_direct        Resolves direct vs indirect gap. Currently
                       UNANSWERABLE: the scalar band_gap carries no
                       k-point information, so no post-processing of the
                       existing data can recover it. One boolean fixes it.

  energy_above_hull    Thermodynamic stability. Fixes two things: enables
                       "stable phases only" filtering, and replaces the
                       current representative-selection rule in ask_kg
                       (lowest mp-id, an arbitrary-but-deterministic
                       heuristic explicitly NOT a stability ranking) with
                       a real one.

  symmetry             Space group number/symbol. Directly addresses the
                       measured finding that crystal system +
                       centrosymmetry cannot distinguish the phases of
                       28.8% of multi-entry compositions. Space group is
                       far more discriminating and is a single integer.

  theoretical          Separates experimentally-reported entries from
                       computationally-generated candidates. Explains
                       high phase counts (e.g. compositions with dozens of
                       entries) without needing to guess.

  volume, nsites       Volume per atom, the cell-setting- and
                       supercell-invariant quantity used in
                       maintenance/polymorphism_tolerance.py. Storing it
                       avoids re-deriving it from structure text.

  density              Conventional, cheap, useful for screening.

BUDGET NOTE -- READ BEFORE RUNNING
Every added property is roughly +1 triple per material. At ~150k
materials, each field adds ~150k triples. The graph is currently
~1.36M triples / ~55 MB and the HF Space free tier was already flagged
as RAM-constrained at a smaller size. Adding all seven fields above is
roughly +1M triples (~+40%). Consider ingesting a SUBSET of these fields
(is_gap_direct + energy_above_hull + spacegroup number are the highest
value-per-triple) rather than all of them.

USAGE
    export MP_API_KEY=...        # or set it inline below
    python ontology_extension.py --dry-run     # 1 chunk, inspect fields
    python ontology_extension.py               # full extraction

Writes: data/mp_extended_<timestamp>.csv
Does NOT touch the KG. Ingestion is a separate, later step -- see
extension_workflow.md.
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# Match whatever range produced the current dataset. The files are named
# `full_dataset_Bandgap_0_to_5*`, and the KG contains band gaps of 0.0,
# so the original (0.1, 3.5) was widened. Set this deliberately -- a
# mismatch here silently changes which materials you get back.
BAND_GAP_RANGE = (0.0, 5.0)

CHUNK_SIZE = 1000
NUM_CHUNKS = None          # None = all; set an int to cap for testing

# Original four, kept so the output can be joined against the existing
# dataset on material_id.
BASE_FIELDS = [
    "material_id",
    "formula_pretty",
    "band_gap",
]

# The additions. Ordered by value-per-triple; trim from the BOTTOM if the
# graph size becomes a problem.
EXTENSION_FIELDS = [
    "is_gap_direct",           # direct vs indirect -- currently unanswerable
    "energy_above_hull",       # stability -- fixes representative selection
    "symmetry",                # space group -- distinguishes phases
    "theoretical",             # experimental vs computational candidate
    "nsites",                  # -> volume per atom
    "volume",                  # -> volume per atom
    "density",                 # screening convenience
]

# NOT included, deliberately:
#   structure  -- already have it; re-downloading 150k structures is slow
#                 and the existing parse works
#   bulk_modulus / shear_modulus / dielectric -- only populated for a
#                 small subset; would be mostly-null columns
#   band structure objects -- a different endpoint, much larger payloads


def flatten_symmetry(doc):
    """
    `symmetry` comes back as an object, not a scalar. Pull the useful
    scalars out of it. Field names verified against the SymmetryData
    model shape -- if the API changes, --dry-run will surface it.
    """
    sym = getattr(doc, "symmetry", None)
    if sym is None:
        return {}
    out = {}
    for attr, col in [("number", "spacegroup_number"),
                      ("symbol", "spacegroup_symbol"),
                      ("point_group", "point_group"),
                      ("crystal_system", "mp_crystal_system")]:
        val = getattr(sym, attr, None)
        if val is not None:
            out[col] = str(val)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch ONE chunk and print the fields actually "
                         "returned, without writing anything")
    ap.add_argument("--api-key", default=os.environ.get("MP_API_KEY"))
    args = ap.parse_args()

    if not args.api_key:
        print("No API key. Set MP_API_KEY or pass --api-key.")
        print("Get one at https://materialsproject.org/api")
        return

    try:
        from mp_api.client import MPRester
    except ImportError:
        print("mp_api not installed:  pip install mp-api")
        return

    fields = BASE_FIELDS + EXTENSION_FIELDS
    num_chunks = 1 if args.dry_run else NUM_CHUNKS

    print(f"Band gap range : {BAND_GAP_RANGE}")
    print(f"Fields         : {fields}")
    print(f"Chunks         : {num_chunks or 'all'} x {CHUNK_SIZE}")
    print()

    with MPRester(args.api_key) as mpr:
        docs = mpr.materials.summary.search(
            band_gap=BAND_GAP_RANGE,
            fields=fields,
            num_chunks=num_chunks,
            chunk_size=CHUNK_SIZE,
        )

    print(f"Retrieved {len(docs)} materials.")
    if not docs:
        return

    if args.dry_run:
        d = docs[0]
        print("\nFIELDS ACTUALLY RETURNED on the first document:")
        for f in fields:
            val = getattr(d, f, "<MISSING>")
            shown = str(val)
            if len(shown) > 90:
                shown = shown[:90] + " ..."
            print(f"  {f:22} = {shown}")
        print("\nAny field showing <MISSING> is not available on this "
              "endpoint under that name -- correct it in EXTENSION_FIELDS "
              "before a full run. Field names are not guaranteed stable "
              "across mp-api versions; this dry run is the check.")
        sym = flatten_symmetry(d)
        print(f"\nflatten_symmetry() ->  {sym}")
        return

    rows = []
    for d in docs:
        rec = {}
        for f in BASE_FIELDS + EXTENSION_FIELDS:
            if f == "symmetry":
                continue
            rec[f] = getattr(d, f, None)
        rec.update(flatten_symmetry(d))
        rows.append(rec)

    df = pd.DataFrame(rows)

    # rename to match the existing pipeline's column conventions
    df = df.rename(columns={"formula_pretty": "formula"})

    # derived: the invariant used for phase comparison
    if {"volume", "nsites"} <= set(df.columns):
        df["volume_per_atom"] = pd.to_numeric(df["volume"], errors="coerce") / \
                                pd.to_numeric(df["nsites"], errors="coerce")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = DATA_DIR / f"mp_extended_{stamp}.csv"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"\nWrote {out}  ({len(df):,} rows, {len(df.columns)} columns)")
    print(f"Columns: {list(df.columns)}")
    print("\nNull counts per column:")
    print(df.isna().sum().to_string())
    print("\nNOTE: columns with high null counts are only populated for a "
          "subset of Materials Project entries. Decide whether to ingest "
          "them before adding them to the ontology -- a mostly-null "
          "property costs triples without adding query value.")
    print("\nNothing was written to the KG. See extension_workflow.md.")


if __name__ == "__main__":
    main()
