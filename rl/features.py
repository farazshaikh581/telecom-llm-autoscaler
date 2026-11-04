"""State and reward of the per-service literature agents (AWARE, FIRM), built from the harness
observation (harness/observe.py); the simulator returns the same observation, so training and
cluster runs compute them identically.

AWARE `testing/` formulation (commit 7cd13d6), horizontal only:
  state  = [CPU utilization, SLO preservation, copies / max copies, arrival rate / max capacity]
           (max copies per service: config/harness.yaml)
           (its CPU-share terms are vertical scaling and are dropped)
  reward = 0.3 x CPU utilization + 0.7 x SLO preservation, -1 for scaling in the opposite
           direction of the previous step (flip-flop)
CPU utilization is of the copies' 1-CPU limit (0-1), SLO preservation is the share of requests
answered within the SLO in the last interval. The observation holds latency percentiles, not
that share, so it is read off the piecewise-linear distribution through avg-free points
(P50, P75, P90, P95, P99, max), and failed requests count as outside.
"""
import pathlib

import numpy as np
import yaml

CFG = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config/harness.yaml"))
LIMIT_M = 1000
MAX_COPIES = CFG["max_replicas"]
WORKER_CPU_M = 1000 * CFG["workers"] * CFG["worker_vcpus"]
ALPHA, BETA = 0.3, 0.7
FLIP_PENALTY = -1.0
N_STATE = 4


def slo_share(entry_obs):
    lat, slo = entry_obs.get("latency_ms") or {}, entry_obs["slo_ms"]
    if not lat:
        return 0.0 if entry_obs.get("rps", 0) > 0 else 1.0
    qs = [(0.50, lat["p50"]), (0.75, lat["p75"]), (0.90, lat["p90"]), (0.95, lat["p95"]), (0.99, lat["p99"]),
          (1.0, lat["max"])]
    ps = np.array([0.0] + [q for q, _ in qs])
    vs = np.array([0.0] + [v for _, v in qs])
    vs = np.maximum.accumulate(vs)
    share = float(np.interp(slo, vs, ps)) if slo < vs[-1] else 1.0
    return share * (1 - entry_obs.get("error_rate", 0.0))


def state(obs, service, capacity_per_copy):
    s = obs["services"][service]
    e = obs["entries"][s["entry"]]
    util = s["cpu_m"] / (max(s["ready"], 1) * LIMIT_M)
    return [min(util, 1.0), slo_share(e), s["ready"] / MAX_COPIES,
            min(e["rps"] / (MAX_COPIES * capacity_per_copy), 1.5)]


def reward(st, action, last_action):
    """action / last_action: +1 scale out, -1 scale in, 0 nothing."""
    r = ALPHA * st[0] + BETA * st[1]
    if action * last_action < 0:
        r = FLIP_PENALTY
    return r
