"""
exe_query.py — NL query interface for the MSE Knowledge Graph.

TWO CHANGES, both driven by measurements rather than guesses.

1. ORDER BY is no longer applied by default.

   Benchmarked on the full graph (maintenance/bench_sparql_cost.py):
       6 patterns + FILTER + ORDER BY + LIMIT 10 : 63.88s
       6 patterns + FILTER +            LIMIT 10 :  0.06s
   ORDER BY forces rdflib to materialise and sort every match before it
   can take the top N; without it the join streams and stops at LIMIT.
   That single clause was essentially the entire query latency.

   Sorting is now opt-in via sort_by=. Note the trade-off: without an
   ORDER BY, LIMIT returns an arbitrary N of the matching set, not the
   "first" N in any meaningful sense, and repeat runs may return
   different rows. For ranking questions ("lowest band gap"), pass
   sort_by explicitly and accept the cost.

   Detecting ranking intent from the question text was considered and
   rejected: sort intent belongs in the NL->SPARQL translation the model
   already performs, not in a downstream keyword regex. Doing it properly
   means retraining the query model.

2. Results are collapsed to one row per composition, not per node.

   The old `drop_duplicates(subset=["formula"])` dated from when formula
   was node identity: it picked an arbitrary node and discarded the rest
   silently, invisibly, because formula being both identity and dedupe
   key meant nothing distinguished "the one kept" from "the ones lost".

   Node identity is now material_id; every phase is its own reachable
   node (see llm/ingest_from_txt.py). But listing every node isn't right
   either: measured via pymatgen StructureMatcher
   (maintenance/phase_match.py, primitive_cell=True, scale=True), 91.6%
   of multi-entry compositions contain genuinely distinct structures
   (phases), averaging ~2.9 phases each -- and this KG has no property
   that tells them apart (crystal_system + is_centrosymmetric alone fail
   to distinguish 28.8% of multi-entry compositions;
   maintenance/polymorphism_check.py).

   So: one representative phase is shown per composition (lowest mp-id --
   a deterministic, auditable pick, explicitly NOT a stability ranking;
   this dataset has no energy_above_hull to rank by), alongside n_matching
   / n_total / n_phases so the fold is stated, not hidden. Nothing is
   silently dropped: collapse_phases=False returns the raw one-row-per-
   node result.
"""

import re
import time
from pathlib import Path

from env.modules import *
from query.query_rules import SPARQL_PREFIX
from utils.sanitize_query import nl_to_sparql
from utils.sel_ollama import QUERY_MODEL
from utils.extract_where import _extract_where_body
from ontology.core import (g, EX, Material, hasFormula, hasBandGap,
                           hasCrystalSystem, hasCentrosymmetric, hasExternalId)
# Literal / XSD / pd come from env.modules


def run_sparql(query: str):
    """Execute SPARQL against the in-memory graph, return a DataFrame."""
    qres = g.query(query)
    cols = [str(v) for v in qres.vars]
    rows = [{str(k): (str(v) if v is not None else None)
             for k, v in zip(cols, r)} for r in qres]
    return pd.DataFrame(rows, columns=cols)


# ─── Phase report integration ──────────────────────────────────────────
# maintenance/phase_groups.csv is written by maintenance/phase_match.py
# (pymatgen StructureMatcher, primitive_cell=True). It holds the TRUE
# number of structurally distinct phases per composition. Loaded lazily
# and cached; absence is handled, not fatal.
_phase_index = None


def _load_phase_index():
    """formula -> (n_entries, n_phases, status). {} if the report is absent."""
    global _phase_index
    if _phase_index is not None:
        return _phase_index
    _phase_index = {}
    try:
        report = (Path(__file__).resolve().parent.parent
                  / "maintenance" / "phase_groups.csv")
        if report.exists():
            rep = pd.read_csv(report)
            for r in rep.itertuples(index=False):
                _phase_index[str(r.formula)] = (
                    int(r.n_entries) if pd.notna(r.n_entries) else None,
                    int(r.n_phases) if pd.notna(getattr(r, "n_phases", None)) else None,
                    str(getattr(r, "status", "")),
                )
            print(f"[phases] loaded phase report: {len(_phase_index):,} "
                  f"composition(s)")
        else:
            print(f"[phases] no phase report at {report} — phase counts "
                  f"will be omitted (run maintenance/phase_match.py)")
    except Exception as e:
        print(f"[phases] could not read phase report ({e}) — continuing "
              f"without phase counts")
    return _phase_index


def _total_entries_in_kg(formula: str) -> int:
    """
    How many nodes carry this formula, graph-wide. Direct rdflib index
    lookup on (predicate, object) -- the same fast path show_material
    uses (~0.1 ms), NOT a SPARQL query. Called only for the handful of
    rows actually returned, so cost is negligible.

    This is what makes n_total honest: collapsing happens AFTER the
    SPARQL LIMIT, so counting rows in the result window would report the
    window size, not the truth.
    """
    try:
        return sum(1 for _ in g.subjects(hasFormula,
                                         Literal(formula, datatype=XSD.string)))
    except Exception:
        return 0


def _pick_representative(grp: pd.DataFrame) -> pd.Series:
    """
    Choose one row to display, from among the MATCHING rows only.

    Rule: lowest numeric Materials Project id.

    This is NOT a stability ranking and must not be described as one.
    Stability requires energy_above_hull or formation energy; neither is
    in this dataset (the MP query fetched material_id, formula_pretty,
    band_gap, structure). Lowest mp-id is a deterministic, explainable
    heuristic -- earlier ids are generally earlier, often ICSD-derived
    entries -- and nothing stronger.

    Adding energy_above_hull to the MP fields list on the planned
    re-parse turns this into a genuine most-stable pick by changing the
    sort key on this function alone.

    Chosen from matching rows only: a globally-chosen representative
    could contradict the query's own filter.
    """
    if "m" not in grp.columns:
        return grp.iloc[0]

    def _idnum(iri):
        m = re.search(r"(\d+)", str(iri).rsplit("#", 1)[-1])
        return int(m.group(1)) if m else float("inf")

    order = grp["m"].map(_idnum).sort_values().index
    return grp.loc[order[0]]


def _collapse_to_material(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per composition.

    This is NOT the old drop_duplicates(subset=["formula"]) bug. That
    picked an arbitrary node while formula was (wrongly) node identity,
    so discarded rows were invisible everywhere. Here node identity is
    material_id, every phase is its own reachable node, and the folding
    happens at the presentation layer while REPORTING what was folded.

    Reason to fold: the KG models formula, band gap, crystal system and
    centrosymmetry, and none of those distinguishes phases of one
    composition -- measured at 28.8% of multi-entry compositions
    (maintenance/polymorphism_check.py). Emitting several rows that look
    alike with nothing explaining the difference is worse than one row
    that states how many phases it stands for.

    Columns added:
      n_matching : phases of this composition satisfying the query
      n_total    : phases of this composition in the KG (true, via index
                   lookup -- not window-limited)
      n_phases   : structurally distinct phases per StructureMatcher,
                   from the phase report; None if unavailable
    """
    if df.empty or "formula" not in df.columns:
        return df

    phase_idx = _load_phase_index()
    has_m = "m" in df.columns

    def _agg(grp):
        # include_groups=False (needed on pandas >=2.2) removes the
        # grouping column ITSELF from grp -- "formula" is not a column
        # here even though it was the groupby key. The key is still
        # available via grp.name, which the apply machinery sets
        # regardless of include_groups.
        formula = grp.name
        rep = _pick_representative(grp)

        rec = {"formula": formula}
        for col in ("bandgap", "crystal_system", "centro", "source_id"):
            if col in grp.columns:
                rec[col] = rep[col]

        rec["n_matching"] = len(grp)
        rec["n_total"] = _total_entries_in_kg(formula)

        info = phase_idx.get(str(formula))
        if info is not None:
            _, n_phases, status = info
            rec["n_phases"] = (n_phases if status == "matched" else None)
        else:
            # not in the report: either a single-entry composition (one
            # phase by construction) or outside the matched set
            rec["n_phases"] = 1 if rec["n_total"] <= 1 else None

        if has_m:
            rec["representative_id"] = str(rep["m"]).rsplit("#", 1)[-1]
        return pd.Series(rec)

    return (df.groupby("formula", dropna=False, sort=False)
              .apply(_agg, include_groups=False)
              .reset_index(drop=True))


def ask_kg(question: str,
           n: int = 10,
           window: int | None = None,
           sort_by: str | None = None,   # None | "bandgap" | "ingest"
           collapse_phases: bool = True,
           model=None):
    """
    Natural language -> SPARQL -> DataFrame.

    n        : rows to return after grouping
    window   : raw rows to fetch before grouping (defaults to n * 10, so
               grouping doesn't starve the result set). Raise it if a
               query collapses heavily and returns fewer than n rows.
    sort_by  : None (fast, arbitrary N) | "bandgap" (ASC) | "ingest" (DESC).
               Sorting forces a full materialise+sort of all matches --
               see module docstring for the measured cost.
    collapse_phases :
               True  -> one row per composition: a single representative
                        phase's values, plus n_matching/n_total/n_phases
               False -> one row per graph node (one per MP entry/phase)
    """
    model = model or QUERY_MODEL

    t = time.perf_counter()
    sparql0 = nl_to_sparql(question, model=model)
    t_llm = time.perf_counter() - t

    body = _extract_where_body(sparql0)

    projection = "?m ?formula ?bandgap ?crystal_system ?centro ?source_id"
    if sort_by == "ingest":
        projection += " ?ingest_time ?ingest_idx"

    where_block = "WHERE {\n"
    if body:
        where_block += "  " + body.replace("\n", "\n  ") + "\n"
    if sort_by == "ingest":
        # ingest metadata is provenance-only; bind it solely when sorting by it
        where_block += "  OPTIONAL { ?m ex:ingestTime ?ingest_time }\n"
        where_block += "  OPTIONAL { ?m ex:ingestIndex ?ingest_idx }\n"
    where_block += "}\n"

    if sort_by == "ingest":
        order_block = "ORDER BY DESC(?ingest_time) DESC(?ingest_idx)\n"
    elif sort_by == "bandgap":
        order_block = "ORDER BY ASC(?bandgap)\n"
    else:
        order_block = ""      # <- the 63.88s -> 0.06s change

    # Over-fetch so grouping has material to work with.
    # Collapsing to one row per composition can be aggressive (a single
    # composition may have hundreds of phases), so over-fetch generously.
    # Cheap now that ORDER BY is gone -- the join streams and stops at LIMIT.
    limit = window if window is not None else max(n * 50, 200)
    limit_block = f"LIMIT {int(limit)}\n" if limit is not None else ""

    sparql = SPARQL_PREFIX + f"SELECT {projection}\n" + where_block + order_block + limit_block
    print("SPARQL (final):\n", sparql)

    t = time.perf_counter()
    df = run_sparql(sparql)
    t_sparql = time.perf_counter() - t
    print(f"[timing] LLM {t_llm:.1f}s | SPARQL {t_sparql:.1f}s"
          f"{' (sorted)' if order_block else ' (unsorted)'}")

    if df.empty:
        return df

    n_raw = len(df)
    if collapse_phases:
        df = _collapse_to_material(df)
        folded = n_raw - len(df)
        if folded:
            print(f"[phases] {n_raw} matching phase(s) -> {len(df)} "
                  f"composition(s). One representative phase is shown per "
                  f"composition (lowest mp-id — a deterministic pick, NOT "
                  f"a stability ranking); n_matching / n_total / n_phases "
                  f"report what it stands for. collapse_phases=False for "
                  f"raw per-entry rows.")

    df = df.head(n).reset_index(drop=True)

    if not order_block:
        print("[note] unsorted — this is an arbitrary subset of matches, "
              "not the top N. Pass sort_by='bandgap' to rank (slower).")

    return df