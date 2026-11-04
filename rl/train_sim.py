"""Train a per-service agent (AWARE, FIRM) in the simulator on the RL training days
(data/traces/train, never used on the cluster).

Every 60 s each scaled service gets its state (rl/features.py) and the agent picks stay,
+1 or -1 copy. The reward of an action is computed on the next state. A move out of
[min, max] copies is refused with reward -1 (as AWARE's environment does). Episodes are
10 decisions per service; a simulated run (75 min) gives 7 per service.

  python rl/train_sim.py aware --runs 360 --out rl/checkpoints/aware.pt
Published budgets: AWARE 500 iterations x 5 episodes = 2,500 episodes per service = about 360
simulated runs (7 episodes per service each); FIRM 9,000 episodes x 300 steps = 2.7M
transitions = 4,500 simulated runs (600 transitions each, one agent for all services).
"""
import argparse
import json
import math
import pathlib
import sys
import time

import numpy as np
import pandas as pd
import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rl"))
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "tools/traces"))
import features  # noqa: E402
from build_traces import SCALED_CAP  # noqa: E402
from cluster_sim import ENTRIES, SCALED as SERVICES, ClusterSim  # noqa: E402  scaled services only

CAPACITY = {s: SCALED_CAP[e] for s, e in SERVICES.items()}
TRAIN = sorted((ROOT / "data/traces/train").glob("train_*.csv.gz"))


def start_sizes(trace, lo=features.CFG["min_replicas"], hi=features.CFG["max_replicas"]):
    first = trace.iloc[:60].mean()
    return {s: max(lo, min(hi, math.ceil(first[e] / CAPACITY[s]))) for s, e in SERVICES.items()}


def episode_run(agent, trace, seed, learn=True, ep_len=10):
    """One simulated run with the agent controlling every service. Returns per-run KPIs."""
    sim = ClusterSim(trace, seed=seed)
    obs = sim.reset(start_sizes(trace))
    iv = sim.p["cluster"]["interval_s"]
    last = {s: 0 for s in SERVICES}
    prev = {}                                  # service -> (state, action, log_prob, illegal)
    eps = {s: {"states": [], "actions": [], "rewards": [], "log_probs": []} for s in SERVICES}
    total_r = 0.0
    while sim.t + iv <= sim.total:
        sim.advance(iv)
        obs = sim.observe()
        want = {}
        for s in SERVICES:
            st = features.state(obs, s, CAPACITY[s])
            if s in prev:
                pst, pa, plp, illegal = prev[s]
                delta = agent.delta(pa)
                r = -1.0 if illegal else features.reward(st, delta, last[s])
                last[s] = 0 if illegal else delta
                if learn and hasattr(agent, "step"):      # FIRM: one transition, one update
                    agent.step(s, pst, plp, r, st)
                e = eps[s]
                e["states"].append(pst); e["actions"].append(pa); e["rewards"].append(r); e["log_probs"].append(plp)
                total_r += r
                if len(e["states"]) == ep_len:
                    if learn:
                        agent.end_episode(s, e)
                    eps[s] = {"states": [], "actions": [], "rewards": [], "log_probs": []}
            a, lp = agent.act(s, st, greedy=not learn)
            n = sim.desired[s] + agent.delta(a)
            illegal = not (sim.lo <= n <= sim.hi)
            if not illegal and n != sim.desired[s]:
                want[s] = n
            prev[s] = (st, a, lp, illegal)
        if want:
            sim.scale(want)
    warm = sim.p["cluster"]["warmup_s"]
    viol = np.mean([np.mean([p > sim.p["slo_ms"][e] for p in sim.minute_p95(e)[warm // 60:]])
                    for e in ENTRIES])
    copies = sum(sim.ready_log[s][warm:sim.total].mean() for s in SERVICES)
    return {"reward": total_r, "violation_k3": float(viol), "copies_mean": float(copies)}


def make_agent(kind, seed):
    if kind == "aware":
        from aware_agent import ACTION_DELTA, AwareAgent
        a = AwareAgent(seed=seed)
        a.delta = lambda action: ACTION_DELTA[action]
        return a
    if kind == "firm":
        from firm_agent import ACTION_DELTA, FirmAgent
        a = FirmAgent(seed=seed)
        a.delta = lambda action: ACTION_DELTA[action]
        return a
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["aware", "firm"])
    ap.add_argument("--runs", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    torch.set_num_threads(1)     # one core, so cluster runs on the same host are not disturbed
    rng = np.random.default_rng(a.seed)
    agent = make_agent(a.kind, a.seed)
    log = open(pathlib.Path(a.out).with_suffix(".log.jsonl"), "w")
    t0 = time.time()
    for i in range(a.runs):
        path = TRAIN[rng.integers(len(TRAIN))]
        k = episode_run(agent, pd.read_csv(path), seed=int(rng.integers(1 << 30)))
        rec = dict(run=i, trace=path.name, elapsed_s=round(time.time() - t0, 1), updates=getattr(agent, "updates", 0), **k)
        log.write(json.dumps(rec) + "\n"); log.flush()
        if i % 10 == 0:
            print(rec, flush=True)
            agent.save(a.out)
    agent.save(a.out)


if __name__ == "__main__":
    main()
