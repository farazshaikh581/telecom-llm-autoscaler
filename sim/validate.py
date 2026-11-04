"""Open-loop check of the simulator against real runs: replay each run's actual arrivals (per
second, per entry, from requests.csv.gz) and actual Ready copies (fast.jsonl, 5 s), then compare
the per-minute P95 (failures counted as slower than any answer) and SLO violations over the scored
minutes with the measured ones.

  python sim/validate.py [--cap <worker CPU m>] pilot_hpa_dev0 pilot_telecomgpt_dev0 ...
  -> sim/validation.csv + table
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cluster_sim import ENTRIES, ClusterSim, ROOT  # noqa: E402

WARM_MIN = 15


def replay(run, cap=None, jit=None, params=None):
    d = ROOT / "results/runs" / run
    meta = json.load(open(d / "meta.json"))
    total = meta["duration_s"]
    req = pd.read_csv(d / "requests.csv.gz", usecols=["entry", "sched_ms"])
    req["s"] = (req.sched_ms // 1000).astype(int)
    counts = {e: np.bincount(req.s[req.entry == e], minlength=total + 1)[:total + 1] for e in ENTRIES}
    fast = [json.loads(l) for l in open(d / "fast.jsonl")]
    fast = [r for r in fast if "replicas" in r]
    times = np.array([r["t"] for r in fast])
    ready = [{s: v["ready"] for s, v in r["replicas"].items()} for r in fast]
    workers = sorted({w for r in fast for v in r["pods"].values() for w in v["nodes"]})
    widx = {w: i for i, w in enumerate(workers)}
    where = [{s: [widx[w] for w, k in sorted(v["nodes"].items()) for _ in range(k)] for s, v in r["pods"].items()}
             for r in fast]
    start = meta["start_replicas"]

    def at(t, series, default):
        i = np.searchsorted(times, t, side="right") - 1
        return series[i] if i >= 0 else default

    trace = pd.DataFrame({e: np.zeros(total) for e in ENTRIES})
    sim = ClusterSim(trace, seed=1, worker_cpu_eff_m=cap, **({'params': params} if params else {}))
    if jit:                          # TeaStore warm-up override (sim/fit_jit.py)
        sim.jit = dict(jit)
    sim.arrivals_override = counts
    sim.ready_override = lambda t: at(t, ready, start)
    sim.placement_override = lambda t: at(t, where, {})
    sim.reset(start)
    sim.advance(total)
    tl = pd.read_csv(d / "timeline.csv", index_col=0)
    rows = []
    for e in ENTRIES:
        simp = sim.minute_p95(e)
        first = WARM_MIN if total // 60 > WARM_MIN else 5   # short base runs: minutes 5-15, as their SLO base
        for m in range(first, total // 60):
            real = tl.loc[m, f"{e}_p95_all_ms"] if m in tl.index else np.nan
            rows.append({"run": run, "entry": e, "minute": m, "real_p95": real, "sim_p95": simp[m],
                         "slo": sim.p["slo_ms"][e]})
    return pd.DataFrame(rows)


def main(args):
    cap = None
    if args and args[0] == "--cap":
        cap, args = float(args[1]), args[2:]
    df = pd.concat([replay(r, cap) for r in args])
    df["real_v"] = df.real_p95 > df.slo
    df["sim_v"] = df.sim_p95 > df.slo
    df.to_csv(ROOT / "sim/validation.csv", index=False)
    ok = df.dropna()
    g = ok.groupby(["entry"])
    t = pd.DataFrame({"real_viol": g.real_v.mean(), "sim_viol": g.sim_v.mean(),
                      "agree": g.apply(lambda x: (x.real_v == x.sim_v).mean(), include_groups=False),
                      "p95_ratio_median": g.apply(lambda x: np.median(np.clip(x.sim_p95, 1, 1e6) / np.clip(x.real_p95, 1, 1e6)),
                                                  include_groups=False),
                      "minutes": g.size()})
    print(t.round(3).to_string())
    per_run = ok.groupby(["run"]).agg(real_viol=("real_v", "mean"), sim_viol=("sim_v", "mean"))
    print(per_run.round(3).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
