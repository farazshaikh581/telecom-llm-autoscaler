"""Our agent's environment: the stability-aware HPA-target meta-controller (FGCS work,
dlstm_agent.py) on the shared simulator. Each scaled service is one environment of an SB3
VecEnv; all 8 step together on one ClusterSim, so one shared network makes one decision per
service (the per-service variant).

Every 60 s the agent sets the service's HPA CPU target to 30, 50, 70 or 90%; the Kubernetes HPA
algorithm (sim/sim_controllers.py) then scales every 15 s.

Observation per step (then an 8-step window, as published), from the harness observation:
  P95 / SLO, success ratio, Ready copies / max copies, CPU utilization of the request, request rate
  / (max copies x per-copy capacity), cluster CPU in use / worker cores, current HPA target / 100,
  history-only EWMA forecast of the rate (same scale), pending copies / max copies
  (max copies per service and worker cores: config/harness.yaml).
Dropped from the published 14: memory (not observed), time of day (traces compress days), and
the two load knobs of the old experiments (throughput multiplier, enhancement).
Reward (published weights): 0.55 R_SLO + 0.25 R_CPU + 0.12 R_succ + 0.08 R_stab, with R_SLO's
20/50 ms thresholds rescaled to the service's SLO (0.4 x SLO and SLO) and P95 in place of P90
(our SLO is on P95).

Budget-aware variant (budget=True; exploratory, docs/decisions.md 2026-10-15): two shared features
appended (copies set by all scaled services / copies available to them, and the mean SLO
preservation over all applications), and a shared reward: R_SLO is the mean over all applications
instead of the service's own, minus BUDGET_PENALTY x (copies set beyond the budget) / budget.
"""
from collections import deque

import gymnasium as gym
import numpy as np
from stable_baselines3.common.vec_env import VecEnv

from cluster_sim import ENTRIES, SCALED as SERVICES, ClusterSim   # controllers see the scaled services only
from features import CFG, MAX_COPIES, WORKER_CPU_M, slo_share
from sim_controllers import SimHPA

TARGETS = [30, 50, 70, 90]
WINDOW = 8
N_FEAT = 9
EWMA = 0.3
ROOM = CFG["copies_available"] - sum(f["copies"] for f in CFG.get("fixed", {}).values())   # copies for the scaled services
BUDGET_PENALTY = 0.5


def n_feat(budget=False):
    return N_FEAT + (2 if budget else 0)


def features(obs, service, capacity, target, ewma, budget=False):
    s = obs["services"][service]
    e = obs["entries"][s["entry"]]
    p95 = (e.get("latency_ms") or {}).get("p95", 0.0)
    cpu_total = sum(v["cpu_m"] for v in obs["services"].values()) / WORKER_CPU_M
    scale = MAX_COPIES * capacity
    f = [min(p95 / e["slo_ms"], 10.0), 1 - e.get("error_rate", 0.0), s["ready"] / MAX_COPIES,
         min(s["cpu_util"], 2.0), e["rps"] / scale, cpu_total, target / 100, ewma / scale, s["pending"] / MAX_COPIES]
    if budget:
        f += [sum(v["desired"] for v in obs["services"].values()) / ROOM,
              float(np.mean([slo_share(x) for x in obs["entries"].values()]))]
    return f


def _r_slo(e):
    slo = e["slo_ms"]
    lat = (e.get("latency_ms") or {}).get("p95", 10 * slo)
    lo, span = 0.4 * slo, 0.6 * slo
    return 1.0 if lat <= lo else (0.5 + 0.5 * (slo - lat) / span if lat <= slo
                                  else max(-1.0, -0.5 * (lat - slo) / (2 * slo)))


def reward(obs, service, target, last_ready, budget=False):
    s = obs["services"][service]
    e = obs["entries"][s["entry"]]
    r_slo = float(np.mean([_r_slo(x) for x in obs["entries"].values()])) if budget else _r_slo(e)
    cpu = s["cpu_util"] * 100
    r_cpu = 1.0 if abs(cpu - target) <= 10 else float(np.exp(-((cpu - target) / 50.0) ** 2))
    succ = 1 - e.get("error_rate", 0.0)
    r_succ = 1.0 if succ >= 0.99 else float(np.log(max(succ, 1e-6)))
    delta = abs(s["ready"] - last_ready)
    r_stab = -0.1 * delta if delta <= 2 else -0.5 * delta
    r = 0.55 * r_slo + 0.25 * r_cpu + 0.12 * r_succ + 0.08 * r_stab
    if budget:
        r -= BUDGET_PENALTY * max(0, sum(v["desired"] for v in obs["services"].values()) - ROOM) / ROOM
    return r


class SharedClusterVecEnv(VecEnv):
    """One environment per scaled service over one simulated run; an episode is one 75-min run."""

    def __init__(self, traces, capacity, start_sizes, seed=0, budget=False):
        self.services, self.budget = list(SERVICES), budget
        space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=(WINDOW * n_feat(budget),), dtype=np.float32)
        super().__init__(len(self.services), space, gym.spaces.Discrete(len(TARGETS)))
        self.traces, self.capacity, self.start_sizes = traces, capacity, start_sizes
        self.rng = np.random.default_rng(seed)
        self.actions = None

    def _new_run(self):
        trace = self.traces[int(self.rng.integers(len(self.traces)))]()
        self.sim = ClusterSim(trace, seed=int(self.rng.integers(1 << 30)))
        self.hpa = SimHPA(target=70)
        self.obs = self.sim.reset(self.start_sizes(trace))
        self.targets = {s: 70 for s in self.services}
        self.ewma = {s: 0.0 for s in self.services}
        self.win = {s: deque(maxlen=WINDOW) for s in self.services}
        self.last_ready = {s: self.obs["services"][s]["ready"] for s in self.services}
        for s in self.services:
            f = features(self.obs, s, self.capacity[s], 70, 0.0, self.budget)
            for _ in range(WINDOW):
                self.win[s].append(f)
        return self._stack()

    def _stack(self):
        return np.array([np.concatenate(self.win[s]) for s in self.services], dtype=np.float32)

    def reset(self):
        return self._new_run()

    def step_async(self, actions):
        self.actions = actions

    def step_wait(self):
        for s, a in zip(self.services, self.actions):
            self.targets[s] = TARGETS[int(a)]
            self.hpa.targets[s] = self.targets[s] / 100
        iv = self.sim.p["cluster"]["interval_s"]
        for _ in range(iv // self.hpa.sync_s):
            self.sim.advance(self.hpa.sync_s)
            want = self.hpa.tick(self.sim)
            if want:
                self.sim.scale(want)
        self.obs = self.sim.observe()
        rews = []
        for s in self.services:
            rps = self.obs["entries"][self.obs["services"][s]["entry"]]["rps"]
            self.ewma[s] = EWMA * rps + (1 - EWMA) * self.ewma[s]
            self.win[s].append(features(self.obs, s, self.capacity[s], self.targets[s], self.ewma[s], self.budget))
            rews.append(reward(self.obs, s, self.targets[s], self.last_ready[s], self.budget))
            self.last_ready[s] = self.obs["services"][s]["ready"]
        done = self.sim.t + iv > self.sim.total
        obs = self._stack()
        infos = [{} for _ in self.services]
        if done:
            for k, i in enumerate(infos):
                i["terminal_observation"] = obs[k]
            self.last_run = self.run_kpis()
            obs = self._new_run()
        return obs, np.array(rews, dtype=np.float32), np.array([done] * self.num_envs), infos

    def run_kpis(self):
        warm = self.sim.p["cluster"]["warmup_s"]
        viol = np.mean([np.mean([p > self.sim.p["slo_ms"][e] for p in self.sim.minute_p95(e)[warm // 60:]])
                        for e in ENTRIES])
        copies = sum(self.sim.ready_log[s][warm:self.sim.total].mean() for s in self.services)
        return {"violation_k3": float(viol), "copies_mean": float(copies)}

    def close(self):
        pass

    def get_attr(self, name, indices=None):
        return [getattr(self, name)] * self.num_envs

    def set_attr(self, name, value, indices=None):
        setattr(self, name, value)

    def env_method(self, name, *args, indices=None, **kwargs):
        return [None] * self.num_envs

    def env_is_wrapped(self, wrapper_class, indices=None):
        return [False] * self.num_envs

    def seed(self, seed=None):
        self.rng = np.random.default_rng(seed)
