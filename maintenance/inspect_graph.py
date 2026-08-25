"""
inspect_graph.py — Graph-only census. What is ACTUALLY in the KG?

No CSV, no mint_entities import, no matminer/pandas-conflict exposure.
Only rdflib + the .ttl on disk. Read-only.

Run from repo root:
    python maintenance\\inspect_graph.py

Answers, purely from the graph:
  1. How many material nodes, and how many triples per property?
  2. Which nodes carry MORE THAN ONE value of a property, and how many?
  3. For multi-valued nodes: what do those values look like?
  4. Are there nodes whose hasFormula literal disagrees with their own IRI?
     (that would indicate two distinct formulas landing on one node)
"""

import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

from rdflib import RDF

from ontology.core import g, EX, Material

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / "data" / "mse_kg_full.ttl"

PROPS = [
    ("hasFormula",         EX.hasFormula),
    ("hasBandGap",         EX.hasBandGap),
    ("hasCrystalSystem",   EX.hasCrystalSystem),
    ("hasCentrosymmetric", EX.hasCentrosymmetric),
    ("hasSourceId",        EX.hasSourceId),
    ("hasExternalId",      EX.hasExternalId),
]


def main():
    if len(g) < 1000:
        t = time.perf_counter()
        g.parse(TTL, format="turtle")
        print(f"Loaded {len(g):,} triples in {time.perf_counter()-t:.1f}s\n")

    materials = set(g.subjects(RDF.type, Material))
    print("=" * 74)
    print("1. CENSUS")
    print("=" * 74)
    print(f"  total triples   : {len(g):,}")
    print(f"  material nodes  : {len(materials):,}\n")

    # values per node, per property
    values = {}
    for name, pred in PROPS:
        by_node = defaultdict(list)
        for s, _, o in g.triples((None, pred, None)):
            by_node[s].append(o)
        values[name] = by_node
        n_triples = sum(len(v) for v in by_node.values())
        n_nodes = len(by_node)
        surplus = n_triples - n_nodes
        print(f"  {name:<20} {n_triples:>8,} triples   {n_nodes:>8,} nodes"
              f"   surplus {surplus:>+7,}")

    print("\n" + "=" * 74)
    print("2. MULTI-VALUED NODES (how many values on one node)")
    print("=" * 74)
    for name, _ in PROPS:
        dist = Counter(len(v) for v in values[name].values())
        multi = {k: c for k, c in dist.items() if k > 1}
        total_multi = sum(multi.values())
        print(f"\n  {name}")
        print(f"    nodes with exactly 1 value : {dist.get(1, 0):,}")
        if multi:
            print(f"    nodes with >1 value        : {total_multi:,}")
            for k in sorted(multi):
                print(f"        {k} values: {multi[k]:,} node(s)")
        else:
            print(f"    nodes with >1 value        : 0")

    print("\n" + "=" * 74)
    print("3. WHAT DO MULTI-VALUED NODES LOOK LIKE?")
    print("=" * 74)
    for name in ("hasBandGap", "hasFormula", "hasCrystalSystem",
                 "hasCentrosymmetric"):
        multi_nodes = [(s, v) for s, v in values[name].items() if len(v) > 1]
        if not multi_nodes:
            print(f"\n  {name}: none.")
            continue
        multi_nodes.sort(key=lambda kv: -len(kv[1]))
        print(f"\n  {name}: {len(multi_nodes):,} node(s). Worst 5:")
        for s, v in multi_nodes[:5]:
            local = unquote(str(s).rsplit("#", 1)[-1])
            forms = [str(x) for x in values["hasFormula"].get(s, [])]
            cs = [str(x) for x in values["hasCrystalSystem"].get(s, [])]
            ext = [str(x) for x in values["hasExternalId"].get(s, [])]
            vals = sorted(str(x) for x in v)
            shown = vals if len(vals) <= 8 else vals[:8] + [f"...(+{len(vals)-8})"]
            print(f"    node {local}")
            print(f"       {name} ({len(v)}): {shown}")
            print(f"       hasFormula      : {forms}")
            print(f"       hasCrystalSystem: {cs}")
            print(f"       hasExternalId   : {ext}")

    print("\n" + "=" * 74)
    print("4. IRI vs. MATERIAL_ID-LITERAL AGREEMENT")
    print("=" * 74)
    print("  Identity moved to material_id (see llm/ingest_from_txt.py) --")
    print("  a node's IRI local-name is now derived from hasExternalId, not")
    print("  hasFormula. This checks THAT invariant: does each node's IRI")
    print("  map back to its own hasExternalId? (formula is expected to")
    print("  differ from the IRI now -- that's the intended design.)")

    def slug(text):
        import re
        t = str(text).strip().replace(" ", "_").replace("(", "").replace(")", "")
        t = t.replace("/", "_")
        return re.sub(r"[^A-Za-z0-9_]", "_", t)

    mismatched = []
    for s, lits in values["hasExternalId"].items():
        local = unquote(str(s).rsplit("#", 1)[-1])
        ids = [str(x) for x in lits]
        if any(slug(i) != local for i in ids) or len(set(ids)) > 1:
            mismatched.append((local, ids))
    print(f"\n  nodes where material_id literal(s) disagree with IRI: "
          f"{len(mismatched):,}  (expect 0)")
    if mismatched:
        print("  Examples:")
        for local, ids in mismatched[:15]:
            print(f"    IRI '{local}'  <-  literals {sorted(set(ids))}")

    # Formula plurality is now EXPECTED, not a defect -- report it
    # separately so it isn't confused with the check above.
    multi_formula_nodes = {s: v for s, v in values["hasFormula"].items()
                           if len(v) > 1}
    print(f"\n  (for reference) nodes with >1 hasFormula literal: "
          f"{len(multi_formula_nodes):,}  -- covered by section 2/3 above,")
    print(f"  not a bug: it means two genuinely different formula strings")
    print(f"  were written for one material_id (e.g. the slugify-collision")
    print(f"  case, Ag(NO)3 vs AgNO3) -- worth spot-checking, not expected")
    print(f"  to be zero by design the way section 4's IRI check is.")

    print("\n" + "=" * 74)
    print("Graph-only pass complete. Next step is to explain these numbers")
    print("against the source CSV -- but only after they're established here.")


if __name__ == "__main__":
    main()