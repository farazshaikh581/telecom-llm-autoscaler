"""New-service shift (d1, docs/decisions.md 2026-10-28): image recognition added as a 5th scaled service.

Pairs: ns_{c}_t{d}_r{r} with the main-matrix run test2_{c}_t{d}_r{r} (same controller, day, repeat, seed).
(1) Image recognition: violation rate (k=3) and mean copies; Friedman over the 8 controllers (blocks: the 10
(day, repeat) pairs); Wilcoxon of each controller vs hpa70, Holm within each measure; two-level bootstrap.
(2) The four existing services: violation rate (mean over the 4 entries, as the main matrix's overall) and
copies, d1 minus main matrix per controller; Wilcoxon, Holm over the 8 within each measure; two-level bootstrap.
(3) Overall 5-entry violation rate, total copies, reversals per hour: means, reported.
Writes results/analysis/new_service_{imgrec,old4,overall}.csv.
Usage: .venv/bin/python tools/new_service.py
"""
import json

import numpy as np
import pandas as pd
from scipy import stats

from analyze import DAYS, OUT, R, REPS, boot2, holm, wilcoxon

CTRL = ["hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint"]
OLD = ["graph-bfs", "hotel", "teastore", "thumbnailer"]


def kpis(name):
    s = json.load(open(R / name / "summary.json"))
    e, c = s["entries"], s["cost"]
    img = c["mean_ready"].get("image-recognition", 0.0)
    out = {"old_viol": 100 * np.mean([e[k]["violation_rate"]["k3"] for k in OLD]),
           "old_copies": c["mean_ready_total"] - img, "viol": 100 * s["overall"]["violation_rate_k3"],
           "copies": c["mean_ready_total"], "reversals_h": s["stability"]["reversals_per_hour"]}
    if "imgrec" in e:
        out.update(img_viol=100 * e["imgrec"]["violation_rate"]["k3"], img_copies=img,
                   img_err=100 * e["imgrec"]["error_rate"])
    return out


def main():
    rng = np.random.default_rng(2030)
    idx = pd.MultiIndex.from_product([DAYS, REPS], names=["day", "rep"])
    ns = {c: pd.DataFrame([kpis(f"ns_{c}_t{d}_r{r}") for d, r in idx], index=idx) for c in CTRL}
    mm = {c: pd.DataFrame([kpis(f"test2_{c}_t{d}_r{r}") for d, r in idx], index=idx) for c in CTRL}

    rows = []
    for k in ("img_viol", "img_copies"):
        fr = stats.friedmanchisquare(*[ns[c][k] for c in CTRL]).pvalue
        for c in CTRL:
            d = ns[c][k] - ns["hpa70"][k]
            lo, hi = boot2(d, rng) if c != "hpa70" else (0.0, 0.0)
            rows.append({"kpi": k, "ctrl": c, "mean": ns[c][k].mean(), "runs_over_10": int((ns[c][k] > 10).sum())
                         if k == "img_viol" else np.nan, "diff_vs_hpa70": d.mean(), "ci_lo": lo, "ci_hi": hi,
                         "p": wilcoxon(d) if c != "hpa70" else np.nan, "friedman_p": fr})
    img = pd.DataFrame(rows)
    for k in ("img_viol", "img_copies"):
        m = (img.kpi == k) & (img.ctrl != "hpa70")
        img.loc[m, "p_holm"] = holm(img.loc[m, "p"])

    rows = []
    for k in ("old_viol", "old_copies"):
        for c in CTRL:
            d = ns[c][k] - mm[c][k]
            lo, hi = boot2(d, rng)
            rows.append({"kpi": k, "ctrl": c, "main": mm[c][k].mean(), "d1": ns[c][k].mean(), "diff": d.mean(),
                         "ci_lo": lo, "ci_hi": hi, "worse_pairs": f"{int((d > 0).sum())}/{len(d)}", "p": wilcoxon(d)})
    old = pd.DataFrame(rows)
    for k in ("old_viol", "old_copies"):
        m = old.kpi == k
        old.loc[m, "p_holm"] = holm(old.loc[m, "p"])

    ov = pd.DataFrame({c: ns[c][["viol", "copies", "reversals_h"]].mean() for c in CTRL}).T

    OUT.mkdir(exist_ok=True)
    img.to_csv(OUT / "new_service_imgrec.csv", index=False)
    old.to_csv(OUT / "new_service_old4.csv", index=False)
    ov.to_csv(OUT / "new_service_overall.csv")
    pd.set_option("display.width", 200)
    f = lambda v: f"{v:.3f}"
    print(img.to_string(index=False, float_format=f), "\n")
    print(old.to_string(index=False, float_format=f), "\n")
    print(ov.to_string(float_format=f))


if __name__ == "__main__":
    main()
