"""
queryVia_formula.py — direct rdflib index lookup by formula, no SPARQL.

CHANGE: formula is no longer a unique identifier.

Node identity moved to material_id (see llm/ingest_from_txt.py). In the
source data, 20,721 formulas cover more than one material_id -- ZnS alone
has 140 Materials Project entries. The old implementation did

    m = next(g.subjects(hasFormula, Literal(f, ...)), None)

which returned ONE arbitrary node and silently hid the rest. That was
correct while formula WAS identity; it is wrong now.

show_material() therefore returns every node carrying that formula.
Printing is capped (see `limit`) so a 140-entry formula stays readable,
but the returned DataFrame always contains all matches -- the cap is a
display concern only, never a data one.
"""

from env.modules import *
from ontology.core import g
from ontology.core import (hasBandGap, hasFormula, hasCrystalSystem,
                           hasCentrosymmetric, hasExternalId, EX)

hasSourceId = EX.hasSourceId

_PROPS = [(hasFormula,         "formula"),
          (hasExternalId,      "material_id"),
          (hasBandGap,         "bandgap"),
          (hasCrystalSystem,   "crystal_system"),
          (hasCentrosymmetric, "centro"),
          (hasSourceId,        "source_id")]


def find_materials(f: str) -> list:
    """Every node whose hasFormula matches `f`. May be empty, one, or many."""
    return list(g.subjects(hasFormula, Literal(f, datatype=XSD.string)))


def material_records(f: str) -> pd.DataFrame:
    """All matches as a DataFrame. One row per node, no printing."""
    rows = []
    for m in find_materials(f):
        rec = {"iri": str(m)}
        for p, name in _PROPS:
            vals = [str(o) for o in g.objects(m, p)]
            rec[name] = vals[0] if len(vals) == 1 else (vals or None)
        rows.append(rec)
    return pd.DataFrame(rows)


def show_material(f: str, limit: int = 10, verbose: bool = True):
    """
    Look up every material with formula `f`.

    limit   : how many to PRINT (all matches are still returned)
    verbose : False to suppress printing entirely

    Returns a DataFrame of all matches.
    """
    df = material_records(f)

    if df.empty:
        if verbose:
            print(f"No node with formula: {f}")
        return df

    if verbose:
        n = len(df)
        if n == 1:
            print(f"1 material with formula {f}:")
        else:
            print(f"{n} materials share formula {f} "
                  f"(distinct Materials Project entries):")

        shown = df.head(limit)
        for _, rec in shown.iterrows():
            print(f"\nNode: {rec['iri']}")
            for _, name in _PROPS:
                val = rec.get(name)
                print(f"  {name}: {val if val is not None else '—'}")

        if n > limit:
            print(f"\n... {n - limit} more not shown "
                  f"(all {n} returned in the DataFrame; "
                  f"raise `limit` to print more).")

        # A single formula spanning several crystal systems is worth
        # surfacing -- it's exactly what formula-as-identity used to hide.
        if n > 1 and "crystal_system" in df.columns:
            systems = sorted({str(v) for v in df["crystal_system"].dropna()
                              if not isinstance(v, list)})
            if len(systems) > 1:
                print(f"\n  NOTE: these span {len(systems)} crystal systems: "
                      f"{systems}")

    return df
