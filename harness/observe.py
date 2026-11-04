"""The one observation every controller receives at each decision step.

Load and latency come live from the load generator over the last interval (what a service
mesh or ingress would report); CPU comes from metrics-server (15 s resolution, what HPA sees).
The trace's planned rate is never shown to controllers.

Observation:
  t                 seconds since the run started
  interval_s        decision interval
  entries[e]        slo_ms, rps (requests sent), ok_rps, error_rate, latency_ms {avg,p50,p75,p90,p95,p99,max}
  services[s]       entry, desired, ready, pending, cpu_m (total), cpu_util (of ready copies' requests)
  history           the previous observations (oldest first, up to 5), same shape without history
"""
import collections

import cluster
from common import CFG, SERVICES, SLO_MS


class Observer:
    def __init__(self, loadgen, clock, keep=5):
        self.lg, self.clock = loadgen, clock
        self.history = collections.deque(maxlen=keep)

    def observe(self):
        interval = CFG["interval_s"]
        stats = self.lg.stats(interval)["entries"]
        reps, pods, cpu = cluster.replicas(), cluster.pods(), cluster.pod_metrics()
        entries = {}
        for e, s in stats.items():
            done = s["ok_rps"] + s["failed_rps"]
            entries[e] = {"slo_ms": SLO_MS[e], "rps": round(s["sent_rps"], 3), "ok_rps": round(s["ok_rps"], 3),
                          "error_rate": round((s["failed_rps"] + s["dropped_rps"]) / max(done + s["dropped_rps"], 1e-9), 4),
                          "latency_ms": {k: round(v, 2) for k, v in s["latency_ms"].items() if k != "n"}}
        services = {}
        for name, sc in SERVICES.items():
            ready = reps[name]["ready"]
            services[name] = {"entry": sc["entry"], "desired": reps[name]["desired"], "ready": ready,
                              "pending": pods[name]["pending"], "cpu_m": cpu[name]["cpu_m"],
                              "cpu_util": round(cpu[name]["cpu_m"] / (max(ready, 1) * CFG["cpu_request_m"]), 3)}
        obs = {"t": round(self.clock.now(), 1), "interval_s": interval, "entries": entries, "services": services}
        full = dict(obs, history=list(self.history))
        self.history.append(obs)
        return full
