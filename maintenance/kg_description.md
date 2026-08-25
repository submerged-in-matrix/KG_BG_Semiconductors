# What's in this knowledge graph

A queryable RDF graph of **150,987 inorganic crystalline materials** sourced from the [Materials Project](https://materialsproject.org/), with symmetry features derived using [matminer](https://hackingmaterials.lbl.gov/matminer/) and [pymatgen](https://pymatgen.org/). Every material carries a chemical formula, a computed band gap, a crystal system, a centrosymmetry flag, and a provenance tag.

## Read this before interpreting any band gap

**These are DFT-computed band gaps, not experimental measurements.** Materials Project values come from density functional theory, which with standard semi-local functionals (GGA/PBE) systematically *underestimates* band gaps — often by 30–50%, sometimes predicting a gap of zero for a material that is experimentally a semiconductor. This is a well-known limitation of the method (the *band gap problem*), not an error in this dataset.

A concrete example from this graph: **GaAs** appears with a band gap of **0.19 eV**. The experimentally accepted value is roughly **1.42 eV**. The ordering of materials is often more trustworthy than the absolute numbers — treat these values as a screening signal, not as physical constants.

Relatedly, **72,640 materials (48.1%) carry a band gap of exactly 0.0 eV.** Under DFT these are metallic or semi-metallic; some fraction are semiconductors whose gap the functional failed to open. They are retained rather than filtered, because removing them would silently discard real materials.

## Band gap distribution

| Class | Materials | Share |
|---|---:|---:|
| metallic / zero-gap (DFT) | 72,640 | 48.1% |
| narrow gap (0-1 eV) | 27,076 | 17.9% |
| mid gap (1-2.5 eV) | 27,110 | 18.0% |
| wide gap (2.5-3.4 eV) | 12,278 | 8.1% |
| ultra-wide gap (>3.4 eV) | 11,883 | 7.9% |

*Class boundaries are a convention, not a standard — "wide band gap" is variously defined as >2, >3, or >3.4 eV in the literature. The 3.4 eV ultra-wide boundary used here is roughly the gap of GaN.*

**What this means.** Band gap governs which photons a material absorbs and emits, and how readily it conducts. Narrow-gap materials absorb in the infrared and are of interest for thermal imaging and thermoelectrics. The 1–2.5 eV window spans the visible spectrum and contains most photovoltaic and LED absorbers. Wide and ultra-wide gap materials are transparent to visible light and sustain high electric fields before breakdown, which is what makes them useful in power electronics and UV optoelectronics.

## Crystal systems

| Crystal system | Materials | Share |
|---|---:|---:|
| monoclinic | 34,696 | 23.0% |
| orthorhombic | 31,107 | 20.6% |
| triclinic | 26,310 | 17.4% |
| cubic | 20,094 | 13.3% |
| tetragonal | 16,707 | 11.1% |
| trigonal | 11,933 | 7.9% |
| hexagonal | 10,140 | 6.7% |

**What this means.** The crystal system describes the symmetry of the unit cell. Higher-symmetry systems (cubic) tend to have isotropic properties — conductivity and optical response do not depend strongly on direction. Lower-symmetry systems (monoclinic, triclinic) are anisotropic, which can be a liability for device uniformity or an asset when directional response is the point, as in birefringent optics.

## Centrosymmetry

- **Centrosymmetric:** 92,974 (61.6%)
- **Non-centrosymmetric:** 58,013 (38.4%)

**What this means, and why it's in the ontology.** A centrosymmetric crystal has an inversion centre: for every atom at position **r** there is an identical atom at **−r**. This is not a cosmetic distinction — it forbids entire classes of physical behaviour by symmetry.

In a centrosymmetric crystal, all even-order nonlinear optical responses vanish identically. That rules out second-harmonic generation (frequency doubling) and the linear electro-optic (Pockels) effect. Piezoelectricity — charge generated under mechanical stress — is likewise forbidden, and ferroelectricity, which requires a switchable spontaneous polarisation, is impossible.

So **non-centrosymmetry is a hard prerequisite** for nonlinear-optical crystals, piezoelectric sensors and actuators, and ferroelectric memories. Screening on it eliminates the majority of candidates before any expensive calculation or synthesis is attempted. Being non-centrosymmetric does not *guarantee* a strong response — it only means the response is not forbidden.

### Where the two properties intersect

- **34,167 materials** are non-centrosymmetric with a non-zero band gap — the symmetry-allowed pool for piezoelectric and nonlinear-optical behaviour.
- **9,429 materials** are non-centrosymmetric with a gap above 2.5 eV — the subset that is additionally transparent across much of the visible range, which is the usual starting point for a frequency-doubling crystal.

This intersection is the kind of query the graph exists to answer, and it is one line of SPARQL.

## Composition

The graph spans **86 chemical elements**. The twenty most frequent, by number of materials containing them:

| Element | Materials | Element | Materials |
|---|---:|---|---:|
| O | 79,741 | Li | 21,303 |
| Mg | 18,767 | P | 16,034 |
| S | 14,843 | Mn | 14,046 |
| Fe | 12,956 | Na | 12,359 |
| Si | 12,160 | Co | 11,105 |
| N | 11,010 | F | 10,857 |
| Cu | 9,856 | V | 9,781 |
| H | 9,140 | C | 8,687 |
| Ni | 8,265 | Ca | 8,198 |
| Ba | 8,080 | Ti | 7,726 |

*Element symbols are parsed from the reduced chemical formula, so this counts presence, not stoichiometric amount.*

## Polymorphism — one formula, several structures

- 20,690 compositions appear more than once in the source data and were structurally compared using pymatgen's `StructureMatcher` (primitive-cell reduction, so supercells and alternate cell settings do not count as different structures).
- 18,947 of them (91.6%) contain genuinely distinct structures — different phases sharing a chemical formula.
- 1,743 (8.4%) resolve to a single structure: the same phase recomputed under different settings.
- Across the matched set, 65,598 entries reduce to 60,252 distinct structures.

**What this means.** A chemical formula does not determine a material. The same composition can crystallise into structurally distinct phases — polymorphs — with materially different properties. Carbon as diamond and graphite is the textbook case; ZnS as sphalerite and wurtzite is the semiconductor one.

Node identity in this graph is therefore the Materials Project `material_id`, **not** the chemical formula. One formula maps to many nodes. This matters: an earlier version of this graph keyed nodes on formula, which silently merged distinct phases and discarded their individual crystal systems.

**Honest limitation:** the graph currently stores crystal system and centrosymmetry, and those two descriptors are *not sufficient* to tell every phase apart — measurably so, for a substantial fraction of multi-entry compositions. Query results are therefore reported one row per composition, with `n_total` and `n_phases` columns stating how many entries and how many distinct structures that row stands for, rather than presenting an arbitrary phase as if it were the whole story.

## What this graph cannot tell you

Stated plainly, because a screening tool that hides its blind spots is worse than one that doesn't:

- **Direct vs. indirect band gap** — not currently stored. This distinction determines whether a material can efficiently emit light, and is arguably the single most consequential missing property for optoelectronic screening. Materials Project provides it; it was not scraped in the original extraction. See planned extensions.
- **Thermodynamic stability** — energy above hull is not stored, so the graph cannot distinguish an experimentally realisable phase from a hypothetical one that appears in high-throughput calculations.
- **Experimental verification** — no flag distinguishes computed-only entries from those matched to a measured structure.
- **Transport and optical properties** — carrier effective mass, mobility, dielectric constants, refractive index: none are stored.
- **Temperature and pressure** — all values correspond to DFT ground state conditions.
- **Disorder, defects, doping** — every entry is an idealised, defect-free periodic crystal. Real semiconductors are defined by their dopants.

## Planned extensions

Scoped to what is achievable within a free-tier hosting budget, in priority order:

1. **Richer property set via a second Materials Project extraction.** The original scrape retrieved only `material_id`, `formula_pretty`, `band_gap`, and `structure`. A re-extraction adding `is_gap_direct`, `energy_above_hull`, `symmetry` (space group), `density`/`volume`/`nsites`, and magnetic ordering would resolve the direct/indirect gap question, enable stability filtering, and give the ontology enough resolution to distinguish phases that crystal system and centrosymmetry alone cannot.
2. **Ontology extension to represent phases explicitly**, so that distinct structures under one composition become individually addressable and describable rather than being folded together at presentation time.
3. **Domain expansion** — metal-organic frameworks (MOFs) and alloys, as separate ingestion paths.

*Each extension increases graph size and therefore hosting cost, which is the binding constraint on scope rather than effort.*
