# Extending the Ontology — Workflow

How to add properties to the KG without repeating the failure modes this
project has already hit. Written to be followed cold, months later.

**Scope discipline:** this covers only the extension you actually planned —
a second Materials Project extraction with more fields, so that phases
become distinguishable and direct/indirect gap becomes answerable. It does
not propose new domains, new models, or anything requiring paid hosting.

---

## Why extend at all — the three measured gaps

Not speculation; each was found by a diagnostic in `maintenance/`:

| Gap | Evidence | Fixed by |
|---|---|---|
| Direct vs indirect gap unanswerable | Source scrape took `band_gap` as a scalar; no k-point data exists in the dataset | `is_gap_direct` |
| Crystal system + centrosymmetry can't distinguish phases for **28.8%** of multi-entry compositions | `maintenance/polymorphism_check.py` | `symmetry` (space group) |
| Representative phase per composition picked by an arbitrary rule (lowest mp-id), explicitly *not* stability | `query/exe_query.py:_pick_representative` | `energy_above_hull` |

A fourth, softer one: compositions with dozens of entries (V₂O₅ with 58,
ZnS with 145) can't be explained without knowing which are experimentally
reported versus computationally generated — `theoretical` answers that.

---

## The binding constraint: graph size

Each property adds roughly **one triple per material** — about **150,000
triples per field**.

| State | Triples | Approx size |
|---|---:|---:|
| Current | 1,358,912 | ~55 MB |
| +3 fields | ~1.81M | ~73 MB |
| +7 fields | ~2.41M | ~97 MB |

The HF Space free tier was already flagged as RAM-tight at the *previous*
~999k triples, and cold-start KG parse is already ~60 s on the Space
(~120 s locally). **Adding all seven fields is not obviously affordable.**

**Recommended first pass — three fields only:**

1. `is_gap_direct` — unlocks a whole property class, one boolean
2. `energy_above_hull` — fixes representative selection *and* enables
   stability filtering, one float
3. `spacegroup_number` — the single most phase-discriminating value
   available, one integer

That's ~+450k triples (~+33%). Measure the Space's behaviour after this
before considering the rest.

**If RAM becomes the blocker**, the cheapest reclamation is dropping
`ex:ingestTime` and `ex:ingestIndex` — provenance-only, ~300k triples, and
no query path reads them.

---

## Phase 1 — Extract

```bash
export MP_API_KEY=...
python ontology_extension.py --dry-run
```

**Do not skip the dry run.** It fetches one chunk and prints which fields
actually came back. `mp-api` field names are not guaranteed stable across
versions; any field printing `<MISSING>` needs correcting in
`EXTENSION_FIELDS` before a full run. Cheaper to find here than after
150k records.

Also confirm `BAND_GAP_RANGE` at the top of the script matches what
produced the current dataset. The original scrape used `(0.1, 3.5)`, but
the data files are named `Bandgap_0_to_5` and the KG contains 0.0 values —
so it was widened at some point. A mismatch silently changes the material
set and breaks the join in Phase 2.

Then the real run:

```bash
python ontology_extension.py
```

Writes `data/mp_extended_<timestamp>.csv`. Read the null-count summary it
prints — a mostly-null column costs triples and returns nothing.

---

## Phase 2 — Join and validate BEFORE touching the ontology

The new CSV must join cleanly to the existing data on `material_id`.

```python
from utils.csv_io import read_csv_safe

new = read_csv_safe("data/mp_extended_<timestamp>.csv")
old = read_csv_safe("data/full_dataset_Bandgap_0_to_5_featurized.csv")

print(f"new: {len(new):,}  old: {len(old):,}")
print(f"material_id unique in new: {new['material_id'].is_unique}")
print(f"in both:    {len(set(new.material_id) & set(old.material_id)):,}")
print(f"new only:   {len(set(new.material_id) - set(old.material_id)):,}")
print(f"old only:   {len(set(old.material_id) - set(new.material_id)):,}")
```

**Use `read_csv_safe`, never plain `pd.read_csv`.** Pandas' default NA
list contains the literal string `"NaN"`, which is a real formula in this
dataset (sodium nitride). A plain read silently blanks those rows. This
already cost a debugging session once.

**Checkpoint:** `material_id` unique, overlap ≈ the full existing set. A
large "old only" group means the band gap range changed and materials were
dropped — resolve before proceeding.

Cross-check band gaps agree between old and new for shared ids. Systematic
disagreement means Materials Project has updated values since the original
scrape — decide explicitly whether to adopt the new ones, and record the
decision.

---

## Phase 3 — Extend the ontology

`ontology/core.py` — declare each new property alongside the existing five:

```python
hasDirectGap      = EX.hasDirectGap        # xsd:boolean
hasEnergyAboveHull = EX.hasEnergyAboveHull # xsd:float (eV/atom)
hasSpaceGroup     = EX.hasSpaceGroup       # xsd:integer
```

Add `RDF.Property` type, `RDFS.domain` = `Material`, and `RDFS.range`
declarations, matching the existing pattern exactly.

**Decide multiplicity now, not later.** Every property added so far is
single-valued per node, and `ingest_normalized_row` guards each with
`_has_triple`. The one property that lacked that guard (`hasBandGap`)
produced nodes carrying 400+ values before it was caught. Follow the
guarded pattern.

---

## Phase 4 — Ingest

Update `utils/llm_schema.py`'s `RowOut` with the new optional fields, then
`llm/ingest_from_txt.py`'s `ingest_normalized_row` to write them — each
guarded by `_has_triple`, mirroring `hasCrystalSystem`.

Then rebuild:

```powershell
python -m kg.SC_KG
```

**Rebuild, do not patch in place.** Identity is `material_id`; a full
rebuild from corrected sources is reproducible, whereas incremental
patching against a live graph is how the stale-report incident happened.

---

## Phase 5 — Verify

```powershell
python -m maintenance.inspect_graph
```

**Checkpoint:** surplus `+0` for every property including the new ones.
Non-zero surplus means a missing `_has_triple` guard — the exact defect
that corrupted band gaps before.

```powershell
python -m utils.kg_validate
```

Add validators for the new fields first (`is_gap_direct` ∈ {true,false};
`energy_above_hull` ≥ 0; `spacegroup_number` ∈ 1–230). An unvalidated
property is one nobody will notice going wrong.

---

## Phase 6 — Teach the model the new schema

**This is the step most likely to be skipped, and skipping it reproduces
the exact bug this project already hit twice.**

The adapter can only emit SPARQL for properties it has seen. Adding a
property to the KG without retraining means the model never queries it,
silently — the same shape as the formula-containment gap
(0/191 training examples → element filters silently dropped from every
query, undetected for weeks).

1. Extend `build_dataset_v2.py`: new templates per property, plus
   combinations with existing ones. Keep it templated — SPARQL
   correctness is mechanically checkable, so ground truth comes from
   templates, never from another model.
2. Add the corresponding rules to `NL2SPARQL_SYSTEM` **in the dataset
   generator**, which is the authoritative copy.
3. Retrain (`RETRAIN_RUNBOOK.md` — ~3 minutes on a rented GPU).
4. Verify on held-out combinations, not just eval loss. Eval loss on a
   templated dataset measures template-fitting; the v2 run scored 0.0001
   and that number meant nothing. What mattered was that an element pair
   absent from training (`Bi`+`Te`) still generated correct filters.

---

## Phase 7 — Deploy, all four pieces together

Adapter, prompt, KG, and query code are **one atomic unit**. Deploying any
subset produces silent wrongness rather than an error:

- [ ] `query_lora_adapter_vN/` → `brainteaser/Ask_Kg`
- [ ] `NL2SPARQL_SYSTEM` → `query/query_rules.py` **and** the Space's
      `app.py` — character-identical to the training prompt
- [ ] rebuilt `mse_kg_full.ttl` → Space (git-lfs; needs a **write**-scoped
      HF token)
- [ ] `phase_groups.csv` if regenerated
- [ ] query code changes ported to `app.py` **verbatim**, not
      reimplemented — an inline rewrite of the sanitizer previously
      reintroduced bugs the canonical version had already fixed

**Known stale artifact:** `brainteaser/Ask_Kg` also holds
`query_merged.Q4_K_M.gguf`, built from the v1 adapter. The Space
(transformers + PEFT) ignores it entirely, but anyone cloning the repo for
local Ollama serving gets v1 weights with a v2 prompt. Either regenerate
it when GPU time is next available, or note it in that repo's README.

---

## Verification checklist

| Check | Command | Expect |
|---|---|---|
| Fields exist | `ontology_extension.py --dry-run` | no `<MISSING>` |
| Join is clean | Phase 2 snippet | `material_id` unique, full overlap |
| No multi-valued nodes | `maintenance.inspect_graph` | surplus `+0` |
| Values valid | `utils.kg_validate` | 0 failures |
| Model uses new fields | held-out generation test | new predicates appear |
| Deployed end-to-end | live Space query | new columns populated |

---

## After extending: what becomes answerable

- **Direct/indirect gap** — currently impossible, becomes a filter
- **Stability filtering** — "stable phases only", and a real
  most-stable representative instead of the lowest-mp-id heuristic
- **Phase distinction** — space group separates structures that crystal
  system and centrosymmetry cannot, addressing the measured 28.8%
- **Experimental vs hypothetical** — explains high phase counts without
  guessing

Worth updating `describe_kg.py` afterwards so the generated description
reflects what the graph can newly say, rather than drifting from it.
