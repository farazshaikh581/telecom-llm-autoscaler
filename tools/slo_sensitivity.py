"""SLO factor sensitivity of docs/analysis_plan.md (no new runs): the violation rate of the main matrix with
SLO = k x the no-queue P95 for k = 2, 3, 5, from each run's per-minute P95 (timeline.csv, scored minutes 15-74).
k = 3 reproduces summary.json. Writes results/analysis/slo_sensitivity.csv.
Usage: .venv/bin/python tools/slo_sensitivity.py
"""
import glob

import numpy as np
import pandas as pd

SLO = {"teastore": 171, "hotel": 54, "thumbnailer": 105, "graph-bfs": 203}   # k = 3
CTRL = ["hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "tgpt_perservice", "tgpt_oracl",
        "qwen_joint", "qwen_perservice", "qwen_oracl"]

rows = []
for c in CTRL:
    for run in sorted(glob.glob(f"results/runs/test2_{c}_t*_r*")):
        t = pd.read_csv(f"{run}/timeline.csv")
        t = t[(t.minute >= 15) & (t.minute < 75)]
        r = {"controller": c, "run": run.split("/")[-1]}
        for k in (2, 3, 5):
            r[f"viol_k{k}"] = 100 * np.mean([(t[f"{a}_p95_all_ms"] > SLO[a] / 3 * k).mean() for a in SLO])
        rows.append(r)
d = pd.DataFrame(rows)
m = d.groupby("controller", sort=False)[["viol_k2", "viol_k3", "viol_k5"]].mean().round(2)
m.to_csv("results/analysis/slo_sensitivity.csv")
print(m.to_string())
