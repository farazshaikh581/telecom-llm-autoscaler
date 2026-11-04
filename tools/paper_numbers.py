"""Every number the paper quotes, as LaTeX macros read from results/analysis/*.csv.

Usage: .venv/bin/python tools/paper_numbers.py   (writes paper/numbers.tex)
In the paper: \\N{main.hpa70.viol}. An unknown key prints a red ?key? in the PDF.
Units: violation rates and shares in %, copies as mean ready copies, p values as given.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

A = Path("results/analysis")
OUT = Path("paper/numbers.tex")
keys = {}


def fmt(v, d=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    s = f"{abs(v):.{d}f}"
    return f"$-${s}" if v < 0 and float(s) != 0 else s


def pval(p):
    if p is None or np.isnan(p):
        return "n/a"
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"


def put(key, val):
    assert key not in keys, key
    keys[key] = val


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def means(f, tag):
    df = pd.read_csv(A / f, index_col=0)
    for c, row in df.iterrows():
        put(f"{tag}.{c}.n", str(int(row.n)))
        for col in ("viol", "copies", "actions_h", "reversals_h", "gpu_wh", "host_wh"):
            put(f"{tag}.{c}.{col}", fmt(row[col]))
        put(f"{tag}.{c}.attain", fmt(row.attain, 2))
        put(f"{tag}.{c}.decide_s", fmt(row.decide_s, 2))
        put(f"{tag}.{c}.decide_p95", fmt(row.decide_p95, 2))


def comparisons(f, tag):
    for r in pd.read_csv(A / f).itertuples():
        k = f"{tag}.{r.a}.{r.b}.{r.kpi}"
        put(f"{k}.diff", fmt(r.diff))
        put(f"{k}.lo", fmt(r.ci_lo))
        put(f"{k}.hi", fmt(r.ci_hi))
        put(f"{k}.p", pval(r.p))
        put(f"{k}.ph", pval(r.p_holm))


def friedman(f, tag):
    for r in pd.read_csv(A / f).itertuples():
        put(f"{tag}.{r.kpi}.chi2", fmt(r.chi2))
        put(f"{tag}.{r.kpi}.p", "$<$0.001" if r.p < 0.001 else f"{r.p:.3f}")
        put(f"{tag}.{r.kpi}.p.sci", f"{r.p:.1e}")


means("main_means.csv", "main")
comparisons("main_comparisons.csv", "cmp")
friedman("friedman.csv", "fried")
means("high_load_means.csv", "high")
comparisons("high_load_comparisons.csv", "hcmp")
friedman("high_load_friedman.csv", "hfried")

for r in pd.read_csv(A / "robustness.csv").itertuples():   # 5-pair effect sizes, no tests
    k = f"rob.{slug(r.check)}.{r.kpi}"
    for col in ("mean_a", "mean_b", "diff", "ci_lo", "ci_hi"):
        put(f"{k}.{col.replace('ci_', '').replace('mean_', '')}", fmt(getattr(r, col)))

for r in pd.read_csv(A / "new_service_imgrec.csv").itertuples():
    k = f"ns.img.{r.ctrl}.{r.kpi.split('_')[1]}"
    put(f"{k}.mean", fmt(r.mean))
    put(f"{k}.over10", str(int(r.runs_over_10)) if not np.isnan(r.runs_over_10) else "n/a")
    put(f"{k}.diff", fmt(r.diff_vs_hpa70))
    put(f"{k}.lo", fmt(r.ci_lo))
    put(f"{k}.hi", fmt(r.ci_hi))
    put(f"{k}.ph", pval(r.p_holm))
    put(f"ns.img.friedman.{r.kpi.split('_')[1]}", pval(r.friedman_p)) if r.ctrl == "hpa70" else None
for r in pd.read_csv(A / "new_service_old4.csv").itertuples():
    k = f"ns.old.{r.ctrl}.{r.kpi.split('_')[1]}"
    for col in ("main", "d1", "diff", "ci_lo", "ci_hi"):
        put(f"{k}.{col.replace('ci_', '')}", fmt(getattr(r, col)))
    put(f"{k}.worse", str(r.worse_pairs).split("/")[0])
    put(f"{k}.ph", pval(r.p_holm))
for c, r in pd.read_csv(A / "new_service_overall.csv", index_col=0).iterrows():
    for col in ("viol", "copies", "reversals_h"):
        put(f"ns.all.{c}.{col}", fmt(r[col]))

FAITH = {"unsupported numbers": "unsup", "stale (of present-tense numbers)": "stale",
         "latest (of present-tense numbers)": "latest", "wrong SLO-breach claims": "breachwrong",
         "wrong within-SLO claims": "withinwrong", "wrong pending claims": "pendingwrong",
         "root causes supported": "rootok", "root causes with no input": "rootnoinput",
         "action consistent with reasoning (per service)": "consistent",
         "calls with a clear stated direction": "clear",
         "stale matching the oldest interval (of stale)": "staleoldest",
         "stale matching the previous interval (of stale)": "staleprev"}
for r in pd.read_csv(A / "faithfulness.csv").itertuples():
    k = f"faith.{r.corpus.replace('_', '')}.{r.model}.{FAITH[r.measure]}"
    put(f"{k}.rate", fmt(100 * r.rate))
    put(f"{k}.lo", fmt(100 * r.ci_lo))
    put(f"{k}.hi", fmt(100 * r.ci_hi))
    put(f"{k}.count", f"{r.count:,}")
    put(f"{k}.of", f"{r.of:,}")

for r in pd.read_csv(A / "history_order.csv").itertuples():
    k = f"ho.{r.model}.{r.mode}.{r.measure.replace('_', '')}"
    pct = 100 if r.measure in ("stale", "latest", "breach_wrong", "unsupported") else 1
    for col in ("newest", "oldest", "diff", "ci_lo", "ci_hi"):
        put(f"{k}.{col.replace('ci_', '')}", fmt(pct * getattr(r, col)))
    put(f"{k}.days", str(r.lower_days).split("/")[0])
    put(f"{k}.ph", pval(r.p_holm))

# run counts: scored test-day runs per family (drift references, tuning and failed runs excluded)
R = Path("results/runs")
FAM = {"main": "test2_", "high": "hl_", "newsvc": "ns_", "reworded": "pa_", "ablation": "ab_", "order": "ho_", "budget": "xb_"}
tot = 0
for k, pre in FAM.items():
    n = sum(1 for d in R.glob(pre + "*") if re.search(r"_t\d", d.name) and (d / "summary.json").exists())
    put(f"runs.{k}", str(n))
    tot += n
put("runs.total", f"{tot:,}")
calls = pd.read_csv(A / "faithfulness_calls.csv").groupby("corpus").size()
for c, n in calls.items():
    put(f"calls.{c.replace('_', '')}", f"{n:,}")
put("calls.total", f"{calls.sum():,}")
put("invalid.total", str(sum(1 for k in __import__("json").load(open("results/invalid_runs.json")) if not k.startswith("_"))))

lines = ["% Generated by tools/paper_numbers.py from results/analysis/*.csv. Do not edit by hand.",
         r"\makeatletter",
         r"\providecommand{\N}[1]{\@ifundefined{pn@#1}{\textcolor{red}{?\detokenize{#1}?}}{\@nameuse{pn@#1}}}"]
lines += [rf"\@namedef{{pn@{k}}}{{{v}}}" for k, v in sorted(keys.items())]
lines.append(r"\makeatother")
OUT.write_text("\n".join(lines) + "\n")
print(f"{len(keys)} numbers -> {OUT}")
