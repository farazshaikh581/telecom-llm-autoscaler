"""Compare trained variants of our agent in the simulator on the development days (dev_0-2,
3 seeds each), greedy, no learning, next to simulated HPA. Picks which variant goes to the cluster.

  python rl/eval_ours.py rl/checkpoints/ours_dqn rl/checkpoints/ours_ddqn rl/checkpoints/ours_qrdqn
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rl"))
sys.path.insert(0, str(ROOT / "sim"))
from stable_baselines3.common.vec_env import VecNormalize  # noqa: E402

import train_ours  # noqa: E402  (registers AttentionDoubleLSTM for loading)
from cluster_sim import ENTRIES, ClusterSim  # noqa: E402
from ours_env import SharedClusterVecEnv  # noqa: E402
from sim_controllers import SimHPA, run_closed_loop  # noqa: E402

DEV = [ROOT / f"data/traces/runs/dev_{d}.csv.gz" for d in range(3)]
SEEDS = [1, 2, 3]


def load(prefix):
    algo = next(p for p in pathlib.Path(prefix).name.split("_") if p in ("dqn", "ddqn", "qrdqn", "ppo"))
    cls = {"dqn": "stable_baselines3:DQN", "ddqn": "train_ours:DoubleDQN", "qrdqn": "sb3_contrib:QRDQN",
           "ppo": "stable_baselines3:PPO"}[algo]
    mod, name = cls.split(":")
    return getattr(__import__(mod, fromlist=[name]), name).load(prefix + ".zip", device="cpu")


def eval_agent(prefix):
    model = load(prefix)
    rows = []
    for d, path in enumerate(DEV):
        for seed in SEEDS:
            trace = pd.read_csv(path)
            base = SharedClusterVecEnv([lambda t=trace: t], train_ours.CAPACITY, train_ours.start_sizes, seed=seed,
                                       budget="budget" in pathlib.Path(prefix).name.split("_"))
            env = VecNormalize.load(prefix + "_vecnorm.pkl", base)
            env.training, env.norm_reward = False, False
            obs = env.reset()
            while True:
                act, _ = model.predict(obs, deterministic=True)
                obs, _, dones, _ = env.step(act)
                if dones[0]:
                    rows.append({"agent": pathlib.Path(prefix).name, "day": d, "seed": seed, **base.last_run})
                    break
    return rows


def eval_hpa(target):
    rows = []
    for d, path in enumerate(DEV):
        for seed in SEEDS:
            trace = pd.read_csv(path)
            sim = ClusterSim(trace, seed=seed)
            run_closed_loop(sim, SimHPA(target=target), train_ours.start_sizes(trace), sim.total)
            warm = sim.p["cluster"]["warmup_s"]
            viol = np.mean([np.mean([p > sim.p["slo_ms"][e] for p in sim.minute_p95(e)[warm // 60:]])
                            for e in ENTRIES])
            copies = sum(sim.ready_log[s][warm:sim.total].mean() for s in train_ours.SERVICES)
            rows.append({"agent": f"hpa_{target}", "day": d, "seed": seed, "violation_k3": viol, "copies_mean": copies})
    return rows


def main(prefixes):
    torch.set_num_threads(1)
    rows = eval_hpa(80) + eval_hpa(70)
    for p in prefixes:
        rows += eval_agent(p)
    df = pd.DataFrame(rows)
    tag = "_budget" if all("budget" in pathlib.Path(p).name.split("_") for p in prefixes) else ""   # exploratory variant
    df.to_csv(ROOT / f"rl/eval_ours{tag}.csv", index=False)
    t = df.groupby("agent").agg(violation_k3=("violation_k3", "mean"), copies_mean=("copies_mean", "mean"))
    by_day = df.pivot_table(index="agent", columns="day", values="violation_k3")
    print(t.round(3).join(by_day.round(3).add_prefix("viol_dev")).to_string())
    # same rule as baseline tuning: within 3 points of the lowest violation rate, then fewest copies;
    # with several training seeds (<prefix>_s<n>) per variant, the rule ranks the variants by their
    # mean over seeds, and the picked variant's median seed goes to the cluster (2026-10-06)
    ours = t[~t.index.str.startswith("hpa_")].copy()
    ours["variant"] = ours.index.str.replace(r"_s\d+$", "", regex=True)
    var = ours.groupby("variant")[["violation_k3", "copies_mean"]].mean()
    if ours.variant.nunique() < len(ours):
        print(var.round(3).to_string())
    near = var[var.violation_k3 <= var.violation_k3.min() + 0.03]
    best = near.sort_values(["copies_mean", "violation_k3"]).index[0]
    seeds = ours[ours.variant == best].sort_values("violation_k3")
    pick = seeds.index[len(seeds) // 2]
    (ROOT / f"rl/checkpoints/ours{tag}_pick.txt").write_text(pick + "\n")
    print("picked:", pick)


if __name__ == "__main__":
    main(sys.argv[1:])
