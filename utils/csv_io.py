"""
utils/csv_io.py — Single source of truth for reading this project's CSVs.

Why this exists: pandas' default na_values list is case-sensitive and
contains the literal string 'NaN'. This dataset has real materials whose
formula IS 'NaN' (sodium nitride, Na:N 1:1 — confirmed against the
structure column in maintenance/verify_null_formula.py). Any plain
pd.read_csv() on these files silently converts those formulas to missing
values, regardless of dtype=.

This bit the project in several places at once (kg_validate, kg_remediate,
parse_csv_exe, and the featurization notebook cell), each independently,
which is exactly why it took so long to spot: every stage agreed, because
every stage had the same bug.

Use read_csv_safe() instead of pd.read_csv() anywhere in this project.

'nan' (lowercase) stays on the NA list — element symbols always start
with an uppercase letter, so lowercase 'nan' can never be a real formula.
"""

import pandas as pd

# pandas' own defaults, reproduced explicitly so the one removal is visible.
DEFAULT_NA_VALUES = {
    '', '#N/A N/A', '-1.#QNAN', '#NA', 'n/a', 'NaN', 'null', '-1.#IND',
    '#N/A', '-NaN', 'NA', '<NA>', '1.#QNAN', 'nan', 'NULL', '1.#IND',
    '-nan', 'N/A', 'None',
}

# Everything pandas normally treats as missing, EXCEPT literal 'NaN'.
SAFE_NA_VALUES = sorted(DEFAULT_NA_VALUES - {'NaN'})


def read_csv_safe(path, **kwargs) -> pd.DataFrame:
    """pd.read_csv, but 'NaN' text is preserved as a string.

    Any kwargs are passed through. keep_default_na/na_values are set here
    and should not be overridden by callers.
    """
    kwargs.pop("keep_default_na", None)
    kwargs.pop("na_values", None)
    return pd.read_csv(path, keep_default_na=False,
                       na_values=SAFE_NA_VALUES, **kwargs)


def read_excel_safe(path, **kwargs) -> pd.DataFrame:
    """pd.read_excel equivalent of read_csv_safe."""
    kwargs.pop("keep_default_na", None)
    kwargs.pop("na_values", None)
    return pd.read_excel(path, keep_default_na=False,
                         na_values=SAFE_NA_VALUES, **kwargs)
