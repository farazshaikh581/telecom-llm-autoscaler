"""Our agent, joint variant: one network sees all scaled services and sets every service's HPA target
(30/50/70/90%) each interval. Branching QR-DQN (action branching: Tavakoli et al., AAAI 2018;
quantile regression: Dabney et al., AAAI 2018), since QR-DQN won the per-service comparison.

Network: the published attention double-LSTM over the 8-step window, each step holding all
services' features (rl/ours_env.py), then one head per service with 200 quantiles per action.
Each head learns from its own service's reward (the per-service reward of rl/ours_env.py); the
state is shared. Published DQN hyperparameters: lr 1e-4, gamma 0.99, buffer 5,000, batch 32,
learning starts 50, target network every 500 steps, epsilon 1.0 -> 0.05 over 40% of training
(per head). One gradient step per transition. Observations normalized by running statistics
(as VecNormalize, clip 10), frozen after training.
Budget: 27,000 joint steps = 216,000 service decisions, the same as the per-service variant.

  python rl/ours_joint.py train --out rl/checkpoints/ours_joint_qrdqn
  python rl/ours_joint.py eval rl/checkpoints/ours_joint_qrdqn
"""
import argparse
import json
import pathlib
import sys
import time
from collections import deque

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from stable_baselines3.common.running_mean_std import RunningMeanStd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "rl"), str(ROOT / "sim"), str(ROOT / "tools/traces")]
import train_ours  # noqa: E402
from ours_env import N_FEAT, TARGETS, WINDOW, SharedClusterVecEnv  # noqa: E402

N_SVC = len(train_ours.SERVICES)
N_ACT = len(TARGETS)
N_QUANT = 200
STEPS = 360 * 75
LR, GAMMA, BUFFER, BATCH, START, TARGET_EVERY = 1e-4, 0.99, 5000, 32, 50, 500
EXPLORE_FRAC, EPS_END = 0.4, 0.05
CLIP = 10.0


def joint_obs(per_service):
    """(N services, WINDOW*9) -> (WINDOW, N*9): each window step holds all services' features."""
    x = np.asarray(per_service, dtype=np.float32).reshape(N_SVC, WINDOW, N_FEAT)
    return x.transpose(1, 0, 2).reshape(WINDOW * N_SVC * N_FEAT)


class BranchingQR(nn.Module):
    def __init__(self):
        super().__init__()
        from gymnasium.spaces import Box
        space = Box(-np.inf, np.inf, shape=(WINDOW * N_SVC * N_FEAT,), dtype=np.float32)
        self.trunk = train_ours.AttentionDoubleLSTM(space, features_dim=256)
        self.heads = nn.ModuleList([nn.Sequential(nn.Linear(256, 64), nn.ReLU(), nn.Linear(64, N_ACT * N_QUANT))
                                    for _ in range(N_SVC)])

    def forward(self, x):
        h = self.trunk(x)
        return torch.stack([hd(h).view(-1, N_ACT, N_QUANT) for hd in self.heads], dim=1)   # (B, svc, act, quant)


class JointAgent:
    def __init__(self, seed=0):
        torch.manual_seed(seed)
        self.rng = np.random.default_rng(seed)
        self.net, self.target = BranchingQR(), BranchingQR()
        self.target.load_state_dict(self.net.state_dict())
        self.opt = torch.optim.Adam(self.net.parameters(), lr=LR)
        self.rms = RunningMeanStd(shape=(WINDOW * N_SVC * N_FEAT,))
        self.buf = deque(maxlen=BUFFER)
        self.taus = (torch.arange(N_QUANT, dtype=torch.float32) + 0.5) / N_QUANT
        self.steps, self.updates, self.eps = 0, 0, 1.0

    def norm(self, x, update=False):
        if update:
            self.rms.update(x[None])
        return np.clip((x - self.rms.mean) / np.sqrt(self.rms.var + 1e-8), -CLIP, CLIP).astype(np.float32)

    @torch.no_grad()
    def act(self, xn, eps=None):
        eps = self.eps if eps is None else eps
        q = self.net(torch.as_tensor(xn[None])).mean(-1)[0]          # (svc, act)
        greedy = q.argmax(-1).numpy()
        rand = self.rng.integers(N_ACT, size=N_SVC)
        return np.where(self.rng.random(N_SVC) < eps, rand, greedy)

    def store(self, xn, a, r, xn2, done):
        self.buf.append((xn, np.asarray(a), np.asarray(r, np.float32), xn2, float(done)))

    def train_step(self):
        if len(self.buf) < max(START, BATCH):
            return
        idx = self.rng.integers(len(self.buf), size=BATCH)
        b = [self.buf[i] for i in idx]
        x = torch.as_tensor(np.array([t[0] for t in b]))
        a = torch.as_tensor(np.array([t[1] for t in b]), dtype=torch.int64)               # (B, svc)
        r = torch.as_tensor(np.array([t[2] for t in b]))                                   # (B, svc)
        x2 = torch.as_tensor(np.array([t[3] for t in b]))
        d = torch.as_tensor([t[4] for t in b]).view(-1, 1, 1)
        with torch.no_grad():
            z2 = self.target(x2)                                                            # (B, svc, act, Q)
            a2 = z2.mean(-1).argmax(-1)                                                     # QR-DQN: greedy on target
            z2 = z2.gather(2, a2[..., None, None].expand(-1, -1, 1, N_QUANT)).squeeze(2)   # (B, svc, Q)
            tgt = r[..., None] + GAMMA * (1 - d) * z2
        z = self.net(x).gather(2, a[..., None, None].expand(-1, -1, 1, N_QUANT)).squeeze(2)  # (B, svc, Q)
        u = tgt[:, :, None, :] - z[:, :, :, None]                                          # (B, svc, Q, Q')
        huber = torch.where(u.abs() <= 1, 0.5 * u ** 2, u.abs() - 0.5)
        loss = ((self.taus.view(1, 1, -1, 1) - (u.detach() < 0).float()).abs() * huber).sum(2).mean()
        self.opt.zero_grad()
        loss.backward()
        self.opt.step()
        self.updates += 1

    def on_step(self, total_steps=None):
        self.steps += 1
        if self.steps % TARGET_EVERY == 0:
            self.target.load_state_dict(self.net.state_dict())
        if total_steps:
            frac = min(1.0, self.steps / (EXPLORE_FRAC * total_steps))
            self.eps = 1.0 + frac * (EPS_END - 1.0)

    def save(self, prefix, replay=False):
        torch.save({"net": self.net.state_dict(), "target": self.target.state_dict(), "opt": self.opt.state_dict(),
                    "rms_mean": self.rms.mean, "rms_var": self.rms.var, "rms_count": self.rms.count,
                    "steps": self.steps, "updates": self.updates, "eps": self.eps,
                    "buf": list(self.buf) if replay else None}, prefix + ".pt")

    def load(self, prefix):
        ck = torch.load(prefix + ".pt", weights_only=False)
        self.net.load_state_dict(ck["net"]); self.target.load_state_dict(ck["target"]); self.opt.load_state_dict(ck["opt"])
        self.rms.mean, self.rms.var, self.rms.count = ck["rms_mean"], ck["rms_var"], ck["rms_count"]
        self.steps, self.updates, self.eps = ck["steps"], ck["updates"], ck["eps"]
        if ck.get("buf"):
            self.buf = deque(ck["buf"], maxlen=BUFFER)


def train(out, steps, seed):
    torch.set_num_threads(1)
    traces = [lambda p=p: pd.read_csv(p) for p in train_ours.TRAIN]
    env = SharedClusterVecEnv(traces, train_ours.CAPACITY, train_ours.start_sizes, seed=seed)
    agent = JointAgent(seed)
    log, t0, runs = open(out + ".log.jsonl", "w"), time.time(), 0
    x = agent.norm(joint_obs(env.reset()), update=True)
    while agent.steps < steps:
        a = agent.act(x)
        o2, r, dones, _ = env.step(a)
        done = bool(dones[0])
        if done:   # the env already reset: learn from the terminal observation
            x2 = agent.norm(joint_obs([i["terminal_observation"] for i in _]), update=True)
        else:
            x2 = agent.norm(joint_obs(o2), update=True)
        agent.store(x, a, r, x2, done)
        agent.train_step()
        agent.on_step(total_steps=steps)
        x = agent.norm(joint_obs(o2), update=False) if done else x2
        if done:
            runs += 1
            rec = {"run": runs, "steps": agent.steps, "elapsed_s": round(time.time() - t0, 1), "eps": round(agent.eps, 3),
                   **env.last_run}
            log.write(json.dumps(rec) + "\n"); log.flush()
            if runs % 10 == 0:
                print(rec, flush=True)
                agent.save(out)
    agent.save(out)


def evaluate(prefix):
    """Greedy on the development days in the simulator, 3 seeds (as rl/eval_ours.py)."""
    torch.set_num_threads(1)
    agent = JointAgent()
    agent.load(prefix)
    rows = []
    for d in range(3):
        trace = pd.read_csv(ROOT / f"data/traces/runs/dev_{d}.csv.gz")
        for seed in (1, 2, 3):
            env = SharedClusterVecEnv([lambda t=trace: t], train_ours.CAPACITY, train_ours.start_sizes, seed=seed)
            x = agent.norm(joint_obs(env.reset()))
            while True:
                o2, _, dones, _ = env.step(agent.act(x, eps=0.0))
                if dones[0]:
                    rows.append({"agent": pathlib.Path(prefix).name, "day": d, "seed": seed, **env.last_run})
                    break
                x = agent.norm(joint_obs(o2))
    df = pd.DataFrame(rows)
    print(df.groupby("day")[["violation_k3", "copies_mean"]].mean().round(3).to_string())
    print("mean", df[["violation_k3", "copies_mean"]].mean().round(3).to_dict())
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train", "eval"])
    ap.add_argument("prefix", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--steps", type=int, default=STEPS)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.out, a.steps, a.seed)
    else:
        evaluate(a.prefix)


if __name__ == "__main__":
    main()
