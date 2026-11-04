"""Build the simulator parameters (sim/params.json) from calibration data. No run of the main
matrix is used here; pilot and tuning runs are kept for validation (sim/validate.py).

Per entry:
- service: no-queue latency quantiles in the shared cluster (base_run in config/harness.yaml,
  all apps at the 45% level, evenly spaced arrivals, minutes 5-15). For the functions this is the
  service time of one request (one worker per copy); for TeaStore and hotel it is the latency
  shape that load then stretches.
- TeaStore and hotel: latency curve P95(rho) = base x (1 + a rho^b / (1 - rho)), rho = rate per
  copy / mu, fitted on the alone Poisson sweeps with 2 and 3 copies (calibration_dir/
  <entry>_r2|r3_*_poisson). TeaStore also has its unscaled persistence service as a shared
  server (mu from the 3-copy sweep, where it limits). Hotel: the bottleneck of its 4 services,
  capacities in the ratio of their CPU per request (config trace capacity factors).
- CPU per request (millicores per req/s) per scaled service: from calibration pod CPU where it
  exists, else the trace-scaling constants.
Cluster: copies that fit (config), time to Ready per service (calibration_dir/scaleup_*),
load generator timeout and in-flight limit.
TeaStore warm-up: the one set of values not from calibration. A new copy's requests are slower
for tens of seconds after it turns Ready (JIT); the scale-up calibration sent only a few probe
requests, so j0 and tau were fitted on the scale-ups of the 4 pilot runs (dev_0, dev_1) by a
grid search (j0 400-1500 ms, tau 8-60 s; best 800 ms, 40 s), and are checked on other days.

Since 2026-10-01 the entries and services come from config/harness.yaml (scaled and fixed services).
--provisional: functions whose input changed after the base run (PROVISIONAL) take their service time
from one-copy measurements (results/functions/raw/) times SHARED_FACTOR, the phase-2 ratio of
shared-cluster to one-copy P95 for functions; CPU per request from the handler time. Replaced by the
base run of the new layout.

Usage: python sim/params.py [--provisional]  -> sim/params.json
"""
import glob
import json
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from common import CFG, SCALING  # noqa: E402

CAL = ROOT / CFG["calibration_dir"]
BASE = ROOT / "results/runs" / CFG["base_run"]   # shared base run (config/harness.yaml)
QS = np.linspace(0, 1, 201)
FIFO_ENTRIES = [e for e, v in CFG["entries"].items() if v["ns"] == "sebs"]
ALL_SERVICES = {**CFG["services"], **CFG.get("fixed", {})}   # every modelled pod set (fixed: not scaled)
PROVISIONAL = {"thumbnailer": "thumbnailer_800_px_photos_60_240_kb", "graph-bfs": "graph_bfs_small_10_000"}
HANDLER = PROVISIONAL   # one-copy handler timings of the functions (results/functions/raw/)
# Busy-time factor per function, fitted on the pilot's real (bursty) arrivals, dev_0 runs; held-out check on
# dev_1 pilot and dev_2 tuning runs (2026-10-02): violations real 20.0% / sim 19.6% (thumbnailer), 14.6 / 16.3 (graph-bfs)
BUSY_STRETCH = {"thumbnailer": 1.5, "graph-bfs": 1.8}
SHARED_FACTOR = 1.8   # thumbnailer: one copy P95 66-69 ms, shared base run 111-122 ms (phase 2)
HOTEL_CPU = {"hotel-frontend": 0.38, "hotel-search": 0.30, "hotel-reservation": 0.90, "hotel-rate": 0.31}  # CPU at 450 req/s


def rel(path):
    return str(pathlib.Path(path).relative_to(ROOT))


def shared_service():
    d = pd.read_csv(BASE / "requests.csv.gz")
    d = d[(d.sched_ms >= 300e3) & (d.sched_ms < 900e3) & (d.status == 200)]
    return {e: (g.lat_us / 1000).quantile(QS).round(3).tolist() for e, g in d.groupby("entry")}


def sweep(entry, copies):
    f = sorted(glob.glob(str(CAL / f"{entry}_r{copies}_*_poisson.summary.json")))[-1]
    s = pd.DataFrame(json.load(open(f)))
    base = s.p95.iloc[0]
    return pd.DataFrame({"copies": copies, "rate": s.served_rps, "p95": s.p95, "ratio": s.p95 / base}), rel(f)


def fit_curve(points, mu_bounds):
    """P95 ratio to the low-load P95 = 1 + a rho^b / (1 - rho), rho = rate / (copies mu)."""
    def resid(x):
        mu, a, b = x
        rho = np.clip(points.rate / (points.copies * mu), 0, 0.995)
        return np.log(1 + a * rho ** b / (1 - rho)) - np.log(np.maximum(points.ratio, 1.0))
    r = least_squares(resid, x0=[np.mean(mu_bounds), 0.1, 2.0], bounds=([mu_bounds[0], 1e-4, 0.5], [mu_bounds[1], 1000, 60]))
    return dict(zip(["mu", "a", "b"], [round(float(v), 4) for v in r.x])), float(np.sqrt(np.mean(r.fun ** 2)))


def pod_cpu():
    """CPU per request (millicores per req/s of the app's entry) for every pod of the three apps,
    and the worker of each fixed (unscaled) pod, from Prometheus over minutes 5-15 of the shared
    base run. Workers are stored as w0..w3 (sorted names), not by host name."""
    svc = json.loads(subprocess.run(["kubectl", "-n", "observability", "get", "svc",
                                     "kube-prom-stack-kube-prome-prometheus", "-o", "json"],
                                    capture_output=True, text=True, check=True).stdout)
    prom = f"http://{svc['spec']['clusterIP']}:9090/api/v1/query?"
    meta = json.load(open(BASE / "meta.json"))
    q = 'sum by (namespace,pod,node) (rate(container_cpu_usage_seconds_total{namespace=~"teastore|hotel|sebs",container!=""}[10m]))'
    res = json.load(urllib.request.urlopen(prom + urllib.parse.urlencode({"query": q, "time": int(meta["start_epoch"] + 900)}),
                                           timeout=30))["data"]["result"]
    d = pd.read_csv(BASE / "requests.csv.gz", usecols=["entry", "sched_ms"])
    d = d[(d.sched_ms >= 300e3) & (d.sched_ms < 900e3)]
    rps = (d.groupby("entry").size() / 600).to_dict()
    entry_of_ns = {"teastore": "teastore", "hotel": "hotel"}
    workers = sorted({x["metric"]["node"] for x in res if x["metric"].get("node", "").startswith("worker")})
    widx = {w: i for i, w in enumerate(workers)}
    scaled = {s["deploy"]: name for name, s in ALL_SERVICES.items()}
    cpu, fixed = {}, []
    for x in res:
        ns, pod, node = x["metric"]["namespace"], x["metric"]["pod"], x["metric"].get("node")
        dep = pod.rsplit("-", 2)[0]
        if ns == "sebs":
            if dep not in scaled:
                continue            # MinIO runs on the control node
            entry = dep
        else:
            entry = entry_of_ns[ns]
        key = scaled.get(dep, f"{ns}/{dep}")
        c = cpu.setdefault(key, {"entry": entry, "cpu_m": 0.0})
        c["cpu_m"] += float(x["value"][1]) * 1000
        if key not in scaled.values() and node in widx:
            fixed.append({"name": key, "entry": entry, "worker": widx[node]})
    per_rps = {k: round(v["cpu_m"] / rps[v["entry"]], 4) for k, v in cpu.items()}
    for f in fixed:
        f["cpu_m_per_rps"] = per_rps[f["name"]]
    return per_rps, fixed, len(workers)


def fit_busy(e, latency_q):
    """Fast functions (2026-10-01): much of a request's shared-cluster latency is outside the copy
    (network, proxy, load generator), so the copy is busy only for part of it. Busy time = one-copy
    handler time x k (CPU work stretched by sharing); the rest of the base-run latency is overhead that
    does not hold the copy. k is fitted so that one simulated copy reproduces the together runs
    (P95 per load step, mean absolute log error), on a grid 1.0-2.0."""
    h = pd.read_csv(ROOT / "results/functions/raw" / f"{HANDLER[e]}.csv").handler_ms.to_numpy()
    pts = []
    for d in sorted(glob.glob(str(CAL / "together_20261001*"))):
        pts += [(x["served_rps"], x["p95"]) for x in json.load(open(f"{d}/together_{e}.summary.json"))]

    def p95(occ, over, lam, rng, T=600):
        n = rng.poisson(lam * T)
        arr = np.sort(rng.random(n) * T)
        o, v = rng.choice(occ, n) / 1000, rng.choice(over, n) / 1000
        free, lat = 0.0, np.empty(n)
        for i, a in enumerate(arr):
            free = max(a, free) + o[i]
            lat[i] = (free - a + v[i]) * 1000
        return np.percentile(lat, 95)

    if e in BUSY_STRETCH:   # fitted on real pilot arrivals (sim/fit_busy_pilot.py), preferred over the together fit
        k = BUSY_STRETCH[e]
        over = np.clip(latency_q - h.mean() * k, 0.5, None)
        return {"busy_ms_quantiles": np.quantile(h * k, QS).round(3).tolist(), "overhead_ms_quantiles": over.round(3).tolist(),
                "busy_stretch": k, "busy_fit": "sim/fit_busy_pilot.py (pilot3 dev_0 replays)", "mu_per_copy": round(1000 / float(h.mean() * k), 3)}
    best = None
    for k in np.arange(1.0, 2.01, 0.1):
        over = np.clip(latency_q - h.mean() * k, 0.5, None)
        rng = np.random.default_rng(1)
        err = float(np.mean([abs(np.log(p95(h * k, over, lam, rng) / p)) for lam, p in pts]))
        if best is None or err < best[0]:
            best = (err, round(float(k), 2), over)
    err, k, over = best
    return {"busy_ms_quantiles": np.quantile(h * k, QS).round(3).tolist(), "overhead_ms_quantiles": over.round(3).tolist(),
            "busy_stretch": k, "busy_fit_log_err": round(err, 3), "mu_per_copy": round(1000 / float(h.mean() * k), 3)}


def main():
    p = {"sources": {}, "entries": {}, "services": {}}
    service = shared_service()
    p["sources"]["service"] = f"{rel(BASE)} (minutes 5-15)"

    provisional = "--provisional" in sys.argv
    raw = {}
    for e in FIFO_ENTRIES:
        if provisional and e in PROVISIONAL:
            raw[e] = pd.read_csv(ROOT / "results/functions/raw" / f"{PROVISIONAL[e]}.csv")
            q = np.quantile(raw[e].latency_ms * SHARED_FACTOR, QS).round(3)
            p["sources"][f"service_{e}"] = f"PROVISIONAL: results/functions/raw/{PROVISIONAL[e]}.csv x {SHARED_FACTOR}"
        else:
            q = np.array(service[e])
        p["entries"][e] = {"kind": "fifo", "service_ms_quantiles": [float(v) for v in q],
                           "mu_per_copy": round(1000 / float(np.mean(q[1:-1])), 3)}
        if e in HANDLER and not provisional and glob.glob(str(CAL / "together_20261001*")):
            p["entries"][e].update(fit_busy(e, np.array(q)))
            p["sources"][f"busy_{e}"] = f"results/functions/raw/{HANDLER[e]}.csv, fitted on {rel(CAL)}/together_20261001*"

    # TeaStore: front-end copies (2-copy sweep, below the persistence limit) + persistence (3-copy sweep)
    t2, f2 = sweep("teastore", 2)
    t3, f3 = sweep("teastore", 3)
    front, err_front = fit_curve(t2, (120, 300))
    pers, err_pers = fit_curve(t3.assign(copies=1), (400, 700))
    p["entries"]["teastore"] = {"kind": "curve", "service_ms_quantiles": service["teastore"], "front": front,
                                "persistence": pers, "fit_rmse_log": [round(err_front, 3), round(err_pers, 3)]}
    p["sources"]["teastore_curve"] = [f2, f3]

    # hotel: bottleneck service; fit reservation mu on both sweeps (copies = 2 and 3 of each service)
    h = pd.concat([sweep("hotel", 2)[0], sweep("hotel", 3)[0]])
    res, err_h = fit_curve(h, (300, 900))
    mu = {s: round(res["mu"] * HOTEL_CPU["hotel-reservation"] / c, 2) for s, c in HOTEL_CPU.items()}
    p["entries"]["hotel"] = {"kind": "curve", "service_ms_quantiles": service["hotel"],
                             "curve": {"a": res["a"], "b": res["b"]}, "mu_per_copy": mu, "fit_rmse_log": round(err_h, 3)}
    p["sources"]["hotel_curve"] = [rel(f) for f in sorted(glob.glob(str(CAL / "hotel_r[23]_*_poisson.summary.json")))]

    # CPU per request (millicores per req/s of the entry) for every pod; fixed pods and their workers
    per_rps, fixed, n_workers = pod_cpu()
    p["services"] = {}
    for s, c in ALL_SERVICES.items():
        if s in raw:   # provisional: CPU-bound handler, CPU per request ~ handler time
            p["services"][s] = {"cpu_m_per_rps": round(float(raw[s].handler_ms.mean()), 3)}
        else:
            p["services"][s] = {"cpu_m_per_rps": per_rps[s]}
        if "copies" in c:
            p["services"][s]["fixed_copies"] = c["copies"]
    p["fixed_pods"] = fixed
    p["sources"]["cpu"] = "Prometheus container CPU, shared base run minutes 5-15"

    # time to Ready after a scale-up, and TeaStore warm-up (first requests slow)
    su = pd.read_csv(sorted(glob.glob(str(CAL / "scaleup_*/scaleup.csv")))[-1])
    ready = su.groupby("service").ready_s.median().round(1).to_dict()
    for s in ALL_SERVICES:
        p["services"][s]["ready_s"] = ready.get(s, ready.get(s.replace("hotel-", ""), 2.0))
    p["sources"]["ready"] = rel(sorted(glob.glob(str(CAL / "scaleup_*/scaleup.csv")))[-1])

    # a worker VM has 4 vCPUs = 2 physical cores with their hyperthreads (docs/cluster.md); with
    # hyperthreading 2 cores give about 1.3x one core's throughput each. Assumption, checked by
    # sim/validate.py (sensitivity with other values).
    p["cluster"] = {"workers": n_workers, "worker_cpu_eff_m": 2600, "copies_available": CFG["copies_available"], "min_replicas": CFG["min_replicas"],
                    "max_replicas": CFG["max_replicas"], "timeout_s": 30, "max_inflight": CFG["loadgen"]["max_inflight"],
                    "cpu_request_m": CFG["cpu_request_m"], "warmup_s": CFG["warmup_s"], "ramp_s": CFG["loadgen"]["ramp_s"],
                    "interval_s": CFG["interval_s"]}
    p["teastore_jit"] = {"j0_ms": 1500.0, "tau_s": 60.0}
    p["sources"]["teastore_jit"] = "sim/fit_jit.py on pilot3_* (4-service design), 13 scale-up minutes; grid edge, larger values no better"
    p["slo_ms"] = {e: SCALING["entries"][e]["slo_ms"] for e in SCALING["entries"]}
    p["provisional"] = provisional
    (ROOT / "sim/params.json").write_text(json.dumps(p, indent=1))
    for e, v in p["entries"].items():
        print(e, {k: v[k] for k in v if k not in ("service_ms_quantiles",)})
    print("ready", {s: v["ready_s"] for s, v in p["services"].items()})


if __name__ == "__main__":
    main()
