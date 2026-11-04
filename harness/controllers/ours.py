"""Our agent on the cluster: the stability-aware HPA-target meta-controller (rl/ours_env.py,
rl/train_ours.py). One native HPA per service (as harness/controllers/hpa.py); every interval the
agent picks each service's CPU target (30/50/70/90%) and the harness patches the HPA when it changes.
Observation windows, features and reward are the ones used in the simulator; observations are
normalized with the simulator's statistics (frozen).

Test runs: greedy, no learning (DQN, Double DQN, QR-DQN or PPO). Fine-tuning (learn=1, not PPO): epsilon 0.05, every interval
the scaled services' transitions go to the replay buffer, one gradient step (one per transition, as in
training), target network updated on the published schedule. The replay buffer is carried from one
fine-tuning run to the next (<save>_replay.pkl).

From scratch (scratch=<total decisions per service over all fine-tuning runs>): the published
exploration instead of 0.05: epsilon from 1.0 to 0.05 over the first 40% of those decisions,
counted across runs (<save>_state.json).

Params: ckpt=<prefix, relative to the repo: <prefix>.zip and <prefix>_vecnorm.pkl> (required),
algo=dqn|ddqn|qrdqn|ppo (default: read from the prefix), learn=0|1, save=<prefix to write the model (and
replay buffer) at the end of the run>, scratch=<decisions>.
"""
import json
import pathlib
import pickle
import shutil
import sys
from collections import deque

import numpy as np
import torch
from stable_baselines3.common.logger import configure

from common import ROOT, SERVICES, kubectl

from .base import Controller
from .hpa import HPA, remove_all

sys.path.insert(0, str(ROOT / "rl"))
sys.path.insert(0, str(ROOT / "sim"))
import train_ours  # noqa: E402
from ours_env import EWMA, TARGETS, WINDOW, features, reward  # noqa: E402

EPS_FINETUNE = 0.05


def capacity(s):
    """Per-copy capacity; a service the agent never trained on (new-service shift) takes the harness config's."""
    return train_ours.CAPACITY.get(s) or train_ours.SCALED_CAP[SERVICES[s]["entry"]]


class Ours(Controller):
    def setup(self, ctx):
        torch.set_num_threads(1)
        self.prefix = str(ROOT / self.params["ckpt"])
        self.learn = self.params.get("learn", "0") == "1"
        parts = pathlib.Path(self.prefix).name.split("_")
        algo = self.params.get("algo") or next(p for p in parts if p in ("dqn", "ddqn", "qrdqn", "ppo"))
        if algo == "ppo" and self.learn:
            raise ValueError("online fine-tuning is implemented for DQN, Double DQN and QR-DQN only")
        mod, name = {"dqn": ("stable_baselines3", "DQN"), "ddqn": ("train_ours", "DoubleDQN"), "qrdqn": ("sb3_contrib", "QRDQN"),
                     "ppo": ("stable_baselines3", "PPO")}[algo]
        self.algo = algo
        self.budget = "budget" in parts   # budget-aware variant (rl/ours_env.py), from the checkpoint name
        self.model = getattr(__import__(mod, fromlist=[name]), name).load(self.prefix + ".zip", device="cpu")
        self.model.set_logger(configure(None, []))             # train() and _on_step() log through it
        with open(self.prefix + "_vecnorm.pkl", "rb") as f:
            vn = pickle.load(f)
        self.obs_mean, self.obs_var = vn.obs_rms.mean, vn.obs_rms.var
        self.clip, self.eps_norm = vn.clip_obs, vn.epsilon
        if self.learn:
            replay = self.prefix + "_replay.pkl"
            if pathlib.Path(replay).exists():
                self.model.load_replay_buffer(replay)
            self.model._current_progress_remaining = 0.0     # end of the exploration schedule
            self.model.exploration_rate = EPS_FINETUNE
        self.scratch = int(self.params.get("scratch", 0))
        state = pathlib.Path(self.prefix + "_state.json")
        self.decisions = json.loads(state.read_text())["decisions"] if state.exists() else 0
        self.services = list(SERVICES)
        self.targets = {s: 70 for s in self.services}
        HPA(target=70).setup(ctx)
        self.win, self.ewma, self.last_ready, self.prev = {}, {s: 0.0 for s in self.services}, {}, None
        self.rng = np.random.default_rng(int(self.params.get("seed", 0)))

    def _norm(self, x):
        return np.clip((x - self.obs_mean) / np.sqrt(self.obs_var + self.eps_norm), -self.clip, self.clip).astype(np.float32)

    def _capacity(self, s):
        return capacity(s)

    def decide(self, obs):
        rews = []
        for s in self.services:
            rps = obs["entries"][obs["services"][s]["entry"]]["rps"]
            self.ewma[s] = EWMA * rps + (1 - EWMA) * self.ewma[s]
            f = features(obs, s, self._capacity(s), self.targets[s], self.ewma[s], self.budget)
            if s not in self.win:
                self.win[s] = deque([f] * WINDOW, maxlen=WINDOW)
            else:
                self.win[s].append(f)
            if self.prev is not None:
                rews.append(reward(obs, s, self.targets[s], self.last_ready[s], self.budget))
            self.last_ready[s] = obs["services"][s]["ready"]
        x = self._norm(np.array([np.concatenate(self.win[s]) for s in self.services], dtype=np.float32))
        info = {}
        if self.learn and self.prev is not None:
            px, pa = self.prev
            self.model.replay_buffer.add(px, x, pa.reshape(-1, 1), np.array(rews, dtype=np.float32),
                                         np.zeros(len(self.services)), [{} for _ in self.services])
            self.model.num_timesteps += len(self.services)
            self.model._on_step()                               # target network schedule
            self.model.exploration_rate = EPS_FINETUNE
            if self.model.replay_buffer.size() * len(self.services) >= self.model.learning_starts:
                self.model.train(gradient_steps=len(self.services), batch_size=self.model.batch_size)
            info["reward_mean"] = round(float(np.mean(rews)), 4)
        if self.learn:
            eps = EPS_FINETUNE
            if self.scratch:
                eps = max(EPS_FINETUNE, 1.0 - (1.0 - EPS_FINETUNE) * self.decisions / (0.4 * self.scratch))
            self.decisions += 1
            info["epsilon"] = round(eps, 4)
            act = np.array([self.rng.integers(len(TARGETS)) if self.rng.random() < eps
                            else int(self.model.predict(x[i:i + 1], deterministic=True)[0][0])
                            for i in range(len(self.services))])
        else:
            act = self.model.predict(x, deterministic=True)[0]
        self.prev = (x, np.asarray(act))
        changed = {}
        for s, a in zip(self.services, act):
            t = TARGETS[int(a)]
            if t != self.targets[s]:
                self.targets[s] = t
                changed[s] = t
                d = SERVICES[s]
                patch = {"spec": {"metrics": [{"type": "Resource", "resource": {
                    "name": "cpu", "target": {"type": "Utilization", "averageUtilization": t}}}]}}
                kubectl("-n", d["ns"], "patch", "hpa", f"harness-{d['deploy']}", "--type", "merge",
                        "-p", json.dumps(patch))
        if rews:
            info["rewards"] = {s: round(r, 4) for s, r in zip(self.services, rews)}
        self.last = {"reason": "hpa-targets", "targets": dict(self.targets), "changed": changed, **info}
        return None

    def teardown(self, ctx):
        remove_all()
        out = self.params.get("save")
        if out:
            out = str(ROOT / out)
            self.model.save(out + ".zip")
            shutil.copy(self.prefix + "_vecnorm.pkl", out + "_vecnorm.pkl")
            if self.learn:
                self.model.save_replay_buffer(out + "_replay.pkl")
                pathlib.Path(out + "_state.json").write_text(json.dumps({"decisions": self.decisions}))


class OursJoint(Controller):
    """Joint variant (rl/ours_joint.py): one network sees all services and sets every HPA target.
    Params: ckpt=<prefix of <prefix>.pt> (required), learn=0|1, save=<prefix>, scratch=<decisions>
    (as the per-service controller; fine-tuning keeps epsilon 0.05, from scratch follows the
    published schedule over those decisions)."""

    def setup(self, ctx):
        torch.set_num_threads(1)
        import ours_joint
        self.oj = ours_joint
        self.prefix = str(ROOT / self.params["ckpt"])
        self.learn = self.params.get("learn", "0") == "1"
        self.scratch = int(self.params.get("scratch", 0))
        self.agent = ours_joint.JointAgent(seed=int(self.params.get("seed", 0)))
        self.agent.load(self.prefix)
        self.decisions = self.agent.steps if self.scratch else 0
        self.services = list(SERVICES)
        self.targets = {s: 70 for s in self.services}
        HPA(target=70).setup(ctx)
        self.win, self.ewma, self.last_ready, self.prev = {}, {s: 0.0 for s in self.services}, {}, None

    def decide(self, obs):
        rews, per = [], []
        for s in self.services:
            rps = obs["entries"][obs["services"][s]["entry"]]["rps"]
            self.ewma[s] = EWMA * rps + (1 - EWMA) * self.ewma[s]
            f = features(obs, s, capacity(s), self.targets[s], self.ewma[s])
            if s not in self.win:
                self.win[s] = deque([f] * WINDOW, maxlen=WINDOW)
            else:
                self.win[s].append(f)
            per.append(np.concatenate(self.win[s]))
            if self.prev is not None:
                rews.append(reward(obs, s, self.targets[s], self.last_ready[s]))
            self.last_ready[s] = obs["services"][s]["ready"]
        x = self.agent.norm(self.oj.joint_obs(per))            # statistics frozen from training
        info = {}
        if self.learn:
            if self.prev is not None:
                self.agent.store(self.prev[0], self.prev[1], rews, x, False)
                self.agent.train_step()
                self.agent.on_step()
                info["reward_mean"] = round(float(np.mean(rews)), 4)
            eps = EPS_FINETUNE
            if self.scratch:
                eps = max(EPS_FINETUNE, 1.0 - (1.0 - EPS_FINETUNE) * self.decisions / (0.4 * self.scratch))
            self.decisions += 1
            info["epsilon"] = round(eps, 4)
            act = self.agent.act(x, eps=eps)
        else:
            act = self.agent.act(x, eps=0.0)
        self.prev = (x, act)
        changed = {}
        for s, a in zip(self.services, act):
            t = TARGETS[int(a)]
            if t != self.targets[s]:
                self.targets[s] = changed[s] = t
                d = SERVICES[s]
                patch = {"spec": {"metrics": [{"type": "Resource", "resource": {
                    "name": "cpu", "target": {"type": "Utilization", "averageUtilization": t}}}]}}
                kubectl("-n", d["ns"], "patch", "hpa", f"harness-{d['deploy']}", "--type", "merge", "-p", json.dumps(patch))
        if rews:
            info["rewards"] = {s: round(r, 4) for s, r in zip(self.services, rews)}
        self.last = {"reason": "hpa-targets", "targets": dict(self.targets), "changed": changed, **info}
        return None

    def teardown(self, ctx):
        remove_all()
        if self.params.get("save"):
            self.agent.save(str(ROOT / self.params["save"]), replay=self.learn)
