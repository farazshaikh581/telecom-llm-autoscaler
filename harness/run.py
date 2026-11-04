"""Run one controller through one trace on the shared cluster.

  python harness/run.py --controller hpa:target=70 --trace data/traces/runs/dev_0.csv.gz --name pilot_hpa_dev0

Steps:
1. Preflight: nodes Ready, no leftover autoscaler objects, host swap unused, load generator built.
2. Reset (the hotel registry cleaner runs from here to the end, harness/registry_cleaner.py):
   every scaled service to its start size (copies needed for the trace's first minute
   at SLO capacity, the same for every controller); then hotel bookings from earlier runs
   removed, booking cache and reservation restarted; wait until Ready, settle 30 s.
3. Start recorder and load generator (t = 0), then controller.setup().
4. Every 60 s: observe, decide, apply. Decision time is measured; a decision that runs past
   the next tick delays it (logged).
5. End of trace: stop, teardown, fetch host power, write summary (harness/analyze_run.py).
Results: results/runs/<name>/ (meta.json, decisions.jsonl, fast.jsonl, metrics.jsonl,
requests.csv.gz, gpu.csv, host_power.csv, loadgen.log, summary.json).
"""
import argparse
import gzip
import json
import math
import os
import pathlib
import signal
import subprocess
import sys
import time

import pandas as pd

import cluster
import controllers
from common import CFG, ROOT, SERVICES, Clock, JsonLog, kubectl, kubectl_json
from controllers.hpa import remove_all as remove_hpas
from controllers.keda import remove_all as remove_scaledobjects
from controllers.phpa import remove_all as remove_phpas
from loadgen import BIN, LoadGen
from observe import Observer
from recorder import Recorder, host_address
from registry_cleaner import Cleaner

sys.path.insert(0, str(ROOT / "tools/traces"))
from build_traces import SCALED_CAP  # noqa: E402  per-copy capacity of each app's scaled service



def start_replicas(trace, scale=1.0):
    """Copies each service needs for the trace's first minute at SLO capacity (rounded up)."""
    first = trace.iloc[:60].mean() * scale
    out = {}
    for name, s in SERVICES.items():
        cap = SCALED_CAP[s["entry"]]
        out[name] = cluster.clamp(math.ceil(first[s["entry"]] / cap))
    return out


def reset_hotel_data(log):
    """Same hotel state for every run: bookings made by earlier runs are removed (the seed booking
    stays) and the booking cache is restarted. With the seed-once image (workloads/hotel), sample
    data must be exactly 80 hotels; more means copies inserted it again."""
    def mongo(svc, db, js):
        return kubectl("-n", "hotel", "exec", f"deploy/mongodb-{svc}", "--", "mongo", "--quiet", db, "--eval", js).strip()
    for svc, db, col in [("geo", "geo-db", "geo"), ("profile", "profile-db", "hotels"),
                         ("reservation", "reservation-db", "number")]:
        n = int(mongo(svc, db, f"db.{col}.count()"))
        assert n == 80, f"hotel {db}.{col} holds {n} documents, expected 80 (duplicated seed data)"
    removed = mongo("reservation", "reservation-db",
                    "var f = db.reservation.find().sort({_id: 1}).limit(1).next();"
                    "db.reservation.deleteMany({_id: {$ne: f._id}}).deletedCount")
    kubectl("-n", "hotel", "rollout", "restart", "deploy/memcached-reserve")
    kubectl("-n", "hotel", "rollout", "status", "deploy/memcached-reserve", "--timeout=120s", timeout=150)
    # reservation keeps pooled connections to the old cache and panics on the first broken pipe;
    # the frontend's gRPC resolver can get stuck on reservation copies that are gone (test_phpa_t3:
    # "bad resolver state", every search failed), so it starts fresh too
    for dep in ("reservation", "frontend"):
        kubectl("-n", "hotel", "rollout", "restart", f"deploy/{dep}")
    for dep in ("reservation", "frontend"):
        kubectl("-n", "hotel", "rollout", "status", f"deploy/{dep}", "--timeout=120s", timeout=150)
    log(f"hotel data reset: {removed} bookings from earlier runs removed, booking cache, reservation and frontend restarted")


def teastore_broken():
    """TeaStore web copies that fail a category page (HTTP 500 while the home page works). One copy
    created during xb_ours_t2_r2 failed this way for 16 h and served every TeaStore request of two
    runs alone (2026-10-23, docs/decisions.md)."""
    import urllib.error
    import urllib.request
    bad = []
    for p in kubectl_json("-n", "teastore", "get", "pods")["items"]:
        name, ip = p["metadata"]["name"], p["status"].get("podIP")
        if not name.startswith("teastore-webui") or p["status"].get("phase") != "Running" or not ip:
            continue
        try:
            code = urllib.request.urlopen(f"http://{ip}:8080/tools.descartes.teastore.webui/category?category=2&page=1",
                                          timeout=10).status
        except urllib.error.HTTPError as e:
            code = e.code
        except OSError:
            code = None
        if code != 200:
            bad.append(name)
    return bad


def fix_teastore(log):
    for _ in range(3):
        bad = teastore_broken()
        if not bad:
            return
        log(f"TeaStore web copies failing a category page, deleted: {bad}")
        kubectl("-n", "teastore", "delete", "pod", *bad, "--wait=true", timeout=150)
        kubectl("-n", "teastore", "rollout", "status", "deploy/teastore-webui", "--timeout=180s", timeout=200)
    assert not teastore_broken(), "TeaStore web copies still failing after 3 replacements"


def vm_swap(host):
    """Swapped-out memory per VM on the host, in MiB, by role (control, w0..w3; names mapped by
    ~/.config/telecom-llm-autoscaler/vms.json, kept out of the repo). Read-only; logged at the start
    and end of every run, so a run where a worker VM was swapped can be found (docs/cluster.md)."""
    cmd = ('for p in $(pgrep qemu); do n=$(tr "\\0" " " < /proc/$p/cmdline | grep -o "guest=[A-Za-z0-9_]*"); '
           'echo "${n#guest=} $(awk \'/VmSwap/ {print $2}\' /proc/$p/status)"; done')
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd], capture_output=True, text=True, timeout=30).stdout
    try:
        roles = json.load(open(pathlib.Path.home() / ".config/telecom-llm-autoscaler/vms.json"))
    except OSError:
        roles = {}
    return {roles.get(n, "other"): round(int(kb) / 1024) for n, kb in (l.split() for l in out.splitlines() if len(l.split()) == 2)}


def preflight(log):
    nodes = kubectl_json("get", "nodes")["items"]
    ready = [n["metadata"]["name"] for n in nodes
             if any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"])]
    assert len(ready) >= 1 + CFG["workers"], f"expected control node + {CFG['workers']} workers Ready, got {ready}"
    for ns in sorted({s["ns"] for s in SERVICES.values()}):
        left = kubectl_json("-n", ns, "get", "hpa")["items"]
        assert not left, f"leftover HPA objects in {ns}: {[h['metadata']['name'] for h in left]}"
    assert BIN.exists(), f"build the load generator first: {BIN}"
    host = host_address()
    if host:
        swap = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "free", "-m"], capture_output=True, text=True,
                              timeout=30).stdout.splitlines()
        used = int(next(l for l in swap if l.startswith("Swap")).split()[2])
        # swap activity over 10 s (vmstat si/so, pages in/out per second): what would stall a guest
        vm = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "vmstat", "1", "11"], capture_output=True, text=True,
                            timeout=60).stdout.splitlines()[3:]
        active = sum(int(l.split()[6]) + int(l.split()[7]) for l in vm)
        log(f"host swap used: {used} MiB, swap in+out over 10 s: {active}")
        limit = CFG["host_swap_limit_mib"]
        waited = 0
        while used >= limit and waited < 3 * 3600:   # wait (no load running) for swapoff/swapon on the host
            if waited % 600 == 0:
                log(f"waiting for host swap reset: {used} MiB in use (docs/cluster.md)")
            time.sleep(30)
            waited += 30
            used = int(next(l for l in subprocess.run(["ssh", "-o", "BatchMode=yes", host, "free", "-m"],
                                                      capture_output=True, text=True, timeout=30).stdout.splitlines()
                            if l.startswith("Swap")).split()[2])
        if waited:
            log(f"host swap now {used} MiB after waiting {waited} s")
        assert used < limit, f"host swap {used} MiB >= {limit} MiB: run swapoff -a && swapon -a on the host first (docs/cluster.md)"
        log(f"VM swap at start (MiB): {vm_swap(host)}")
    else:
        log("no host address configured: host power and swap check skipped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--controller", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--duration", type=int, default=0, help="seconds (0 = whole trace), for smoke tests")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every trace rate (capacity tests)")
    ap.add_argument("--arrivals", default="poisson", choices=["poisson", "even"], help="even: no-queue baselines")
    a = ap.parse_args()

    out = ROOT / "results/runs" / a.name
    out.mkdir(parents=True, exist_ok=False)
    events = JsonLog(out / "events.jsonl")

    def log(msg):
        print(time.strftime("%H:%M:%S"), msg, flush=True)
        events.write({"epoch": round(time.time(), 2), "msg": msg})

    trace = pd.read_csv(a.trace)
    total = a.duration or len(trace)
    ctrl = controllers.make(a.controller)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "harness", "config"],
                                capture_output=True, text=True).stdout.strip())

    preflight(log)
    remove_scaledobjects()
    remove_phpas()
    remove_hpas()
    cleaner = Cleaner(log=lambda rec: events.write(rec)).start()
    start = start_replicas(trace, a.scale)
    log(f"reset to start sizes {start}; fixed services {cluster.set_fixed()}")
    cluster.scale(start)
    cluster.wait_ready(start)
    # after scaling down: restarting reservation needs room for its replacement copies
    reset_hotel_data(log)
    fix_teastore(log)
    log(f"ready after {cluster.wait_ready(start):.0f} s; settling 30 s")
    time.sleep(30)

    index = json.load(open(ROOT / "data/traces/runs/index.json"))
    trace_meta = next((r for r in index["runs"] if a.trace.endswith(f"{r['run']}.csv.gz")), None)
    clock = Clock()
    lg = LoadGen(a.trace, out, seed=a.seed, duration=a.duration, scale=a.scale, arrivals=a.arrivals)
    rec = Recorder(out, clock, lg, minutes=math.ceil(total / 60) + 3, tag=a.name)
    rec.start()
    stop = {"flag": False}
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.update(flag=True))
    try:
        ctrl.setup({"out": out})
        meta = {"name": a.name, "controller": a.controller, "controller_info": ctrl.info(), "trace": a.trace,
                "trace_meta": trace_meta, "seed": a.seed if a.seed is not None else CFG["loadgen"]["seed"],
                "duration_s": total, "scale": a.scale, "arrivals": a.arrivals, "start_replicas": start, "fixed_replicas": {n: f["copies"] for n, f in cluster.FIXED.items()}, "start_epoch": clock.t0, "git_commit": commit,
                "harness_uncommitted_changes": dirty, "config": CFG}
        (out / "meta.json").write_text(json.dumps(meta, indent=1))
        log(f"run started: {a.controller} on {a.trace}, {total} s")
        decisions = JsonLog(out / "decisions.jsonl")
        obs = Observer(lg, clock)
        k = 1
        while not stop["flag"] and lg.running():
            tick = k * CFG["interval_s"]
            if tick >= total:
                break
            wait = clock.t0 + tick - time.time()
            if wait > 0:
                time.sleep(min(wait, 1.0))
                continue
            late = -wait
            t0 = time.time()
            o = obs.observe()
            t1 = time.time()
            want = ctrl.decide(o)
            t2 = time.time()
            applied = {}
            if want:
                changes = {s: n for s, n in want.items() if cluster.clamp(n) != o["services"][s]["desired"]}
                applied = cluster.scale(changes) if changes else {}
            decisions.write({"k": k, "t": round(clock.now(), 2), "tick_late_s": round(late, 2),
                             "observe_s": round(t1 - t0, 3), "decide_s": round(t2 - t1, 3),
                             "apply_s": round(time.time() - t2, 3), "obs": o, "want": want, "applied": applied,
                             "controller": ctrl.last})
            if applied:
                log(f"k={k} t={clock.now():.0f} decide {t2 - t1:.1f} s, scaled {applied}")
            k = max(k + 1, math.floor(clock.now() / CFG["interval_s"]) + 1)   # skip ticks a slow decision overran
        log("trace finished" if not stop["flag"] else "stopped by signal")
    finally:
        lg.stop() if stop["flag"] else None
        try:
            lg.wait(timeout=120)
        except subprocess.TimeoutExpired:
            lg.stop()
            lg.wait(timeout=60)
        ctrl.teardown({"out": out})
        bad = teastore_broken()
        (out / "teastore_probe.json").write_text(json.dumps({"broken_at_end": bad}))
        if bad:
            log(f"TeaStore web copies failing a category page at the end: {bad}")
        rec.stop()
        cleaner.stop()
        log("recorder stopped")
        if host_address():
            log(f"VM swap at end (MiB): {vm_swap(host_address())}")
    subprocess.run([sys.executable, str(ROOT / "harness/analyze_run.py"), str(out)])
    events.close()


def checked():
    """Run, and repeat a run the health check flags (a control-node stall, harness/analyze_run.py) or that
    crashes, at most MAX_RETRIES times (2026-10-08, docs/analysis_plan.md). A failed attempt is kept as
    results/runs/failed_<name>_<n> and logged in results/invalid_runs.json; each attempt is a fresh process."""
    name = sys.argv[sys.argv.index("--name") + 1]
    out = ROOT / "results/runs" / name
    for attempt in range(MAX_RETRIES + 1):
        rc = subprocess.run([sys.executable, __file__, *sys.argv[1:]], env={**os.environ, "RUN_ATTEMPT": str(attempt)}).returncode
        summary = out / "summary.json"
        health = json.load(open(summary)).get("health", {}) if summary.exists() else {}
        probe = out / "teastore_probe.json"
        broken = json.load(open(probe)).get("broken_at_end") if probe.exists() else []
        if rc == 0 and summary.exists() and not health.get("harness_failure") and not broken:
            return 0
        reason = (f"control-node stall (health check: {health})" if health.get("harness_failure")
                  else f"broken TeaStore web copy {broken}" if broken else f"run crashed (exit {rc})")
        n = 1
        while (out.parent / f"failed_{name}_{n}").exists():
            n += 1
        if out.exists():
            out.rename(out.parent / f"failed_{name}_{n}")
        inv = ROOT / "results/invalid_runs.json"
        d = json.load(open(inv)) if inv.exists() else {}
        d[f"{name} (attempt {attempt + 1})"] = (f"{reason}; kept as failed_{name}_{n}; "
                                               + ("repeated automatically." if attempt < MAX_RETRIES else "retries exhausted."))
        inv.write_text(json.dumps(d, indent=1))
        print(f"{name}: attempt {attempt + 1} failed ({reason}); " + ("repeating" if attempt < MAX_RETRIES else "giving up"), flush=True)
    return 4


MAX_RETRIES = 2

if __name__ == "__main__":
    if "RUN_ATTEMPT" in os.environ or "--name" not in sys.argv:
        main()
    else:
        sys.exit(checked())
