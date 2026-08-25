"""
bench_sparql_cost.py — Find where the ~18s SPARQL latency actually goes.

Run from repo root:
    python -m maintenance.bench_sparql_cost
(or drop it in maintenance/ and: python maintenance/bench_sparql_cost.py)

Tests four independent hypotheses:
  A. Predicate cardinality  — is any pattern actually selective?
  B. Join width             — does cost scale linearly with pattern count?
  C. Written order          — does reordering the text change anything?
  D. ORDER BY               — how much is the sort vs. the join?

Nothing here modifies the graph.
"""

import time
from pathlib import Path

from rdflib import RDF

from ontology.core import g, EX, Material

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / "data" / "mse_kg_full.ttl"

PREFIX = """PREFIX ex: <http://example.org/mse#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""

PATTERNS = [
    ("?m a ex:Material .",                        RDF.type),
    ("?m ex:hasSourceId ?source_id .",            EX.hasSourceId),
    ("?m ex:hasCentrosymmetric ?centro .",        EX.hasCentrosymmetric),
    ("?m ex:hasCrystalSystem ?crystal_system .",  EX.hasCrystalSystem),
    ("?m ex:hasFormula ?formula .",               EX.hasFormula),
    ("?m ex:hasBandGap ?bandgap .",               EX.hasBandGap),
]


def timed(query, label):
    t = time.perf_counter()
    n = len(list(g.query(query)))
    dt = time.perf_counter() - t
    print(f"  {label:<52} {dt:7.2f}s  ({n:,} rows)")
    return dt


def main():
    if len(g) < 1000:
        t = time.perf_counter()
        g.parse(TTL, format="turtle")
        print(f"Loaded {len(g):,} triples in {time.perf_counter()-t:.1f}s\n")

    # ── A. Cardinality: is anything actually selective? ──────────────────
    print("=" * 72)
    print("A. PREDICATE CARDINALITY (index-level, no SPARQL)")
    print("=" * 72)
    for text, pred in PATTERNS:
        if pred == RDF.type:
            n = sum(1 for _ in g.triples((None, RDF.type, Material)))
        else:
            n = sum(1 for _ in g.triples((None, pred, None)))
        print(f"  {text:<52} {n:>10,} triples")
    n_bg_lt2 = sum(1 for _, _, o in g.triples((None, EX.hasBandGap, None))
                   if float(o) < 2)
    print(f"  {'(of which hasBandGap < 2)':<52} {n_bg_lt2:>10,} triples")

    # ── B. Join width: cost vs. number of patterns ───────────────────────
    print("\n" + "=" * 72)
    print("B. JOIN WIDTH — cumulative patterns, no FILTER, no ORDER BY")
    print("=" * 72)
    for k in range(1, len(PATTERNS) + 1):
        body = "\n  ".join(p[0] for p in PATTERNS[:k])
        q = f"{PREFIX}SELECT ?m WHERE {{\n  {body}\n}}"
        timed(q, f"{k} pattern(s)")

    # ── C. Written order: does the text order matter at all? ─────────────
    print("\n" + "=" * 72)
    print("C. WRITTEN ORDER — same 6 patterns + FILTER, two orderings")
    print("=" * 72)
    proj = "?m ?formula ?bandgap ?crystal_system ?centro ?source_id"

    as_generated = "\n  ".join(p[0] for p in PATTERNS) + "\n  FILTER(?bandgap < 2)"
    q1 = f"{PREFIX}SELECT {proj} WHERE {{\n  {as_generated}\n}}"
    t1 = timed(q1, "as-generated (bandgap last)")

    reordered = ("?m ex:hasBandGap ?bandgap .\n  FILTER(?bandgap < 2)\n  "
                 + "\n  ".join(p[0] for p in PATTERNS[:-1]))
    q2 = f"{PREFIX}SELECT {proj} WHERE {{\n  {reordered}\n}}"
    t2 = timed(q2, "reordered (bandgap + FILTER first)")
    print(f"  -> difference: {abs(t1-t2):.2f}s "
          f"({'reorder helps' if t2 < t1 * 0.9 else 'no meaningful effect'})")

    # ── D. ORDER BY cost, and cost of trimming the projection ───────────
    print("\n" + "=" * 72)
    print("D. ORDER BY / LIMIT / NARROWER PROJECTION")
    print("=" * 72)
    timed(f"{PREFIX}SELECT {proj} WHERE {{\n  {as_generated}\n}}\nORDER BY ASC(?bandgap)\nLIMIT 10",
          "6 patterns + FILTER + ORDER BY + LIMIT 10")
    timed(f"{PREFIX}SELECT {proj} WHERE {{\n  {as_generated}\n}}\nLIMIT 10",
          "6 patterns + FILTER + LIMIT 10 (no ORDER BY)")

    minimal = ("?m ex:hasBandGap ?bandgap .\n  ?m ex:hasFormula ?formula .\n"
               "  FILTER(?bandgap < 2)")
    timed(f"{PREFIX}SELECT ?m ?formula ?bandgap WHERE {{\n  {minimal}\n}}"
          "\nORDER BY ASC(?bandgap)\nLIMIT 10",
          "2 patterns only + FILTER + ORDER BY + LIMIT 10")

    print("\nDone. Compare B (should be ~linear in pattern count) against the")
    print("2-pattern number in D — that gap is what ensure_required costs you.")


if __name__ == "__main__":
    main()
