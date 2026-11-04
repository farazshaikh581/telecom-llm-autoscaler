"""Statistics of docs/analysis_plan.md, from the runs' summary.json files.

Main matrix: per controller the mean of each KPI over the 10 (test day, repeat) pairs; the mean paired
difference to the reference with a two-level bootstrap 95% interval (test days, then repeats within each
day; 10,000 draws); a Friedman test over the 12 controllers; Wilcoxon signed-rank tests per comparison,
Holm-corrected within each of the three families. Robustness checks (5 pairs): mean paired difference with
a bootstrap interval over test days, no test. +25% load (10 pairs, 2026-10-23): the main matrix's tests
over its 8 controllers. The budget-aware variant (exploratory) is reported apart.
Writes results/analysis/*.csv and prints the tables.
Usage: .venv/bin/python tools/analyze.py
"""
import json
import pathlib

import numpy as np
import pandas as pd
from scipy import stats

R = pathlib.Path("results/runs")
OUT = pathlib.Path("results/analysis")
DAYS, REPS, B = range(5), (1, 2), 10_000
CTRL = ["hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "tgpt_perservice", "tgpt_oracl",
        "qwen_joint", "qwen_perservice", "qwen_oracl"]
KPI = {"viol": "violation rate k=3 (%)", "copies": "mean copies"}


def summary(name):
    p = R / name / "summary.json"
    if not p.exists():
        return None
    s = json.load(open(p))
    d = s["decisions"]
    return {"viol": 100 * s["overall"]["violation_rate_k3"], "copies": s["cost"]["mean_ready_total"],
            "attain": 100 * s["overall"]["attainment_k3_mean"],
            "jain": s["overall"]["jain_attainment_k3"], "actions_h": s["stability"]["scaling_actions_per_hour"],
            "reversals_h": s["stability"]["reversals_per_hour"], "gpu_wh": s["energy"]["gpu_wh"],
            "host_wh": s["energy"]["host_pkg_total_wh"], "decide_s": d["decide_s_mean"],
            "decide_p95": d["decide_s_p95"], "late": d["late_ticks"], "invalid": d["invalid"]}


def table(names):
    """names: {(day, rep): run name} -> DataFrame indexed by (day, rep); missing runs dropped."""
    rows = {k: summary(n) for k, n in names.items()}
    return pd.DataFrame({k: v for k, v in rows.items() if v}).T.astype(float)


def boot2(diff, rng):
    """Two-level bootstrap of the mean of a Series indexed by (day, rep)."""
    by_day = [diff.xs(d, level=0).to_numpy() for d in sorted(set(diff.index.get_level_values(0)))]
    m = np.empty(B)
    for b in range(B):
        days = rng.integers(len(by_day), size=len(by_day))
        m[b] = np.mean(np.concatenate([by_day[i][rng.integers(len(by_day[i]), size=len(by_day[i]))] for i in days]))
    return np.percentile(m, [2.5, 97.5])


def wilcoxon(diff):
    if np.allclose(diff, 0):
        return 1.0
    return stats.wilcoxon(diff, zero_method="wilcox").pvalue


def holm(p):
    p = np.asarray(p, float)
    order = np.argsort(p)
    adj, run = np.empty_like(p), 0.0
    for i, j in enumerate(order):
        run = max(run, min(1.0, (len(p) - i) * p[j]))
        adj[j] = run
    return adj


def compare(data, pairs, family, rng):
    rows = []
    for a, b in pairs:
        for k in KPI:
            d = (data[a][k] - data[b][k]).dropna()
            lo, hi = boot2(d, rng)
            rows.append({"family": family, "kpi": k, "a": a, "b": b, "n": len(d), "mean_a": data[a][k].mean(),
                         "mean_b": data[b][k].mean(), "diff": d.mean(), "ci_lo": lo, "ci_hi": hi,
                         "p": wilcoxon(d)})
    df = pd.DataFrame(rows)
    for k in KPI:
        m = df.kpi == k
        df.loc[m, "p_holm"] = holm(df.loc[m, "p"])
    df["called"] = (df.p_holm < 0.05) & ((df.ci_lo > 0) | (df.ci_hi < 0))
    return df


def robust(pairs, rng):
    """pairs: list of (label, {day: run a}, {day: run b}); 5-pair effect sizes, bootstrap over days."""
    rows = []
    for label, ra, rb in pairs:
        ta, tb = table({(d, 1): n for d, n in ra.items()}), table({(d, 1): n for d, n in rb.items()})
        if ta.empty or tb.empty:   # not run yet
            continue
        for k in KPI:
            d = (ta[k] - tb[k]).dropna()
            m = np.array([d.to_numpy()[rng.integers(len(d), size=len(d))].mean() for _ in range(B)]) if len(d) else []
            lo, hi = np.percentile(m, [2.5, 97.5]) if len(d) else (np.nan, np.nan)
            rows.append({"check": label, "kpi": k, "n": len(d), "mean_a": ta[k].mean(), "mean_b": tb[k].mean(),
                         "diff": d.mean(), "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows)


def main():
    rng = np.random.default_rng(2026)
    OUT.mkdir(parents=True, exist_ok=True)
    data = {c: table({(d, r): f"test2_{c}_t{d}_r{r}" for d in DAYS for r in REPS}) for c in CTRL}

    # per-controller means (all KPIs)
    means = pd.DataFrame({c: data[c].mean() for c in CTRL}).T
    means.insert(0, "n", [len(data[c]) for c in CTRL])
    means.to_csv(OUT / "main_means.csv")

    # Friedman over the 12 controllers, blocks = (day, repeat)
    fried = []
    for k in KPI:
        m = pd.concat({c: data[c][k] for c in CTRL}, axis=1).dropna()
        st = stats.friedmanchisquare(*[m[c] for c in CTRL])
        fried.append({"kpi": k, "blocks": len(m), "chi2": st.statistic, "p": st.pvalue})
    fried = pd.DataFrame(fried)
    fried.to_csv(OUT / "friedman.csv", index=False)

    llm = [("tgpt_" + m, "qwen_" + m) for m in ("joint", "perservice", "oracl")]
    llm += [(f"{x}_{a}", f"{x}_{b}") for x in ("tgpt", "qwen")
            for a, b in (("joint", "perservice"), ("joint", "oracl"), ("perservice", "oracl"))]
    comp = pd.concat([compare(data, [(c, "hpa70") for c in CTRL if c != "hpa70"], "1 vs HPA70", rng),
                      compare(data, llm, "2 LLMs", rng),
                      compare(data, [("ours", "aware"), ("ours", "firm")], "3 ours vs RL", rng)])
    comp.to_csv(OUT / "main_comparisons.csv", index=False)

    # robustness checks, 5 pairs
    hl = ["hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint"]
    checks = [(f"+25% {c} vs normal", {d: f"hl_{c}_t{d}" for d in DAYS}, {d: f"test2_{c}_t{d}_r1" for d in DAYS})
              for c in hl]
    checks += [(f"+25% {c} vs HPA70", {d: f"hl_{c}_t{d}" for d in DAYS}, {d: f"hl_hpa70_t{d}" for d in DAYS})
               for c in hl if c != "hpa70"]
    for m in ("tgpt", "qwen"):
        base = {d: f"test2_{m}_joint_t{d}_r1" for d in DAYS}
        checks.append((f"{m} reworded vs default", {d: f"pa_{m}_joint_t{d}" for d in DAYS}, base))
        checks += [(f"{m} {v} vs default", {d: f"ab_{m}_{v}_t{d}" for d in DAYS}, base)
                   for v in ("zeroshot", "history", "domain", "cot")]
    # budget-aware variant (exploratory): repeat 1, repeat 2 and +25% load, against the main agent and HPA70
    for tag, xb, rep in (("r1", "xb_ours_t{d}", 1), ("r2", "xb_ours_t{d}_r2", 2)):
        for c in ("ours", "hpa70", "keda"):
            checks.append((f"budget {tag} vs {c}", {d: xb.format(d=d) for d in DAYS},
                           {d: f"test2_{c}_t{d}_r{rep}" for d in DAYS}))
    for c in ("ours", "hpa70", "keda", "aware"):
        checks.append((f"budget +25% vs {c}", {d: f"xb_hl_t{d}" for d in DAYS}, {d: f"hl_{c}_t{d}" for d in DAYS}))
    rob = robust(checks, rng)
    rob.to_csv(OUT / "robustness.csv", index=False)

    # +25% load, 10 pairs (repeat 1: hl_*_t{d}, repeat 2: hl_*_t{d}_r2): same tests as the main matrix
    # over its 8 controllers; the budget-aware variant (exploratory) in a family of its own
    hdat = {c: table({(d, r): f"hl_{c}_t{d}" + ("_r2" if r == 2 else "") for d in DAYS for r in REPS})
            for c in hl}
    hdat["budget"] = table({(d, r): f"xb_hl_t{d}" + ("_r2" if r == 2 else "") for d in DAYS for r in REPS})
    hmeans = pd.DataFrame({c: hdat[c].mean() for c in hdat}).T
    hmeans.insert(0, "n", [len(hdat[c]) for c in hdat])
    hmeans.to_csv(OUT / "high_load_means.csv")
    hfried = []
    for k in KPI:
        m = pd.concat({c: hdat[c][k] for c in hl}, axis=1).dropna()
        st = stats.friedmanchisquare(*[m[c] for c in hl])
        hfried.append({"kpi": k, "blocks": len(m), "chi2": st.statistic, "p": st.pvalue})
    hfried = pd.DataFrame(hfried)
    hfried.to_csv(OUT / "high_load_friedman.csv", index=False)
    for c in hl:   # +25% minus normal load, same (day, repeat)
        hdat["normal_" + c] = data[c]
    hcomp = pd.concat([compare(hdat, [(c, "hpa70") for c in hl if c != "hpa70"], "H1 vs HPA70", rng),
                       compare(hdat, [(c, "normal_" + c) for c in hl], "H2 +25% vs normal", rng),
                       compare(hdat, [("budget", c) for c in ("hpa70", "ours", "keda", "aware")],
                               "H3 budget (exploratory)", rng)])
    hcomp.to_csv(OUT / "high_load_comparisons.csv", index=False)

    pd.set_option("display.width", 200, "display.max_rows", 200)
    f = lambda x: f"{x:.2f}"
    print("== main matrix means\n", means[["n", "viol", "copies", "actions_h", "reversals_h", "decide_s", "gpu_wh",
                                           "host_wh"]].round(2).to_string())
    print("\n== Friedman\n", fried.to_string(index=False, float_format=lambda x: f"{x:.3g}"))
    print("\n== comparisons\n", comp[["family", "kpi", "a", "b", "n", "diff", "ci_lo", "ci_hi", "p", "p_holm",
                                      "called"]].to_string(index=False, float_format=f))
    print("\n== robustness (5 pairs, effect sizes)\n", rob.to_string(index=False, float_format=f))
    print("\n== +25% load means (10 pairs)\n", hmeans[["n", "viol", "copies", "actions_h", "reversals_h"]]
          .round(2).to_string())
    print("\n== +25% load Friedman\n", hfried.to_string(index=False, float_format=lambda x: f"{x:.3g}"))
    print("\n== +25% load comparisons\n", hcomp[["family", "kpi", "a", "b", "n", "diff", "ci_lo", "ci_hi", "p",
                                               "p_holm", "called"]].to_string(index=False, float_format=f))


if __name__ == "__main__":
    main()
