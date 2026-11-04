"""KPIs of one run (results/runs/<name>/), written to summary.json.

Scored window: requests sent in [warm-up, end) (900 s warm-up; 60 s for short smoke tests).
Per entry (TeaStore, 3 functions, hotel):
- requests, error rate (non-200, timeouts and dropped arrivals)
- latency of successful requests: avg, P50, P75, P90, P95, P99, max (ms)
- SLO attainment: share of requests answered within the SLO (errors count as misses), k = 2, 3, 5
- SLO violation rate: share of 60 s intervals whose P95 (errors counted as slower than any answer) is over the SLO
Overall: mean violation rate over entries, Jain fairness of per-entry attainment (k = 3),
cost (replica-seconds, requested vCPU-hours, actual CPU), stability (scaling actions per hour,
reversals), energy (GPU: each GPU and their sum; host packages), decision time, invalid or late decisions.

Usage: python harness/analyze_run.py results/runs/<name>
"""
import datetime
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import CFG, SCALING, SERVICES  # noqa: E402

PCTS = [50, 75, 90, 95, 99]
# A failed request counts as slower than any answer. A finite stand-in (1e9 ms) keeps percentiles
# defined; np.inf makes interpolated percentiles NaN, and NaN never compares as over the SLO.
FAILED_MS = 1e9


def jsonl(p):
    return [json.loads(l) for l in open(p)] if p.exists() else []


def energy_wh(t, w, lo, hi):
    """Trapezoid integral of power samples (epoch s, W) over [lo, hi)."""
    m = (t >= lo) & (t < hi)
    if m.sum() < 2:
        return None
    return float(np.trapezoid(w[m], t[m]) / 3600)


def gpu_energy(path, lo, hi):
    """GPU energy over [lo, hi), per GPU and summed. nvidia-smi writes one row per GPU per sample.
    Files from before 2026-10-06 have no index column: their rows come in a fixed order (GPU 0, 1, ...)
    within each sample, so the index is the row's position in its sample (samples are about 1 s
    apart, the rows of one sample a few ms)."""
    g = pd.read_csv(path, skipinitialspace=True)
    g.columns = [c.split(" [")[0].strip() for c in g.columns]
    ts = g["timestamp"].map(lambda s: datetime.datetime.strptime(s.strip(), "%Y/%m/%d %H:%M:%S.%f").timestamp())
    if "index" not in g.columns:
        new_sample = np.r_[True, np.diff(ts.to_numpy()) > 0.5]
        g["index"] = g.groupby(np.cumsum(new_sample)).cumcount()
    per = {}
    for i, d in g.groupby("index"):
        per[str(int(i))] = energy_wh(ts[d.index].to_numpy(), pd.to_numeric(d["power.draw"], errors="coerce").to_numpy(), lo, hi)
    total = sum(v for v in per.values() if v is not None) if any(v is not None for v in per.values()) else None
    return total, per


def timeline(run, total):
    """timeline.csv: per minute of the whole run (warm-up included) and entry: requests sent, error
    share, P95 (errors counted as slower than any answer) and P95 of successful requests; plus ready copies per service."""
    d = pd.read_csv(run / "requests.csv.gz", dtype={"error": str})
    d = d[d.sched_ms < total * 1000]
    d["minute"] = (d.sched_ms // 60000).astype(int)
    d["lat_all"] = np.where(d.status == 200, d.lat_us / 1000, FAILED_MS)
    rows = []
    for (m, e), g in d.groupby(["minute", "entry"]):
        ok = g.lat_us[g.status == 200] / 1000
        rows.append({"minute": m, "entry": e, "rps": round(len(g) / 60, 2),
                     "error_share": round(float((g.status != 200).mean()), 4),
                     "p95_all_ms": round(float(np.percentile(g.lat_all, 95)), 1) if len(g) else None,
                     "p95_ok_ms": round(float(np.percentile(ok, 95)), 1) if len(ok) else None})
    t = pd.DataFrame(rows).pivot(index="minute", columns="entry")
    t.columns = [f"{e}_{k}" for k, e in t.columns]
    fast = [r for r in jsonl(run / "fast.jsonl") if "replicas" in r]
    if fast:
        rep = pd.DataFrame([{"minute": int(r["t"] // 60), **{f"ready_{s}": r["replicas"][s]["ready"] for s in SERVICES}}
                            for r in fast]).groupby("minute").mean().round(1)
        t = t.join(rep)
    t.to_csv(run / "timeline.csv")
    return t


def main(run):
    run = pathlib.Path(run)
    meta = json.load(open(run / "meta.json"))
    total = meta["duration_s"]
    warm = CFG["warmup_s"] if total >= 2 * CFG["warmup_s"] else 60
    t0 = meta["start_epoch"]
    out = {"name": meta["name"], "controller": meta["controller"], "trace": meta["trace"],
           "scored_s": [warm, total]}

    # ---- requests ----
    d = pd.read_csv(run / "requests.csv.gz", dtype={"error": str})
    d = d[(d.sched_ms >= warm * 1000) & (d.sched_ms < total * 1000)]
    d["ok"] = d.status == 200
    d["lat_ms"] = np.where(d.ok, d.lat_us / 1000, FAILED_MS)
    d["iv"] = (d.sched_ms // (CFG["interval_s"] * 1000)).astype(int)
    entries = {}
    for e, g in d.groupby("entry"):
        base = SCALING["entries"][e]["no_queue_p95_ms"]
        slo = SCALING["entries"][e]["slo_ms"]
        ok = g.lat_ms[g.ok]
        q = np.percentile(ok, PCTS) if len(ok) else [np.nan] * len(PCTS)
        per_iv = g.groupby("iv").lat_ms.quantile(0.95, interpolation="linear")
        entries[e] = {
            "requests": int(len(g)), "error_rate": round(float(1 - g.ok.mean()), 5),
            "latency_ms": {"avg": round(float(ok.mean()), 2), **{f"p{p}": round(float(v), 2) for p, v in zip(PCTS, q)},
                           "max": round(float(ok.max()), 2)},
            "slo_ms": slo,
            "attainment": {f"k{k}": round(float((g.lat_ms <= k * base).mean()), 5) for k in (2, 3, 5)},
            "violation_rate": {f"k{k}": round(float((per_iv > k * base).mean()), 4) for k in (2, 3, 5)},
            "intervals": int(len(per_iv)),
        }
    out["entries"] = entries
    att = np.array([v["attainment"]["k3"] for v in entries.values()])
    out["overall"] = {
        "violation_rate_k3": round(float(np.mean([v["violation_rate"]["k3"] for v in entries.values()])), 4),
        "attainment_k3_mean": round(float(att.mean()), 5),
        "jain_attainment_k3": round(float(att.sum() ** 2 / (len(att) * (att ** 2).sum())), 5),
        "error_rate": round(float(1 - d.ok.mean()), 5),
    }

    # ---- replicas, cost, stability ----
    fast = [r for r in jsonl(run / "fast.jsonl") if "replicas" in r and warm <= r["t"] < total]
    if fast:
        dt = CFG["recorder"]["fast_s"]
        ready = pd.DataFrame([{s: r["replicas"][s]["ready"] for s in SERVICES} for r in fast])
        desired = pd.DataFrame([{s: r["replicas"][s]["desired"] for s in SERVICES} for r in fast])
        pending = pd.DataFrame([{s: r["pods"][s]["pending"] for s in SERVICES} for r in fast])
        hours = len(fast) * dt / 3600
        changes = desired.diff().fillna(0)
        actions = int((changes != 0).sum().sum())
        reversals = 0
        for s in SERVICES:
            steps = np.sign(changes[s][changes[s] != 0].to_numpy())
            reversals += int((steps[1:] != steps[:-1]).sum())
        out["cost"] = {"replica_seconds": float(ready.sum().sum() * dt),
                       "requested_vcpu_hours": round(float(ready.sum().sum() * dt * CFG["cpu_request_m"] / 1000 / 3600), 3),
                       "mean_ready_total": round(float(ready.sum(axis=1).mean()), 2),
                       "max_ready_total": int(ready.sum(axis=1).max()),
                       "mean_ready": {s: round(float(ready[s].mean()), 2) for s in SERVICES},
                       "share_time_pending": round(float((pending.sum(axis=1) > 0).mean()), 4)}
        out["stability"] = {"scaling_actions_per_hour": round(actions / hours, 2),
                            "reversals_per_hour": round(reversals / hours, 2)}
    metrics = [r for r in jsonl(run / "metrics.jsonl") if warm <= r["t"] < total]
    if metrics:
        cpu = pd.DataFrame([{s: r["services"][s]["cpu_m"] for s in SERVICES} for r in metrics])
        out.setdefault("cost", {})["actual_cpu_mean_cores"] = round(float(cpu.sum(axis=1).mean() / 1000), 3)

    # ---- energy ----
    lo, hi = t0 + warm, t0 + total
    en = {}
    if (run / "gpu.csv").exists():
        en["gpu_wh"], per_gpu = gpu_energy(run / "gpu.csv", lo, hi)    # all GPUs together
        for i, v in per_gpu.items():
            en[f"gpu{i}_wh"] = v
    if (run / "host_power.csv").exists():
        h = pd.read_csv(run / "host_power.csv")
        if len(h):
            for c in [c for c in h.columns if c.endswith("_W")]:
                en[f"host_{c[:-2]}_wh"] = energy_wh(h["epoch"].to_numpy(), h[c].to_numpy(), lo, hi)
    out["energy"] = {k: (round(v, 2) if v is not None else None) for k, v in en.items()}

    # ---- testbed health ----
    # The recorder samples every fast_s seconds through the Kubernetes API from the control node; a
    # longer gap means the control node or its API stalled (2026-10-06: a 5.4 s pause of the whole
    # control VM, API calls of 15 s, a run's requests failing). Normal jitter is under 0.01 s (99.9% of
    # 89,101 samples); a stall of 2 s or more anywhere in the run makes it a harness failure
    # (docs/analysis_plan.md): rerun, logged. Warm-up counts too: it sets the state scoring starts from.
    fast_t = np.array([r["t"] for r in jsonl(run / "fast.jsonl") if "t" in r])
    if len(fast_t) > 2:
        extra = np.diff(fast_t) - CFG["recorder"]["fast_s"]
        scored = fast_t[1:] > warm
        st = extra[scored]
        out["health"] = {"control_stall_max_s": round(float(max(st.max(), 0)) if len(st) else 0.0, 1),
                         "control_stalls_2s": int((st >= 2).sum()),
                         "control_stalls_2s_warmup": int((extra[~scored] >= 2).sum()),
                         "harness_failure": bool((extra >= 2).any())}

    # ---- decisions ----
    dec = [r for r in jsonl(run / "decisions.jsonl")]
    if dec:
        ds = np.array([r["decide_s"] for r in dec])
        calls = [c for r in dec for c in (r["controller"] or {}).get("calls", [])]
        out["decisions"] = {"count": len(dec), "decide_s_mean": round(float(ds.mean()), 3),
                            "decide_s_p95": round(float(np.percentile(ds, 95)), 3),
                            "decide_s_max": round(float(ds.max()), 3),
                            "late_ticks": int(sum(r["tick_late_s"] > 5 for r in dec)),
                            "invalid": int(sum(1 for c in calls if "decision" not in c)),
                            "llm_calls": len(calls)}
    (run / "summary.json").write_text(json.dumps(out, indent=1))
    timeline(run, total)

    # ---- print ----
    print(f"{meta['name']}  {meta['controller']}  scored {warm}-{total} s")
    print(f"{'entry':12} {'req':>8} {'err%':>6} {'avg':>7} {'p50':>7} {'p75':>7} {'p90':>7} {'p95':>7} {'p99':>8} {'SLO':>5} {'att%':>6} {'viol%':>6}")
    for e, v in entries.items():
        l = v["latency_ms"]
        print(f"{e:12} {v['requests']:>8} {100 * v['error_rate']:>6.2f} {l['avg']:>7.1f} {l['p50']:>7.1f} {l['p75']:>7.1f} "
              f"{l['p90']:>7.1f} {l['p95']:>7.1f} {l['p99']:>8.1f} {v['slo_ms']:>5} {100 * v['attainment']['k3']:>6.2f} "
              f"{100 * v['violation_rate']['k3']:>6.1f}")
    print(json.dumps({k: out[k] for k in ("overall", "cost", "stability", "energy", "decisions") if k in out}))


if __name__ == "__main__":
    main(sys.argv[1])
