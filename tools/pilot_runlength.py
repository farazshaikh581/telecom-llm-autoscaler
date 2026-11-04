"""Run-length pilot: KPIs over growing scored windows (first W minutes after warm-up).

Same definitions as harness/analyze_run.py: violation rate = share of 60 s intervals whose P95
(failed requests counted as slower than any answer) is over k x no-queue P95; attainment = share of
requests within k x no-queue P95. k = 3. Also a moving-block bootstrap CI (block 5 min) of the
full-window violation rate, for the within-run precision.

Usage: python tools/pilot_runlength.py [prefix]  -> results/<prefix>/runlength.csv, results/<prefix>/runlength_ci.json
(prefix "pilot" = phase 1, default; "pilot2" = second testbed)
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from common import CFG, SCALING  # noqa: E402

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "pilot"
RUNS = [f"{PREFIX}_{c}_dev{d}" for d in (0, 1) for c in ("hpa", "telecomgpt")]
K = 3
FAILED_MS = 1e9
WINDOWS = list(range(5, 61, 5))


def per_minute(run):
    """Per scored minute and entry: violated (0/1), requests, requests within SLO; mean ready copies."""
    meta = json.load(open(run / "meta.json"))
    warm, total = CFG["warmup_s"], meta["duration_s"]
    d = pd.read_csv(run / "requests.csv.gz", usecols=["entry", "sched_ms", "lat_us", "status"])
    d = d[(d.sched_ms >= warm * 1000) & (d.sched_ms < total * 1000)]
    d["lat_ms"] = np.where(d.status == 200, d.lat_us / 1000, FAILED_MS)
    d["m"] = ((d.sched_ms - warm * 1000) // (CFG["interval_s"] * 1000)).astype(int)
    base = {e: SCALING["entries"][e]["no_queue_p95_ms"] for e in d.entry.unique()}
    d["lim"] = d.entry.map(base) * K
    g = d.groupby(["entry", "m"])
    pm = pd.DataFrame({"p95": g.lat_ms.quantile(0.95, interpolation="linear"),
                       "n": g.size(), "within": g.apply(lambda x: (x.lat_ms <= x.lim).sum(), include_groups=False),
                       "lim": g.lim.first()}).reset_index()
    pm["viol"] = (pm.p95 > pm.lim).astype(int)
    fast = [json.loads(l) for l in open(run / "fast.jsonl")]
    fast = [r for r in fast if "replicas" in r and warm <= r["t"] < total]
    cop = pd.DataFrame([{"m": int((r["t"] - warm) // 60), "copies": sum(v["ready"] for v in r["replicas"].values())}
                        for r in fast]).groupby("m").copies.mean()
    return pm, d, cop


def main():
    rows, ci = [], {}
    rng = np.random.default_rng(0)
    for name in RUNS:
        pm, d, cop = per_minute(ROOT / "results/runs" / name)
        for w in WINDOWS:
            s = pm[pm.m < w]
            dd = d[d.m < w]
            row = {"run": name, "window_min": w,
                   "violation_rate": s.groupby("entry").viol.mean().mean(),
                   "attainment": (s.groupby("entry").within.sum() / s.groupby("entry").n.sum()).mean(),
                   "mean_copies": cop[cop.index < w].mean()}
            for e, g in dd.groupby("entry"):
                ok = g.lat_ms[g.lat_ms < FAILED_MS]
                row[f"p95_{e}"] = float(np.percentile(ok, 95))
                row[f"viol_{e}"] = s[s.entry == e].viol.mean()
            rows.append(row)
        # moving-block bootstrap over minutes of the full window
        v = pm.pivot(index="m", columns="entry", values="viol").mean(axis=1).to_numpy()
        n, b = len(v), 5
        starts = rng.integers(0, n - b + 1, size=(10000, n // b))
        boots = np.array([v[np.concatenate([np.arange(s, s + b) for s in st])].mean() for st in starts])
        ci[name] = {"violation_rate": float(v.mean()), "ci95": [float(np.percentile(boots, 2.5)),
                                                                float(np.percentile(boots, 97.5))]}
    out = ROOT / "results" / PREFIX
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).round(4).to_csv(out / "runlength.csv", index=False)
    json.dump(ci, open(out / "runlength_ci.json", "w"), indent=1)


if __name__ == "__main__":
    main()
