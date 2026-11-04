"""AWARE and FIRM on the cluster: the agents trained in the simulator (rl/train_sim.py), one
decision per service every interval (stay, +1 or -1 copy), with the same state and reward as in
training (rl/features.py). Both keep learning online as published (AWARE: PPO update every 5
episodes of 10 decisions per service; FIRM: one DDPG update per transition). Every run starts
from the same checkpoint; what an agent learns during a run is logged, not carried over.

Params: ckpt=<checkpoint path, relative to the repo> (required), save=<path> (write the agent at
the end of the run: cluster fine-tuning passes it on to the next development day).
"""
import sys

import torch

from common import CFG, ROOT, SERVICES

from .base import Controller

sys.path.insert(0, str(ROOT / "rl"))
sys.path.insert(0, str(ROOT / "tools/traces"))
import features  # noqa: E402
from build_traces import SCALED_CAP  # noqa: E402

CAPACITY = {s: SCALED_CAP[v["entry"]] for s, v in SERVICES.items()}
EP_LEN = 10


class _PerServiceRL(Controller):
    agent_cls = None

    def setup(self, ctx):
        torch.set_num_threads(1)
        self.agent = self.make_agent()
        self.agent.load(str(ROOT / self.params["ckpt"]))
        self.prev, self.last_delta = {}, {s: 0 for s in SERVICES}
        self.eps = {s: self._empty() for s in SERVICES}

    def teardown(self, ctx):
        if self.params.get("save"):
            self.agent.save(str(ROOT / self.params["save"]))

    @staticmethod
    def _empty():
        return {"states": [], "actions": [], "rewards": [], "log_probs": []}

    def decide(self, obs):
        want, log = {}, {}
        for s in SERVICES:
            st = features.state(obs, s, CAPACITY[s])
            rec = {"state": [round(x, 4) for x in st]}
            if s in self.prev:
                pst, pa, extra, illegal = self.prev[s]
                delta = self.delta(pa)
                r = -1.0 if illegal else features.reward(st, delta, self.last_delta[s])
                self.last_delta[s] = 0 if illegal else delta
                rec["reward"] = round(r, 4)
                if hasattr(self.agent, "step"):
                    self.agent.step(s, pst, extra, r, st)
                e = self.eps[s]
                e["states"].append(pst); e["actions"].append(pa); e["rewards"].append(r); e["log_probs"].append(extra)
                if len(e["states"]) == EP_LEN:
                    self.agent.end_episode(s, e)
                    self.eps[s] = self._empty()
            a, extra = self.agent.act(s, st)
            cur = obs["services"][s]["desired"]
            n = cur + self.delta(a)
            illegal = not (CFG["min_replicas"] <= n <= CFG["max_replicas"])
            if not illegal and n != cur:
                want[s] = n
            self.prev[s] = (st, a, extra, illegal)
            rec["action"] = self.delta(a)
            log[s] = rec
        self.last = {"reason": "policy", "services": log, "updates": self.agent.updates}
        return want


class AWARE(_PerServiceRL):
    def make_agent(self):
        from aware_agent import ACTION_DELTA, AwareAgent
        self.delta = ACTION_DELTA.__getitem__
        return AwareAgent(seed=int(self.params.get("seed", 0)))


class FIRM(_PerServiceRL):
    def make_agent(self):
        from firm_agent import ACTION_DELTA, FirmAgent
        self.delta = ACTION_DELTA.__getitem__
        return FirmAgent(seed=int(self.params.get("seed", 0)))
