"""History-order test (d4b, docs/decisions.md 2026-10-26): order=newest runs vs the main-matrix runs.

Pairs: ho_{m}_{mode}_t{d} with test2_{m}_{mode}_t{d}_r1 (even days) or _r2 (odd days). Per model and mode, the
mean paired difference (newest minus oldest) over the 5 days with a bootstrap interval over days, and a
Wilcoxon test, Holm-corrected within each measure. Faithfulness rates (ORACL-style) come from
results/analysis/faithfulness_calls.csv (run tools/faithfulness.py first); a day's rate is its pooled count
over its pooled total. Writes results/analysis/history_order.csv.
Usage: .venv/bin/python tools/history_order.py
"""
import numpy as np
import pandas as pd

from analyze import B, DAYS, OUT, holm, summary, wilcoxon

CELLS = [(m, mode) for m in ("tgpt", "qwen") for mode in ("joint", "oracl")]
BEHAV = {"viol": "violation rate k=3 (%)", "copies": "mean copies", "reversals_h": "reversals per hour",
         "actions_h": "scaling actions per hour"}
FAITH = {"stale": ("stale", "present_numbers", "stale share of present-tense numbers"),
         "latest": ("latest", "present_numbers", "latest share of present-tense numbers"),
         "breach_wrong": ("breach_wrong", "breach_claims", "wrong SLO-breach claims"),
         "unsupported": ("unsupported", "numbers", "unsupported numbers")}


def rep(d):
    return 1 if d % 2 == 0 else 2


def main():
    rng = np.random.default_rng(2029)
    calls = pd.read_csv(OUT / "faithfulness_calls.csv")
    rows = []
    for m, mode in CELLS:
        new = {d: summary(f"ho_{m}_{mode}_t{d}") for d in DAYS}
        old = {d: summary(f"test2_{m}_{mode}_t{d}_r{rep(d)}") for d in DAYS}
        per = {k: ([new[d][k] for d in DAYS], [old[d][k] for d in DAYS]) for k in BEHAV}
        if mode == "oracl":
            for k, (a, b, _) in FAITH.items():
                rate = {}
                for kind, run in (("oracl_newest", lambda d: f"ho_{m}_oracl_t{d}"),
                                  ("oracl", lambda d: f"test2_{m}_oracl_t{d}_r{rep(d)}")):
                    g = calls[calls.run.isin([run(d) for d in DAYS])]
                    s = g.groupby("run")[[a, b]].sum()
                    rate[kind] = [s.loc[run(d), a] / s.loc[run(d), b] if s.loc[run(d), b] else np.nan for d in DAYS]
                per[k] = (rate["oracl_newest"], rate["oracl"])
        for k, (a, b) in per.items():
            a, b = np.array(a, float), np.array(b, float)
            diff = (a - b)[~np.isnan(a - b)]
            bs = diff[rng.integers(len(diff), size=(B, len(diff)))].mean(1)
            better = int((diff < 0).sum())
            rows.append({"measure": k, "model": m, "mode": mode, "n": len(diff), "newest": np.nanmean(a),
                         "oldest": np.nanmean(b), "diff": diff.mean(), "ci_lo": np.percentile(bs, 2.5),
                         "ci_hi": np.percentile(bs, 97.5), "lower_days": f"{better}/{len(diff)}", "p": wilcoxon(diff)})
    res = pd.DataFrame(rows)
    res["p_holm"] = res.groupby("measure").p.transform(holm)
    res.to_csv(OUT / "history_order.csv", index=False)
    pd.set_option("display.width", 200)
    print(res.to_string(index=False, float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
