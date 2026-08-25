"""
describe_kg.py — Generate a materials-science description of the KG,
suitable for pasting into the Hugging Face Space README.

Computes only what the CURRENT ontology can support:
  hasFormula, hasBandGap, hasCrystalSystem, hasCentrosymmetric,
  hasExternalId, hasSourceId

Deliberately does NOT report direct/indirect gap, stability, effective
mass, mobility, or dielectric response -- none of those were scraped
from Materials Project, so any claim about them would be invented. They
are listed under planned extensions instead.

Run from repo root:
    python -m maintenance.describe_kg

Writes: maintenance/kg_description.md
"""

import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from rdflib import RDF

from ontology.core import g, EX, Material

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / "data" / "mse_kg_full.ttl"
PHASE_REPORT = ROOT / "maintenance" / "phase_groups.csv"
OUT = ROOT / "maintenance" / "kg_description.md"

# Band-gap class boundaries, in eV.
#
# CONVENTION, NOT A STANDARD. Different authors draw these lines
# differently -- "wide band gap" is variously >2, >3, or >3.4 eV in the
# literature. The ultra-wide (UWBG) boundary at 3.4 eV is the most
# commonly cited, being roughly the gap of GaN. Stated explicitly in the
# output so a reader can re-bin if they disagree.
BINS = [
    ("metallic / zero-gap (DFT)", 0.0, 0.0),
    ("narrow gap (0-1 eV)",       0.0, 1.0),
    ("mid gap (1-2.5 eV)",        1.0, 2.5),
    ("wide gap (2.5-3.4 eV)",     2.5, 3.4),
    ("ultra-wide gap (>3.4 eV)",  3.4, 999.0),
]

_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)")


def load_graph():
    if len(g) < 1000:
        t = time.perf_counter()
        g.parse(TTL, format="turtle")
        print(f"Loaded {len(g):,} triples ({time.perf_counter()-t:.1f}s)")


def collect():
    """One pass over the graph, gathering everything we need."""
    rows = []
    for m in g.subjects(RDF.type, Material):
        f = g.value(m, EX.hasFormula)
        bg = g.value(m, EX.hasBandGap)
        cs = g.value(m, EX.hasCrystalSystem)
        ce = g.value(m, EX.hasCentrosymmetric)
        rows.append({
            "formula": str(f) if f is not None else None,
            "bandgap": float(bg) if bg is not None else None,
            "crystal_system": str(cs) if cs is not None else None,
            "centro": (str(ce).lower() == "true") if ce is not None else None,
        })
    return pd.DataFrame(rows)


def classify(bg):
    if bg is None:
        return "unknown"
    if bg == 0.0:
        return BINS[0][0]
    for name, lo, hi in BINS[1:]:
        if lo < bg <= hi:
            return name
    return "unknown"


def main():
    load_graph()
    print("Collecting material properties...")
    df = collect()
    n = len(df)
    print(f"  {n:,} materials")

    df["class"] = df["bandgap"].map(classify)

    # ── element frequency ───────────────────────────────────────────
    el_counter = Counter()
    for f in df["formula"].dropna():
        el_counter.update(set(_ELEMENT_RE.findall(f)))

    # ── phase multiplicity, if the report exists ───────────────────
    phase_note = ""
    if PHASE_REPORT.exists():
        pr = pd.read_csv(PHASE_REPORT)
        matched = pr[pr["status"] == "matched"] if "status" in pr.columns else pr
        if not matched.empty and "n_phases" in matched.columns:
            one = int((matched["n_phases"] == 1).sum())
            multi = len(matched) - one
            total_phases = int(matched["n_phases"].sum())
            total_entries = int(matched["n_entries"].sum())
            phase_note = (
                f"- {len(matched):,} compositions appear more than once in the "
                f"source data and were structurally compared using pymatgen's "
                f"`StructureMatcher` (primitive-cell reduction, so supercells "
                f"and alternate cell settings do not count as different "
                f"structures).\n"
                f"- {multi:,} of them ({100*multi/len(matched):.1f}%) contain "
                f"genuinely distinct structures — different phases sharing a "
                f"chemical formula.\n"
                f"- {one:,} ({100*one/len(matched):.1f}%) resolve to a single "
                f"structure: the same phase recomputed under different "
                f"settings.\n"
                f"- Across the matched set, {total_entries:,} entries reduce to "
                f"{total_phases:,} distinct structures.\n"
            )

    cls_counts = df["class"].value_counts()
    cs_counts = df["crystal_system"].value_counts()
    # Centrosymmetry arrives from rdflib as the STRING "true"/"false"
    # (xsd:boolean literals stringified on read), never as a Python bool.
    # Comparing against the bool False silently matches nothing and would
    # report 0 non-centrosymmetric materials -- confidently wrong. Normalise
    # to a real boolean once, here, and use it everywhere below.
    _centro_bool = (df["centro"].astype(str).str.strip().str.lower()
                    .map({"true": True, "false": False}))
    df["centro_bool"] = _centro_bool

    centro_counts = df["centro"].value_counts(dropna=False)

    n_noncentro = int((_centro_bool == False).sum())
    n_centro = int((_centro_bool == True).sum())
    n_centro_unknown = int(_centro_bool.isna().sum())
    if n_centro_unknown:
        print(f"  WARNING: {n_centro_unknown:,} material(s) have an "
              f"unparseable centrosymmetry value — excluded from "
              f"centrosymmetry statistics")

    # NLO candidate cross-tab: non-centrosymmetric AND a real gap
    nlo = df[(_centro_bool == False) & (df["bandgap"] > 2.5)]
    piezo_candidates = df[(_centro_bool == False) & (df["bandgap"] > 0)]

    zero_gap = int((df["bandgap"] == 0.0).sum())

    md = []
    A = md.append

    A("# What's in this knowledge graph\n")
    A(f"A queryable RDF graph of **{n:,} inorganic crystalline materials** "
      f"sourced from the [Materials Project](https://materialsproject.org/), "
      f"with symmetry features derived using "
      f"[matminer](https://hackingmaterials.lbl.gov/matminer/) and "
      f"[pymatgen](https://pymatgen.org/). Every material carries a chemical "
      f"formula, a computed band gap, a crystal system, a centrosymmetry "
      f"flag, and a provenance tag.\n")

    # ── THE CAVEAT, FIRST ──────────────────────────────────────────
    A("## Read this before interpreting any band gap\n")
    A("**These are DFT-computed band gaps, not experimental measurements.** "
      "Materials Project values come from density functional theory, which "
      "with standard semi-local functionals (GGA/PBE) systematically "
      "*underestimates* band gaps — often by 30–50%, sometimes predicting a "
      "gap of zero for a material that is experimentally a semiconductor. "
      "This is a well-known limitation of the method (the *band gap problem*), "
      "not an error in this dataset.\n")
    A("A concrete example from this graph: **GaAs** appears with a band gap "
      "of **0.19 eV**. The experimentally accepted value is roughly **1.42 eV**. "
      "The ordering of materials is often more trustworthy than the absolute "
      "numbers — treat these values as a screening signal, not as physical "
      "constants.\n")
    A(f"Relatedly, **{zero_gap:,} materials ({100*zero_gap/n:.1f}%) carry a "
      f"band gap of exactly 0.0 eV.** Under DFT these are metallic or "
      f"semi-metallic; some fraction are semiconductors whose gap the "
      f"functional failed to open. They are retained rather than filtered, "
      f"because removing them would silently discard real materials.\n")

    # ── band gap distribution ───────────────────────────────────────
    A("## Band gap distribution\n")
    A("| Class | Materials | Share |")
    A("|---|---:|---:|")
    for name, _, _ in BINS:
        c = int(cls_counts.get(name, 0))
        A(f"| {name} | {c:,} | {100*c/n:.1f}% |")
    A("")
    A("*Class boundaries are a convention, not a standard — \"wide band gap\" "
      "is variously defined as >2, >3, or >3.4 eV in the literature. The "
      "3.4 eV ultra-wide boundary used here is roughly the gap of GaN.*\n")

    A("**What this means.** Band gap governs which photons a material "
      "absorbs and emits, and how readily it conducts. Narrow-gap materials "
      "absorb in the infrared and are of interest for thermal imaging and "
      "thermoelectrics. The 1–2.5 eV window spans the visible spectrum and "
      "contains most photovoltaic and LED absorbers. Wide and ultra-wide gap "
      "materials are transparent to visible light and sustain high electric "
      "fields before breakdown, which is what makes them useful in power "
      "electronics and UV optoelectronics.\n")

    # ── crystal systems ─────────────────────────────────────────────
    A("## Crystal systems\n")
    A("| Crystal system | Materials | Share |")
    A("|---|---:|---:|")
    for cs, c in cs_counts.items():
        A(f"| {cs} | {int(c):,} | {100*int(c)/n:.1f}% |")
    A("")
    A("**What this means.** The crystal system describes the symmetry of the "
      "unit cell. Higher-symmetry systems (cubic) tend to have isotropic "
      "properties — conductivity and optical response do not depend strongly "
      "on direction. Lower-symmetry systems (monoclinic, triclinic) are "
      "anisotropic, which can be a liability for device uniformity or an "
      "asset when directional response is the point, as in birefringent "
      "optics.\n")

    # ── centrosymmetry ──────────────────────────────────────────────
    A("## Centrosymmetry\n")
    A(f"- **Centrosymmetric:** {n_centro:,} ({100*n_centro/n:.1f}%)")
    A(f"- **Non-centrosymmetric:** {n_noncentro:,} ({100*n_noncentro/n:.1f}%)\n")
    A("**What this means, and why it's in the ontology.** A centrosymmetric "
      "crystal has an inversion centre: for every atom at position **r** "
      "there is an identical atom at **−r**. This is not a cosmetic "
      "distinction — it forbids entire classes of physical behaviour by "
      "symmetry.\n")
    A("In a centrosymmetric crystal, all even-order nonlinear optical "
      "responses vanish identically. That rules out second-harmonic "
      "generation (frequency doubling) and the linear electro-optic (Pockels) "
      "effect. Piezoelectricity — charge generated under mechanical stress — "
      "is likewise forbidden, and ferroelectricity, which requires a "
      "switchable spontaneous polarisation, is impossible.\n")
    A("So **non-centrosymmetry is a hard prerequisite** for nonlinear-optical "
      "crystals, piezoelectric sensors and actuators, and ferroelectric "
      "memories. Screening on it eliminates the majority of candidates before "
      "any expensive calculation or synthesis is attempted. Being "
      "non-centrosymmetric does not *guarantee* a strong response — it only "
      "means the response is not forbidden.\n")

    A("### Where the two properties intersect\n")
    A(f"- **{len(piezo_candidates):,} materials** are non-centrosymmetric with "
      f"a non-zero band gap — the symmetry-allowed pool for piezoelectric and "
      f"nonlinear-optical behaviour.")
    A(f"- **{len(nlo):,} materials** are non-centrosymmetric with a gap above "
      f"2.5 eV — the subset that is additionally transparent across much of "
      f"the visible range, which is the usual starting point for a "
      f"frequency-doubling crystal.\n")
    A("This intersection is the kind of query the graph exists to answer, and "
      "it is one line of SPARQL.\n")

    # ── composition ─────────────────────────────────────────────────
    A("## Composition\n")
    A(f"The graph spans **{len(el_counter)} chemical elements**. The twenty "
      f"most frequent, by number of materials containing them:\n")
    A("| Element | Materials | Element | Materials |")
    A("|---|---:|---|---:|")
    top = el_counter.most_common(20)
    for i in range(0, 20, 2):
        l, r = top[i], top[i+1]
        A(f"| {l[0]} | {l[1]:,} | {r[0]} | {r[1]:,} |")
    A("")
    A("*Element symbols are parsed from the reduced chemical formula, so this "
      "counts presence, not stoichiometric amount.*\n")

    # ── phases ──────────────────────────────────────────────────────
    if phase_note:
        A("## Polymorphism — one formula, several structures\n")
        A(phase_note)
        A("**What this means.** A chemical formula does not determine a "
          "material. The same composition can crystallise into structurally "
          "distinct phases — polymorphs — with materially different "
          "properties. Carbon as diamond and graphite is the textbook case; "
          "ZnS as sphalerite and wurtzite is the semiconductor one.\n")
        A("Node identity in this graph is therefore the Materials Project "
          "`material_id`, **not** the chemical formula. One formula maps to "
          "many nodes. This matters: an earlier version of this graph keyed "
          "nodes on formula, which silently merged distinct phases and "
          "discarded their individual crystal systems.\n")
        A("**Honest limitation:** the graph currently stores crystal system "
          "and centrosymmetry, and those two descriptors are *not sufficient* "
          "to tell every phase apart — measurably so, for a substantial "
          "fraction of multi-entry compositions. Query results are therefore "
          "reported one row per composition, with `n_total` and `n_phases` "
          "columns stating how many entries and how many distinct structures "
          "that row stands for, rather than presenting an arbitrary phase as "
          "if it were the whole story.\n")

    # ── limitations ─────────────────────────────────────────────────
    A("## What this graph cannot tell you\n")
    A("Stated plainly, because a screening tool that hides its blind spots is "
      "worse than one that doesn't:\n")
    A("- **Direct vs. indirect band gap** — not currently stored. This "
      "distinction determines whether a material can efficiently emit light, "
      "and is arguably the single most consequential missing property for "
      "optoelectronic screening. Materials Project provides it; it was not "
      "scraped in the original extraction. See planned extensions.")
    A("- **Thermodynamic stability** — energy above hull is not stored, so "
      "the graph cannot distinguish an experimentally realisable phase from a "
      "hypothetical one that appears in high-throughput calculations.")
    A("- **Experimental verification** — no flag distinguishes computed-only "
      "entries from those matched to a measured structure.")
    A("- **Transport and optical properties** — carrier effective mass, "
      "mobility, dielectric constants, refractive index: none are stored.")
    A("- **Temperature and pressure** — all values correspond to DFT ground "
      "state conditions.")
    A("- **Disorder, defects, doping** — every entry is an idealised, "
      "defect-free periodic crystal. Real semiconductors are defined by their "
      "dopants.\n")

    # ── extensions (ONLY what is actually planned) ─────────────────
    A("## Planned extensions\n")
    A("Scoped to what is achievable within a free-tier hosting budget, in "
      "priority order:\n")
    A("1. **Richer property set via a second Materials Project extraction.** "
      "The original scrape retrieved only `material_id`, `formula_pretty`, "
      "`band_gap`, and `structure`. A re-extraction adding "
      "`is_gap_direct`, `energy_above_hull`, `symmetry` (space group), "
      "`density`/`volume`/`nsites`, and magnetic ordering would resolve the "
      "direct/indirect gap question, enable stability filtering, and give the "
      "ontology enough resolution to distinguish phases that crystal system "
      "and centrosymmetry alone cannot.")
    A("2. **Ontology extension to represent phases explicitly**, so that "
      "distinct structures under one composition become individually "
      "addressable and describable rather than being folded together at "
      "presentation time.")
    A("3. **Domain expansion** — metal-organic frameworks (MOFs) and alloys, "
      "as separate ingestion paths.\n")
    A("*Each extension increases graph size and therefore hosting cost, which "
      "is the binding constraint on scope rather than effort.*\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWritten: {OUT}")
    print(f"  {len(md)} blocks, {sum(len(x) for x in md):,} chars")


if __name__ == "__main__":
    main()
