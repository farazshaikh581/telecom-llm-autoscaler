"""Simulated cluster for training and checking controllers: the same trace, services, limits and
observation as the harness (harness/observe.py), advanced one second at a time.

Functions (thumbnailer, compression, imgrec): every request is simulated. It goes to a random
Ready copy (as kube-proxy does), and each copy serves one request at a time (FIFO), with service
times drawn from the no-queue latency measured in the shared cluster.
TeaStore and hotel: per second, latency = no-queue latency x (1 + a rho^b / (1 - rho)) + queue
wait, where rho is the load against the calibrated capacity (hotel: its bottleneck service;
TeaStore: front-end copies and the shared persistence service) and the queue is a fluid backlog
of the load above capacity. A request served by a new TeaStore copy is slower while the copy's Java
code warms up: extra j0 x exp(-age / tau) ms (params teastore_jit).
Workers: every pod sits on one of the workers (scaled copies spread per service, as the
topology rule does; fixed pods where they run). Each second a worker's CPU demand is the sum of
its pods' CPU per request x their load; above the worker's effective capacity (2 cores with
hyperthreads) every pod on it slows down in proportion.
Cluster: at most copies_available copies of the scaled services are placed; the rest stay
Pending until room frees. A new copy is Ready after its measured start time. Requests over the
load generator's timeout fail.
Parameters: sim/params.json (sim/params.py). Validation against real runs: sim/validate.py.
"""
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARAMS = json.load(open(ROOT / "sim/params.json"))
# Services and entries from config/harness.yaml (2026-10-01: scaled and fixed services). SERVICES holds
# every modelled pod set; controllers see and change only SCALED; FIXED keep their copies.
import yaml  # noqa: E402
_CFG = yaml.safe_load(open(ROOT / "config/harness.yaml"))
SCALED = {name: s["entry"] for name, s in _CFG["services"].items()}
FIXED = {name: s["copies"] for name, s in _CFG.get("fixed", {}).items()}
SERVICES = {**SCALED, **{name: s["entry"] for name, s in _CFG.get("fixed", {}).items()}}
ENTRIES = list(_CFG["entries"])
FIFO = [e for e, v in _CFG["entries"].items() if v["ns"] == "sebs"]          # serverless functions
FIFO_SERVICE = {e: next(n for n, x in SCALED.items() if x == e) for e in FIFO}
HOTEL = [n for n, e in SERVICES.items() if e == "hotel"]
FAILED_MS = 1e9


class Pod:
    __slots__ = ("created", "ready_at", "free_at", "busy_s", "worker")

    def __init__(self, created, ready_at, worker):
        self.created, self.ready_at, self.worker = created, ready_at, worker
        self.free_at, self.busy_s = 0.0, 0.0


class ClusterSim:
    def __init__(self, trace, seed=0, params=PARAMS, arrivals="poisson", load_scale=1.0, worker_cpu_eff_m=None):
        """trace: DataFrame with one row per second and a rate column per entry (data/traces/runs)."""
        self.p, self.rng = params, np.random.default_rng(seed)
        self.rates = {e: trace[e].to_numpy(float) * load_scale for e in ENTRIES}
        self.total = len(trace)
        self.arrivals = arrivals
        c = self.p["cluster"]
        self.cap, self.lo, self.hi = c["copies_available"], c["min_replicas"], c["max_replicas"]
        self.n_workers = c["workers"]
        self.worker_cap = worker_cpu_eff_m or c["worker_cpu_eff_m"]
        self.timeout_ms, self.ramp = c["timeout_s"] * 1000, c["ramp_s"]
        self.svc_q = {e: np.array(self.p["entries"][e]["service_ms_quantiles"]) for e in ENTRIES}
        # fast functions: busy time holds the copy, overhead does not (sim/params.py fit_busy)
        self.busy_q = {e: np.array(v["busy_ms_quantiles"]) for e, v in self.p["entries"].items() if "busy_ms_quantiles" in v}
        self.over_q = {e: np.array(v["overhead_ms_quantiles"]) for e, v in self.p["entries"].items() if "overhead_ms_quantiles" in v}
        self.cpu_per_rps = {s: v["cpu_m_per_rps"] for s, v in self.p["services"].items()}
        self.fixed = self.p["fixed_pods"]
        # CPU per busy second of a function copy (CPU per request / mean service time)
        # (capped at 1: a copy's CPU limit; graph-bfs's CPU per request over its busy time exceeds it)
        self.fifo_cpu = {e: min(1.0, self.cpu_per_rps[FIFO_SERVICE[e]] / 1000 * self.p["entries"][e]["mu_per_copy"]) for e in FIFO}
        self.jit = dict(self.p.get("teastore_jit", {"j0_ms": 0.0, "tau_s": 1.0}))
        self.arrivals_override = None   # {entry: counts per second}, for validation
        self.ready_override = None      # f(t) -> {service: ready copies}, for validation
        self.placement_override = None  # f(t) -> {service: [worker per copy]}, for validation

    # ---- cluster state ----
    def reset(self, start):
        start = {**FIXED, **start}   # fixed services at their configured copies
        self.t = 0
        self.pods = {s: [] for s in SERVICES}
        self.pending = {s: 0 for s in SERVICES}
        self.desired = dict(start)
        for s, n in start.items():
            for _ in range(n):
                self.pods[s].append(Pod(-600, -600, self._pick_worker(s)))   # warm, as after the harness reset
        self.backlog = {e: 0.0 for e in ENTRIES if e not in FIFO}
        self.lat = {e: [[] for _ in range(self.total + 1)] for e in ENTRIES}   # per second: (ms or FAILED_MS, weight)
        self.sent = {e: np.zeros(self.total + 1) for e in ENTRIES}
        self.cpu = {s: np.zeros(self.total + 1) for s in SERVICES}   # CPU used per second (millicores)
        self.ready_log = {s: np.zeros(self.total + 1) for s in SERVICES}
        self.slow = np.ones(self.n_workers)
        self.history = []
        return self.observe()

    def placed(self):
        return sum(len(v) for v in self.pods.values())

    def _pick_worker(self, s):
        """Topology spread per service (maxSkew 1), ties to the worker with fewest pods, then random."""
        mine = np.zeros(self.n_workers)
        allp = np.zeros(self.n_workers)
        for name, ps in self.pods.items():
            for p in ps:
                allp[p.worker] += 1
                if name == s:
                    mine[p.worker] += 1
        cand = np.flatnonzero(mine == mine.min())
        cand = cand[allp[cand] == allp[cand].min()]
        return int(self.rng.choice(cand))

    def scale(self, want):
        for s, n in want.items():
            n = max(self.lo, min(self.hi, int(n)))
            self.desired[s] = n
            have = len(self.pods[s]) + self.pending[s]
            if n > have:
                self.pending[s] += n - have
            elif n < have:
                drop = have - n
                k = min(drop, self.pending[s])          # pending copies go first
                self.pending[s] -= k
                drop -= k
                self.pods[s].sort(key=lambda p: (p.ready_at <= self.t, -p.created))  # not ready, then newest
                del self.pods[s][:drop]
        self._place()

    def _place(self):
        for s in SERVICES:
            while self.pending[s] and self.placed() < self.cap:
                self.pending[s] -= 1
                self.pods[s].append(Pod(self.t, self.t + self.p["services"][s]["ready_s"], self._pick_worker(s)))

    def _sync_override(self, t):
        """Validation: make the pod list match the recorded Ready copies and their workers."""
        ready = self.ready_override(t)
        where = self.placement_override(t) if self.placement_override else {}
        for s in SERVICES:
            if s not in ready:          # fixed services are not recorded: they keep their copies
                continue
            ps, k = self.pods[s], ready[s]
            while len(ps) < k:
                ps.append(Pod(t, t, 0))
            while len(ps) > k:
                ps.remove(max(ps, key=lambda p: p.created))
            ws = where.get(s, [])
            for i, p in enumerate(sorted(ps, key=lambda p: p.created)):
                p.worker = ws[i] if i < len(ws) else i % self.n_workers

    def live(self, s, t):
        return [p for p in self.pods[s] if p.ready_at <= t]

    def ready(self, s, t):
        return len(self.live(s, t))

    # ---- traffic ----
    def _arrivals(self, e, t):
        if self.arrivals_override is not None:
            a = self.arrivals_override[e]
            return int(a[t]) if t < len(a) else 0
        r = self.rates[e][t] * (0.2 + 0.8 * min(1.0, t / self.ramp) if self.ramp else 1.0)
        return int(self.rng.poisson(r)) if self.arrivals == "poisson" else int(round(r))

    def _service_ms(self, e, n):
        return np.interp(self.rng.random(n), np.linspace(0, 1, len(self.svc_q[e])), self.svc_q[e])

    def _contention(self, t, n_arr):
        """Per worker slowdown (<= 1) from this second's CPU demand."""
        demand = np.zeros(self.n_workers)
        for f in self.fixed:
            demand[f["worker"]] += f["cpu_m_per_rps"] * n_arr[f["entry"]]
        for s, e in SERVICES.items():
            live = self.live(s, t)
            if not live:
                continue
            share = n_arr[e] * self.cpu_per_rps[s] / len(live)
            for p in live:
                demand[p.worker] += min(1000.0, share)
        self.slow = np.minimum(1.0, self.worker_cap / np.maximum(demand, 1e-9))

    def _fifo_second(self, e, t, n):
        self.sent[e][t] = n
        if not n:
            return
        live = self.live(FIFO_SERVICE[e], t)
        if not live:
            self.lat[e][t].extend((FAILED_MS, 1.0) for _ in range(n))
            return
        times = t + np.sort(self.rng.random(n))
        if e in self.busy_q:
            u = self.rng.random(n)
            svc = np.interp(u, np.linspace(0, 1, len(self.busy_q[e])), self.busy_q[e]) / 1000
            over = np.interp(self.rng.random(n), np.linspace(0, 1, len(self.over_q[e])), self.over_q[e])
        else:
            svc, over = self._service_ms(e, n) / 1000, np.zeros(n)
        pick = self.rng.integers(0, len(live), n)
        for a, sv, i, ov in zip(times, svc, pick, over):
            p = live[i]
            sv = sv / self.slow[p.worker]
            start = max(a, p.free_at)
            p.free_at = start + sv
            p.busy_s += sv
            self.cpu[FIFO_SERVICE[e]][t] += sv * 1000 * self.fifo_cpu[e]
            ms = (p.free_at - a) * 1000 + ov
            self.lat[e][t].append((ms if ms <= self.timeout_ms else FAILED_MS, 1.0))

    def _curve_second(self, e, t, n):
        self.sent[e][t] = n
        par = self.p["entries"][e]
        if e == "teastore":
            live = self.live("teastore-webui", t)
            speed = [self.slow[p.worker] for p in live]
            cap_front = sum(speed) * par["front"]["mu"]
            pw = next((f["worker"] for f in self.fixed if f["name"] == "teastore/teastore-persistence"), 0)
            cap_pers = par["persistence"]["mu"] * self.slow[pw]
            rho_f = n / cap_front if cap_front else 9.9
            mult = self._mult(rho_f, par["front"]) + self._mult(n / cap_pers, par["persistence"]) - 1
            capacity = min(cap_front, cap_pers)
            stretch = 1 / (np.mean(speed) if speed else 1.0)
        else:
            rhos, caps, speeds = [], [], []
            for s, mu in par["mu_per_copy"].items():
                sp = [self.slow[p.worker] for p in self.live(s, t)]
                caps.append(sum(sp) * mu)
                rhos.append(n / caps[-1] if caps[-1] else 9.9)
                speeds += sp
            mult, capacity = self._mult(max(rhos), par["curve"]), min(caps)
            stretch = 1 / (np.mean(speeds) if speeds else 1.0)
        b = self.backlog[e] = max(0.0, self.backlog[e] + n - capacity)
        wait_ms = (b / capacity * 1000) if capacity else self.timeout_ms * 2
        if not n:
            return
        k = min(n, 40)
        ms = self._service_ms(e, k) * mult * stretch + wait_ms
        if e == "teastore" and live and self.jit["j0_ms"]:
            age = np.array([t - p.ready_at for p in live])[self.rng.integers(0, len(live), k)]
            ms = ms + self.jit["j0_ms"] * np.exp(-np.maximum(age, 0) / self.jit["tau_s"])
        w = n / k
        self.lat[e][t].extend((m if m <= self.timeout_ms else FAILED_MS, w) for m in ms)

    @staticmethod
    def _mult(rho, c):
        rho = min(rho, 0.98)   # beyond this the fluid backlog carries the overload
        return 1 + c["a"] * rho ** c["b"] / (1 - rho)

    # ---- one control interval ----
    def advance(self, seconds):
        for _ in range(seconds):
            t = self.t
            if t >= self.total:
                break
            if self.ready_override:
                self._sync_override(t)
            n_arr = {e: self._arrivals(e, t) for e in ENTRIES}
            self._contention(t, n_arr)
            for e in ENTRIES:
                (self._fifo_second if e in FIFO else self._curve_second)(e, t, n_arr[e])
            for s, e in SERVICES.items():
                k = self.ready(s, t)
                self.ready_log[s][t] = k
                if e not in FIFO:
                    self.cpu[s][t] = min(k * 1000.0, n_arr[e] * self.cpu_per_rps[s])
            self.t += 1
            if not self.ready_override:
                self._place()

    def interval_stats(self, e, t0, t1):
        rec = [r for sec in self.lat[e][t0:t1] for r in sec]
        sent = self.sent[e][t0:t1].sum()
        if not rec:
            return {"rps": round(sent / (t1 - t0), 3), "ok_rps": 0.0, "error_rate": 0.0, "latency_ms": {}}, None
        m = np.array([r[0] for r in rec])
        w = np.array([r[1] for r in rec])
        ok = m < FAILED_MS
        stats = {"rps": round(sent / (t1 - t0), 3), "ok_rps": round(float(w[ok].sum()) / (t1 - t0), 3),
                 "error_rate": round(float(w[~ok].sum() / w.sum()), 4), "latency_ms": {}}
        if ok.any():
            stats["latency_ms"] = {"avg": round(float(np.average(m[ok], weights=w[ok])), 2),
                                   **{f"p{q}": round(wq(m[ok], w[ok], q / 100), 2) for q in (50, 75, 90, 95, 99)},
                                   "max": round(float(m[ok].max()), 2)}
        return stats, wq(m, w, 0.95)

    def observe(self):
        iv = self.p["cluster"]["interval_s"]
        t0, t1 = max(0, self.t - iv), max(1, self.t)
        entries = {e: dict(slo_ms=self.p["slo_ms"][e], **self.interval_stats(e, t0, t1)[0]) for e in ENTRIES}
        services = {}
        for s, e in SCALED.items():   # what controllers see (as the harness observation)
            ready = self.ready(s, self.t)
            if e in FIFO:
                cpu = 1000 * sum(min(p.busy_s, iv) for p in self.pods[s]) / iv
                for p in self.pods[s]:
                    p.busy_s = 0.0
            else:
                cpu = min(ready * 1000, entries[e]["rps"] * self.cpu_per_rps[s])
            services[s] = {"entry": e, "desired": self.desired.get(s, 0), "ready": ready,
                           "pending": self.pending[s] + len(self.pods[s]) - ready, "cpu_m": round(cpu, 1),
                           "cpu_util": round(cpu / (max(ready, 1) * self.p["cluster"]["cpu_request_m"]), 3)}
        obs = {"t": self.t, "interval_s": iv, "entries": entries, "services": services}
        full = dict(obs, history=list(self.history[-5:]))
        self.history.append(obs)
        return full

    def cpu_util(self, s, window):
        """Mean CPU utilization of the Ready copies' requests over the last `window` seconds (what
        HPA sees from metrics-server)."""
        t0, t1 = max(0, self.t - window), max(1, self.t)
        used = self.cpu[s][t0:t1].mean()
        ready = max(self.ready_log[s][t0:t1].mean(), 1e-9)
        return used / (ready * self.p["cluster"]["cpu_request_m"])

    def minute_p95(self, e):
        """Per minute: P95 with failures counted as slower than any answer (as analyze_run.py)."""
        return [self.interval_stats(e, m * 60, (m + 1) * 60)[1] for m in range(self.t // 60)]


def wq(values, weights, q):
    """Weighted quantile."""
    o = np.argsort(values)
    v, w = values[o], weights[o]
    c = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), c, v))
