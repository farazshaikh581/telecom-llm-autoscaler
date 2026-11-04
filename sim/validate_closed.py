"""Closed-loop check: HPA driving the simulator vs HPA driving the real cluster, same trace, start
sizes and CPU target. KPIs as harness/analyze_run.py (scored minutes after the warm-up): SLO
violation rate (k = 3, mean over entries), mean Ready copies, scaling actions per hour.
Several simulator seeds show the spread from random arrivals.

  python sim/validate_closed.py pilot_hpa_dev0 pilot_hpa_dev1 tune_hpa_t50_dev2 ...
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cluster_sim import ENTRIES, ROOT, SCALED as SERVICES, ClusterSim  # noqa: E402
from sim_controllers import SimHPA, run_closed_loop  # noqa: E402

SEEDS = [1, 2, 3]


def sim_kpis(trace_path, target, start, duration, seed):
    trace = pd.read_csv(ROOT / trace_path)
    sim = ClusterSim(trace, seed=seed)
    actions = run_closed_loop(sim, SimHPA(target=target), start, duration)
    warm = sim.p["cluster"]["warmup_s"]
    viol = np.mean([np.mean([p > sim.p["slo_ms"][e] for p in sim.minute_p95(e)[warm // 60:]]) for e in ENTRIES])
    copies = sum(sim.ready_log[s][warm:duration].mean() for s in SERVICES)
    return {"violation_k3": viol, "copies_mean": copies}


def main(runs):
    rows = []
    for run in runs:
        d = ROOT / "results/runs" / run
        meta, summ = json.load(open(d / "meta.json")), json.load(open(d / "summary.json"))
        target = int(meta["controller"].split("target=")[1].split(",")[0])
        sims = [sim_kpis(meta["trace"], target, meta["start_replicas"], meta["duration_s"], s) for s in SEEDS]
        rows.append({"run": run, "target": target,
                     "real_viol": summ["overall"]["violation_rate_k3"],
                     "sim_viol": np.mean([x["violation_k3"] for x in sims]),
                     "sim_viol_range": f"{min(x['violation_k3'] for x in sims):.3f}-{max(x['violation_k3'] for x in sims):.3f}",
                     "real_copies": summ["cost"]["mean_ready_total"],
                     "sim_copies": np.mean([x["copies_mean"] for x in sims])})
        print(rows[-1], flush=True)
    t = pd.DataFrame(rows)
    t.round(3).to_csv(ROOT / "sim/validation_closed.csv", index=False)
    print(t.round(3).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
