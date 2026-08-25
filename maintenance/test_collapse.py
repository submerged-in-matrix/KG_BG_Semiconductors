"""
test_collapse.py — Exercise exe_query's collapse logic without Ollama.

Run in the same Python session as a script that's already loaded the
graph, or standalone (it'll load once, then stay warm if you drop into
-i mode: `python -i maintenance\\test_collapse.py`).

Bypasses nl_to_sparql entirely -- writes SPARQL directly, tests
run_sparql + _collapse_to_material on real data. This is what actually
needed re-checking after the run_sparql bug; the LLM step was never
implicated.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ontology.core import g
from query.exe_query import run_sparql, _collapse_to_material, _total_entries_in_kg
from query.queryVia_formula import find_materials

if len(g) < 1000:
    t = time.perf_counter()
    g.parse(ROOT / "data" / "mse_kg_full.ttl", format="turtle")
    print(f"Loaded {len(g):,} triples ({time.perf_counter()-t:.1f}s)\n")
else:
    print(f"Graph already loaded: {len(g):,} triples\n")

SPARQL_PREFIX = """PREFIX ex: <http://example.org/mse#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

# A formula known to have many phases (from the earlier session output).
TEST_FORMULA = "ZnS"

print("=" * 70)
print(f"1. RAW QUERY — every node for {TEST_FORMULA}")
print("=" * 70)
q = f"""{SPARQL_PREFIX}
SELECT ?m ?formula ?bandgap ?crystal_system ?centro ?source_id
WHERE {{
  ?m a ex:Material .
  ?m ex:hasFormula ?formula .
  ?m ex:hasBandGap ?bandgap .
  ?m ex:hasCrystalSystem ?crystal_system .
  ?m ex:hasCentrosymmetric ?centro .
  ?m ex:hasSourceId ?source_id .
  FILTER(?formula = "{TEST_FORMULA}")
}}
LIMIT 500
"""
t = time.perf_counter()
raw = run_sparql(q)
print(f"  {len(raw)} raw node(s) returned  ({time.perf_counter()-t:.2f}s)")

print("\n" + "=" * 70)
print("2. GROUND TRUTH — direct index lookup (what show_material uses)")
print("=" * 70)
true_nodes = find_materials(TEST_FORMULA)
print(f"  {len(true_nodes)} node(s) via g.subjects(hasFormula, ...)")

print("\n" + "=" * 70)
print("3. _total_entries_in_kg() — does it match ground truth?")
print("=" * 70)
n_total = _total_entries_in_kg(TEST_FORMULA)
print(f"  _total_entries_in_kg('{TEST_FORMULA}') = {n_total}")
if n_total == len(true_nodes):
    print("  MATCH — n_total is trustworthy.")
else:
    print(f"  MISMATCH — expected {len(true_nodes)}, got {n_total}. "
          f"Investigate before trusting n_total in ask_kg output.")

print("\n" + "=" * 70)
print("4. _collapse_to_material() — on the raw query result")
print("=" * 70)
collapsed = _collapse_to_material(raw)
print(collapsed.to_string(index=False))

print("\n" + "=" * 70)
print("5. SANITY — n_matching should equal len(raw); n_total should be a")
print("   TRUE graph-wide count regardless of the LIMIT 500 above")
print("=" * 70)
if not collapsed.empty:
    row = collapsed.iloc[0]
    print(f"  n_matching = {row.get('n_matching')}  (raw query returned {len(raw)})")
    print(f"  n_total    = {row.get('n_total')}  (ground truth: {len(true_nodes)})")
    print(f"  n_phases   = {row.get('n_phases')}  "
          f"(from maintenance/phase_groups.csv, if present)")
    print(f"  representative_id = {row.get('representative_id')}")

print("\n" + "=" * 70)
print("6. A LOW-LIMIT CASE — the bug this test exists to catch")
print("=" * 70)
print("  If collapsing happened BEFORE limiting, or n_total were derived")
print("  from the window instead of a fresh index lookup, this would show")
print("  n_total == the artificially small LIMIT below instead of the")
print("  true graph-wide count.")
q_small = q.replace("LIMIT 500", "LIMIT 3")
small_raw = run_sparql(q_small)
small_collapsed = _collapse_to_material(small_raw)
if not small_collapsed.empty:
    row = small_collapsed.iloc[0]
    print(f"  window size forced to 3 -> n_matching={row.get('n_matching')}, "
          f"n_total={row.get('n_total')}")
    if row.get("n_total") == len(true_nodes):
        print("  PASS — n_total is window-independent, as intended.")
    else:
        print("  FAIL — n_total is leaking the window size. Bug.")
