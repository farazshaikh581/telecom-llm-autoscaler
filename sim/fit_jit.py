"""Fit TeaStore's warm-up (JIT) term: a new front-end copy's requests take j0 x exp(-age / tau) ms
extra (sim/cluster_sim.py). Calibration sends too few requests to a new copy to fit it, so it is
fitted on real runs with TeaStore scale-ups: open-loop replay (sim/validate.py) over a grid, scored
on TeaStore's per-minute P95 in the 3 minutes after each scale-up (mean absolute log ratio of
simulated to measured). Phase 1 did this by hand on its pilot (best j0 800 ms, tau 40 s).

  python sim/fit_jit.py <run> [<run> ...]  -> grid table, best (j0_ms, tau_s)
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cluster_sim import ROOT  # noqa: E402
from validate import replay, WARM_MIN  # noqa: E402

J0 = [0, 400, 600, 800, 1000, 1200, 1500]
TAU = [8, 15, 25, 40, 60]


def scaleup_minutes(run):
    """Scored minutes within 3 min after TeaStore's Ready copies went up."""
    fast = [json.loads(l) for l in open(ROOT / "results/runs" / run / "fast.jsonl")]
    fast = [r for r in fast if "replicas" in r]
    mins, prev = set(), None
    for r in fast:
        n = r["replicas"]["teastore-webui"]["ready"]
        if prev is not None and n > prev:
            m = int(r["t"] // 60)
            mins.update(range(m, m + 3))
        prev = n
    return {m for m in mins if m >= WARM_MIN}


def main(runs):
    mins = {r: scaleup_minutes(r) for r in runs}
    print({r: len(m) for r, m in mins.items()}, "scale-up minutes")
    rows = []
    for j0 in J0:
        for tau in (TAU if j0 else [1]):
            errs = []
            for r in runs:
                df = replay(r, jit={"j0_ms": j0, "tau_s": tau})
                d = df[(df.entry == "teastore") & df.minute.isin(mins[r])].dropna()
                errs += list(np.abs(np.log(np.clip(d.sim_p95, 1, 1e6) / np.clip(d.real_p95, 1, 1e6))))
            rows.append({"j0_ms": j0, "tau_s": tau, "err": float(np.mean(errs)), "n": len(errs)})
            print(rows[-1], flush=True)
    t = pd.DataFrame(rows).sort_values("err")
    print(t.head(8).to_string(index=False))
    b = t.iloc[0]
    print(f"best: j0 {b.j0_ms:g} ms, tau {b.tau_s:g} s")


if __name__ == "__main__":
    main(sys.argv[1:])
