"""Train our agent (stability-aware HPA-target meta-controller, rl/ours_env.py) in the simulator
on the RL training days.

Network as published (FGCS work, dlstm_agent.py): attention double-LSTM over an 8-step window,
features 256, head [64]. --algo:
  dqn    DQN as published (SB3 DQN, plain: "double" in the published name is the double LSTM,
         not Double DQN), published hyperparameters (lr 1e-4, gamma 0.99, buffer 5,000,
         batch 32, learning starts 50, target update 500, exploration 40% to 0.05). With the
         services stepping together, one gradient step per service per step keeps the published
         one update per transition.
  ddqn   Double DQN (van Hasselt et al., 2016): the online network picks the next action, the
         target network values it. Everything else as dqn.
  qrdqn  QR-DQN (sb3-contrib), the same hyperparameters, distributional value head.
  ppo    PPO (SB3 defaults), the same network as feature extractor. Dropped from the variants
         (docs/decisions.md, 2026-10-03): on-policy, no online fine-tuning on the cluster.
Budget: 216,000 service decisions (360 simulated runs), the same simulated experience as AWARE.

  python rl/train_ours.py --algo dqn --out rl/checkpoints/ours_dqn
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
import torch.nn as nn
import torch.nn.functional as F
from stable_baselines3 import DQN
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import VecNormalize

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rl"))
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "tools/traces"))
import features  # noqa: E402
from build_traces import SCALED_CAP  # noqa: E402
from cluster_sim import SCALED as SERVICES  # noqa: E402  scaled services only
from ours_env import N_FEAT, WINDOW, SharedClusterVecEnv  # noqa: E402

CAPACITY = {s: SCALED_CAP[e] for s, e in SERVICES.items()}
TRAIN = sorted((ROOT / "data/traces/train").glob("train_*.csv.gz"))
STEPS = 360 * 75 * len(SERVICES)


class AttentionDoubleLSTM(BaseFeaturesExtractor):
    """As published: per-step MLP, 2-layer LSTM (256), attention pooling over the window."""

    def __init__(self, observation_space, features_dim=256):
        super().__init__(observation_space, features_dim)
        self.seq_len, self.input_dim, self.hidden_dim = WINDOW, observation_space.shape[0] // WINDOW, 256
        self.feature_net = nn.Sequential(nn.Linear(self.input_dim, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU())
        self.lstm = nn.LSTM(input_size=128, hidden_size=self.hidden_dim, num_layers=2, batch_first=True, dropout=0.1)
        self.attn_fc = nn.Linear(self.hidden_dim, 1)
        self.out_net = nn.Sequential(nn.Linear(self.hidden_dim, features_dim), nn.ReLU())
        self.last_attn_weights = None

    def forward(self, observations):
        b = observations.shape[0]
        x = observations.view(b, self.seq_len, self.input_dim)
        feats = self.feature_net(x.reshape(-1, self.input_dim)).view(b, self.seq_len, -1)
        out, _ = self.lstm(feats)
        w = F.softmax(self.attn_fc(out), dim=1)
        self.last_attn_weights = w.detach().cpu().numpy()
        return self.out_net(torch.sum(out * w, dim=1))


class DoubleDQN(DQN):
    """SB3 DQN with the Double DQN target: argmax from the online network, value from the target."""

    def train(self, gradient_steps, batch_size=100):
        self.policy.set_training_mode(True)
        self._update_learning_rate(self.policy.optimizer)
        losses = []
        for _ in range(gradient_steps):
            data = self.replay_buffer.sample(batch_size, env=self._vec_normalize_env)
            discounts = data.discounts if data.discounts is not None else self.gamma
            with torch.no_grad():
                next_a = self.q_net(data.next_observations).argmax(dim=1, keepdim=True)
                next_q = torch.gather(self.q_net_target(data.next_observations), 1, next_a)
                target = data.rewards + (1 - data.dones) * discounts * next_q
            current = torch.gather(self.q_net(data.observations), 1, data.actions.long())
            loss = F.smooth_l1_loss(current, target)
            losses.append(loss.item())
            self.policy.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
            self.policy.optimizer.step()
        self._n_updates += gradient_steps
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/loss", np.mean(losses))


def start_sizes(trace, lo=features.CFG["min_replicas"], hi=features.CFG["max_replicas"]):
    first = trace.iloc[:60].mean()
    return {s: max(lo, min(hi, math.ceil(first[e] / CAPACITY[s]))) for s, e in SERVICES.items()}


def make_model(algo, env, seed):
    pk = dict(features_extractor_class=AttentionDoubleLSTM, features_extractor_kwargs=dict(features_dim=256))
    if algo in ("dqn", "ddqn", "qrdqn"):
        kw = dict(learning_rate=1e-4, gamma=0.99, buffer_size=5000, batch_size=32, learning_starts=50,
                  train_freq=1, gradient_steps=env.num_envs, target_update_interval=500,
                  exploration_fraction=0.4, exploration_final_eps=0.05, seed=seed, verbose=0)
        if algo in ("dqn", "ddqn"):
            return {"dqn": DQN, "ddqn": DoubleDQN}[algo]("MlpPolicy", env, policy_kwargs=dict(pk, net_arch=[64]), **kw)
        from sb3_contrib import QRDQN
        return QRDQN("MlpPolicy", env, policy_kwargs=dict(pk, net_arch=[64]), **kw)
    from stable_baselines3 import PPO
    return PPO("MlpPolicy", env, policy_kwargs=dict(pk, net_arch=dict(pi=[64], vf=[64])), seed=seed, verbose=0)


class RunLog(BaseCallback):
    def __init__(self, path, base):
        super().__init__()
        self.f, self.base, self.t0, self.n = open(path, "w"), base, time.time(), 0

    def _on_step(self):
        if any(self.locals["dones"]):
            self.n += 1
            k = self.base.last_run
            rec = {"run": self.n, "steps": self.num_timesteps, "elapsed_s": round(time.time() - self.t0, 1), **k}
            self.f.write(json.dumps(rec) + "\n"); self.f.flush()
            if self.n % 10 == 0:
                print(rec, flush=True)
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algo", choices=["dqn", "ddqn", "qrdqn", "ppo"], required=True)
    ap.add_argument("--steps", type=int, default=STEPS, help="0: save the untrained model (from-scratch arm)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--budget", action="store_true", help="budget-aware variant (rl/ours_env.py; exploratory)")
    ap.add_argument("--out", required=True, help="path prefix: <out>.zip, <out>_vecnorm.pkl, <out>.log.jsonl")
    a = ap.parse_args()
    torch.set_num_threads(1)
    traces = [lambda p=p: pd.read_csv(p) for p in TRAIN]
    base = SharedClusterVecEnv(traces, CAPACITY, start_sizes, seed=a.seed, budget=a.budget)
    env = VecNormalize(base, norm_obs=True, norm_reward=False, clip_obs=10.0)
    model = make_model(a.algo, env, a.seed)
    if a.steps:
        model.learn(total_timesteps=a.steps, callback=RunLog(a.out + ".log.jsonl", base))
    model.save(a.out + ".zip")
    env.save(a.out + "_vecnorm.pkl")


if __name__ == "__main__":
    main()
