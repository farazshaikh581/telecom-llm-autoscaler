"""Compare service setups in the simulator before spending cluster time (2026-10-01).

HISTORICAL: written for the 8-service design and its sim/params.json; the simulator has read its
service lists from config/harness.yaml since the switch to 4 services, so this no longer runs as is.
Its results are in results/sim_setups/ and docs/decisions.md (2026-10-01).

Setups:
  all8     the current design: 8 scaled services
  no_comp  compression removed (no copies, no traffic)
  four     4 scaled services (TeaStore front end, hotel reservation, thumbnailer, image recognition);
           compression removed; hotel frontend, search and rate fixed at FIXED copies each
Each setup gets its own traces, scaled with the trace builder's rule (tools/traces/build_traces.py):
the busiest minute needs 125% of the copies the controller can use (24 minus fixed copies), then
load_scale. So every setup puts the same relative pressure on the cluster. HPA (CPU target 70%, as in
the pilot) drives the simulator (sim/params.json, validated on the phase-2 pilot runs) on the 3 dev
days, 3 seeds each. KPIs over the scored hour, as harness/analyze_run.py: SLO violation rate (k = 3,
mean over the apps that are present), per app, mean copies (scaled + fixed), scaling actions per
hour, Jain index over per-app SLO attainment.

  python tools/sim_setups.py  -> table, results/sim_setups/setups.csv
"""
import json
import pathlib
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/traces"))
sys.path.insert(0, str(ROOT / "sim"))
import build_traces as bt  # noqa: E402
from cluster_sim import ENTRIES, SERVICES, ClusterSim  # noqa: E402
from sim_controllers import SimHPA  # noqa: E402

HUAWEI_ZIP = pathlib.Path.home() / "trace_raw/huawei/requests_second.zip"
FIXED = 2
HOTEL_LIGHT = ["hotel-frontend", "hotel-search", "hotel-rate"]
SEEDS = [1, 2, 3]
CAP0, TARGET0 = {a: list(v) for a, v in bt.CAP.items()}, bt.TARGET   # before any setup patches them
START = json.load(open(ROOT / "results/runs/pilot2b_hpa_dev0/meta.json"))["start_replicas"]


def setup_def(name):
    cap = {a: list(v) for a, v in CAP0.items()}
    apps, fixed = list(ENTRIES), {}
    if name in ("no_comp", "four"):
        apps.remove("compression")
        cap.pop("compression")
    if name == "four":
        cap["hotel"] = [CAP0["hotel"][2]]                          # reservation only
        fixed = {s: FIXED for s in HOTEL_LIGHT}
    target = 1.25 * (bt.COPIES_AVAILABLE - sum(fixed.values()))
    return apps, cap, fixed, target


def traces(name, alibaba, zf):
    apps, cap, fixed, target = setup_def(name)
    bt.CAP, bt.TARGET = cap, target
    out = []
    for n in range(3):
        wday, hday = bt.DAYS["wikipedia"]["dev"][n], bt.DAYS["huawei"]["dev"][n]
        shapes = {"teastore": bt.wiki_shape(wday)}
        hs, _ = bt.huawei_shapes(zf, hday, bt.DAYS["huawei"]["functions"])
        shapes.update({a: s for a, s in hs.items() if a in apps})
        aday = bt.DAYS["alibaba"]["dev"][n % len(bt.DAYS["alibaba"]["dev"])]
        x = alibaba[alibaba.day == aday].sort_values("second")["rps"].to_numpy(dtype=float)
        shapes["hotel"] = x / bt.minute_peak(x)
        k, rates, info = bt.scale(shapes)
        df = pd.DataFrame({e: (rates[e] * bt.LOAD_SCALE if e in rates else np.zeros(bt.TOTAL)) for e in ENTRIES})
        out.append((f"dev_{n}", df, info))
    return out


class HPAWithFixed(SimHPA):
    """HPA on the scaled services only; fixed and removed services are left alone."""
    def __init__(self, skip, **kw):
        super().__init__(**kw)
        self.skip = set(skip)

    def tick(self, sim):
        return {s: n for s, n in super().tick(sim).items() if s not in self.skip}


def run(name, day, trace, seed):
    apps, _, fixed, _ = setup_def(name)
    start = dict(START)
    skip = set(fixed)
    if "compression" not in apps:
        start["compression"] = 0
        skip.add("compression")
    start.update(fixed)
    sim = ClusterSim(trace, seed=seed)
    ctrl = HPAWithFixed(skip, target=70)
    sim.reset(start)
    actions = 0
    duration = sim.total
    while sim.t < duration:
        sim.advance(ctrl.sync_s)
        want = ctrl.tick(sim)
        if want:
            actions += len(want)
            sim.scale(want)
    warm = sim.p["cluster"]["warmup_s"]
    per_app = {e: float(np.mean([p > sim.p["slo_ms"][e] for p in sim.minute_p95(e)[warm // 60:]])) for e in apps}
    att = np.array([1 - v for v in per_app.values()])
    return {"setup": name, "day": day, "seed": seed,
            "viol": float(np.mean(list(per_app.values()))), **{f"viol_{e}": v for e, v in per_app.items()},
            "copies": float(sum(sim.ready_log[s][warm:duration].mean() for s in SERVICES)),
            "actions_per_h": actions / ((duration - warm) / 3600),
            "jain": float(att.sum() ** 2 / (len(att) * (att ** 2).sum()))}


def main():
    zf = zipfile.ZipFile(HUAWEI_ZIP)
    alibaba = pd.read_csv(ROOT / "data/traces/alibaba_hotel_second.csv")
    rows, infos = [], []
    for name in ("all8", "no_comp", "four"):
        for day, df, info in traces(name, alibaba, zf):
            infos.append({"setup": name, "day": day, **info})
            for seed in SEEDS:
                rows.append(run(name, day, df, seed))
                print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in rows[-1].items()}), flush=True)
    out = ROOT / "results/sim_setups"
    out.mkdir(parents=True, exist_ok=True)
    t = pd.DataFrame(rows)
    t.to_csv(out / "setups.csv", index=False)
    (out / "traces.json").write_text(json.dumps(infos, indent=1))
    cols = ["viol"] + [c for c in t.columns if c.startswith("viol_")] + ["copies", "actions_per_h", "jain"]
    print(t.groupby("setup", sort=False)[cols].mean().round(3).T.to_string())


if __name__ == "__main__":
    main()
