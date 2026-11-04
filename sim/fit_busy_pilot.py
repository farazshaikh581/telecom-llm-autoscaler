"""Fit the fast functions' busy-time factor k (sim/params.py) on real pilot arrivals (2026-10-02).
Open-loop replay (sim/validate.py) of the dev_0 pilot runs over a grid of k per function, scored on
the per-minute P95 (mean absolute log ratio, simulated to measured); checked on held-out runs
(dev_1 pilot, dev_2 tuning). The together runs alone (smooth Poisson load) gave too small a k:
real per-second bursts keep a copy busy longer.

  python sim/fit_busy_pilot.py  -> per-k table, BEST, held-out CHECK
"""
import sys, json, copy, pathlib
R = pathlib.Path.home() / "telecom-llm-autoscaler"; sys.path.insert(0, str(R / "sim"))
import numpy as np, pandas as pd
from validate import replay
from cluster_sim import PARAMS
H = {"thumbnailer": "thumbnailer_800_px_photos_60_240_kb", "graph-bfs": "graph_bfs_small_10_000"}
FIT, CHECK = ["pilot3_hpa_dev0", "pilot3_telecomgpt_dev0"], ["pilot3_hpa_dev1", "pilot3_telecomgpt_dev1", "tune3_hpa_t60_dev2", "tune3_hpa_t70_dev2"]
def params_for(k):
    p = copy.deepcopy(PARAMS)
    for e, kk in k.items():
        h = pd.read_csv(R / "results/functions/raw" / f"{H[e]}.csv").handler_ms.to_numpy()
        q = np.array(p["entries"][e]["service_ms_quantiles"])
        p["entries"][e]["busy_ms_quantiles"] = np.quantile(h * kk, np.linspace(0, 1, 201)).tolist()
        p["entries"][e]["overhead_ms_quantiles"] = np.clip(q - h.mean() * kk, 0.5, None).tolist()
        p["entries"][e]["mu_per_copy"] = 1000 / (h.mean() * kk)
    return p
def score(runs, p, e):
    d = pd.concat([replay(r, params=p) for r in runs]); d = d[d.entry == e].dropna()
    rv, sv = (d.real_p95 > d.slo), (d.sim_p95 > d.slo)
    return float(np.mean(np.abs(np.log(np.clip(d.sim_p95, 1, 1e6) / np.clip(d.real_p95, 1, 1e6))))), float(rv.mean()), float(sv.mean())
base = {"thumbnailer": PARAMS["entries"]["thumbnailer"]["busy_stretch"], "graph-bfs": PARAMS["entries"]["graph-bfs"]["busy_stretch"]}
best = {}
for e, grid in [("thumbnailer", [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0]), ("graph-bfs", [1.2, 1.4, 1.6, 1.8, 2.0, 2.2])]:
    res = []
    for kk in grid:
        k = dict(base); k[e] = kk
        err, rv, sv = score(FIT, params_for(k), e); res.append((err, kk, rv, sv))
        print(json.dumps({"entry": e, "k": kk, "log_err": round(err, 3), "real_viol": round(rv, 3), "sim_viol": round(sv, 3)}), flush=True)
    best[e] = min(res)[1]
print("BEST", best)
p = params_for(best)
for e in best:
    print("CHECK", e, [round(x, 3) for x in score(CHECK, p, e)], "(log_err, real_viol, sim_viol) | with current k:", [round(x, 3) for x in score(CHECK, PARAMS, e)])
