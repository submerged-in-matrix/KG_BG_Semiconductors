# Featurized CSV columns: material_id, formula, band_gap, structure, crystal_system, is_centrosymmetric
import re
from collections import Counter

from env.modules import *
from ontology.core import *
from utils.KG_dir import DATA
from utils.csv_io import read_csv_safe

# ─── Load ───────────────────────────────────────────────────────────────
#
# read_csv_safe, not pd.read_csv. pandas' default na_values list is
# case-sensitive and contains the literal token 'NaN' -- which is a real
# formula in this dataset (sodium nitride, Na:N 1:1, confirmed against
# the structure column via maintenance/verify_null_formula.py). A plain
# read silently blanks those formulas regardless of dtype=.
# See utils/csv_io.py for the single definition of the safe NA list.
df_raw = read_csv_safe(DATA)

# rename to stable names (will be treated as gloabal)
df = df_raw.rename(columns={
    "material_id":        "material_id",
    "formula":            "formula",
    "band_gap":            "band_gap_eV",
    "crystal_system":     "crystal_system",
    "is_centrosymmetric": "is_centrosymmetric",
}).copy()



# enforcing dtypes
for col in ["formula", "material_id", "crystal_system"]:
    if col in df.columns:
        df[col] = df[col].astype("string")

df["band_gap_eV"] = pd.to_numeric(df.get("band_gap_eV"), errors="coerce")

if "is_centrosymmetric" in df.columns:
    # normalize
    df["is_centrosymmetric"] = df["is_centrosymmetric"].map(
        lambda x: bool(int(x)) if str(x).strip() in {"1","0"} else
                  (str(x).strip().lower() == "true") if pd.notna(x) else None
    )

# ─── Backstop: recover formula from structure if it's still missing ───
#
# Second, independent layer. Even if formula comes back NA for some
# reason this specific fix doesn't cover (a future gap, a different
# corruption mode), try to derive a composition straight from the
# structure column before accepting the gap as genuine. Only fills a
# formula that pandas marked NA; never overwrites a value that's present.
_re_sites_header = re.compile(r"Sites\s*\(\d+\)", re.I)
_re_site_row = re.compile(r"^\s*\d+\s+([A-Za-z][a-z]?)\s+[-\d.Ee+]+\s+[-\d.Ee+]+\s+[-\d.Ee+]+")

def _derive_formula_from_structure(struct_text) -> str | None:
    if not isinstance(struct_text, str) or not struct_text.strip():
        return None
    lines = struct_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if _re_sites_header.search(ln))
    except StopIteration:
        return None
    species = [m.group(1) for ln in lines[start + 1:]
               if (m := _re_site_row.match(ln))]
    if not species:
        return None
    counts = Counter(species)
    return "".join(f"{el}{n if n > 1 else ''}" for el, n in sorted(counts.items()))


_recovered, _still_missing = [], []
if "formula" in df.columns and "structure" in df.columns:
    _na_formula = df["formula"].isna()
    for idx in df.index[_na_formula]:
        derived = _derive_formula_from_structure(df.at[idx, "structure"])
        mid = df.at[idx, "material_id"]
        if derived:
            df.at[idx, "formula"] = derived
            _recovered.append((mid, derived))
        else:
            _still_missing.append(mid)

# ─── 2) Helpers (consistent across the notebook) ───
def _slugify(text: str) -> str:
    text = str(text).strip().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
    text = re.sub(r"[^A-Za-z0-9_]", "_", text)
    return text

def mint_entity(label, cls: URIRef, fallback_prefix: str, idx: int):
    if label is None or (pd.isna(label) if hasattr(pd, "isna") else label is None) or str(label).strip() == "":
        safe = f"{fallback_prefix}_{idx}"
        iri  = EX[safe]
        return iri
    label_str = str(label)
    safe = _slugify(label_str)
    iri  = EX[safe]
    return iri

print("Data loaded. Rows:", len(df))
print("Columns:", list(df.columns))
if _recovered:
    print(f"Formula recovered from structure for {len(_recovered)} row(s):")
    for mid, f in _recovered:
        print(f"  {mid:<14} -> {f!r}")
if _still_missing:
    print(f"Formula still missing after structure fallback, "
          f"genuine gap(s): {_still_missing}")
