"""
check_validation_report.py — Quick diagnostic on validation_report.csv.

Run this BEFORE kg_remediate.py to sanity-check that failures are real
data corruption and not a validator that's too strict / miscalibrated
against the actual label vocabulary in your graph or CSV.
"""

import pandas as pd

df = pd.read_csv(r"data\validation_report.csv")

print("=" * 64)
print("1. FAILURES BY FIELD")
print("=" * 64)
print(df["field"].value_counts().to_string())

print("\n" + "=" * 64)
print("2. CLASSIFICATION BREAKDOWN PER FIELD")
print("=" * 64)
print(df.groupby("field")["classification"].value_counts().to_string())

print("\n" + "=" * 64)
print("3. SAMPLE 'unrecoverable' ROWS PER FIELD (5 each)")
print("=" * 64)
for field, group in df[df["classification"] == "unrecoverable"].groupby("field"):
    print(f"\n--- {field} ---")
    print(group[["external_id", "graph_value", "csv_value", "reason"]].head(5).to_string(index=False))

print("\n" + "=" * 64)
print("4. DISTINCT graph_value / csv_value SEEN FOR crystal_system & centro FAILURES")
print("=" * 64)
for field in ["crystal_system", "centro"]:
    sub = df[df["field"] == field]
    if sub.empty:
        continue
    print(f"\n--- {field}: distinct graph_value ---")
    print(sub["graph_value"].value_counts().to_string())
    print(f"\n--- {field}: distinct csv_value ---")
    print(sub["csv_value"].value_counts().to_string())
