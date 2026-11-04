"""FIRM's RL core (DDPG), as re-implemented in AWARE `testing/ddpg/` (commit 7cd13d6,
Apache-2.0; FIRM: Qian et al., OSDI 2020). RL core only, not the full FIRM system.

Kept as published: actor and critic (40-40 hidden units, batch norm, fan-in init), the actor's
action scores with Ornstein-Uhlenbeck noise scaled by epsilon (1.0, decay 1e-6 per update), the
highest score picks the action, replay buffer 100,000, batch 64, warm-up 70 transitions, one
update per step, soft target updates (tau 0.001), discount 0.9, learning rates 3e-4 / 3e-3.
Changed: 4 state features and 3 actions (stay, +1, -1; vertical actions removed). Epsilon is
floored at 0: the published decay has no floor and would turn negative after 1M updates
(the published budget is 2.7M), flipping and then growing the noise. One agent
shared by all services (FIRM's one-for-all setting); it keeps learning online on the cluster
as it does in training.
"""
import random
from collections import deque
from copy import deepcopy

import numpy as np
import torch
from torch import nn

from features import N_STATE

N_ACTIONS = 3
ACTION_DELTA = {0: 0, 1: +1, 2: -1}
ACTOR_LR, CRITIC_LR = 3e-4, 3e-3
SIGMA = 0.2
BUFFER_SIZE = 100000
MINIBATCH_SIZE = 64
DISCOUNT = 0.9
EPSILON, EPSILON_DECAY = 1.0, 1e-6
TAU = 0.001
WARMUP = 70
HIDDEN1 = HIDDEN2 = 40
W_FINAL = 0.003


def fan_in_init(size):
    w = 1. / np.sqrt(size[0])
    return torch.Tensor(size).uniform_(-w, w)


class Actor(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm0 = nn.BatchNorm1d(N_STATE)
        self.fc1 = nn.Linear(N_STATE, HIDDEN1)
        self.fc1.weight.data = fan_in_init(self.fc1.weight.data.size())
        self.bn1 = nn.BatchNorm1d(HIDDEN1)
        self.fc2 = nn.Linear(HIDDEN1, HIDDEN2)
        self.fc2.weight.data = fan_in_init(self.fc2.weight.data.size())
        self.bn2 = nn.BatchNorm1d(HIDDEN2)
        self.fc3 = nn.Linear(HIDDEN2, N_ACTIONS)
        self.fc3.weight.data.uniform_(-W_FINAL, W_FINAL)

    def forward(self, x):
        h1 = self.bn1(torch.relu(self.fc1(self.norm0(x))))
        h2 = self.bn2(torch.relu(self.fc2(h1)))
        return torch.tanh(self.fc3(h2))


class Critic(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(N_STATE, HIDDEN1)
        self.fc1.weight.data = fan_in_init(self.fc1.weight.data.size())
        self.bn1 = nn.BatchNorm1d(HIDDEN1)
        self.fc2 = nn.Linear(HIDDEN1 + N_ACTIONS, HIDDEN2)
        self.fc2.weight.data = fan_in_init(self.fc2.weight.data.size())
        self.fc3 = nn.Linear(HIDDEN2, 1)
        self.fc3.weight.data.uniform_(-W_FINAL, W_FINAL)

    def forward(self, x, a):
        h1 = self.bn1(torch.relu(self.fc1(x)))
        return self.fc3(torch.relu(self.fc2(torch.cat([h1, a], dim=1))))


class OUNoise:
    def __init__(self, rng, mu, sigma, theta=.15, dt=1e-2):
        self.rng, self.mu, self.sigma, self.theta, self.dt = rng, mu, sigma, theta, dt
        self.x = np.zeros_like(mu)

    def __call__(self):
        self.x = self.x + self.theta * (self.mu - self.x) * self.dt + \
            self.sigma * np.sqrt(self.dt) * self.rng.normal(size=self.mu.shape)
        return self.x


class FirmAgent:
    def __init__(self, seed=0):
        torch.manual_seed(seed)
        random.seed(seed)
        self.rng = np.random.default_rng(seed)
        self.actor, self.critic = Actor(), Critic()
        self.target_actor, self.target_critic = deepcopy(Actor()), deepcopy(Critic())
        self.actor_opt = torch.optim.Adam(self.actor.parameters(), lr=ACTOR_LR)
        self.critic_opt = torch.optim.Adam(self.critic.parameters(), lr=CRITIC_LR)
        self.noise = OUNoise(self.rng, np.zeros(N_ACTIONS), SIGMA)
        self.buffer = deque(maxlen=BUFFER_SIZE)
        self.epsilon = EPSILON
        self.updates = 0
        self.learning = True

    @torch.no_grad()
    def act(self, service, state, greedy=False):
        self.actor.eval()
        a = self.actor(torch.as_tensor([state], dtype=torch.float32))[0].numpy()
        self.actor.train()
        if not greedy:
            a = a + self.epsilon * self.noise()
        return int(np.argmax(a)), a.astype(np.float32)

    def step(self, service, state, action_vec, reward, next_state):
        """One transition; one update once the buffer holds the warm-up."""
        self.buffer.append((np.asarray(state, np.float32), action_vec, np.asarray(next_state, np.float32), reward))
        if not self.learning or len(self.buffer) < max(WARMUP, MINIBATCH_SIZE):
            return
        batch = random.sample(self.buffer, MINIBATCH_SIZE)
        s = torch.as_tensor(np.array([b[0] for b in batch]))
        a = torch.as_tensor(np.array([b[1] for b in batch]))
        s2 = torch.as_tensor(np.array([b[2] for b in batch]))
        r = torch.as_tensor([b[3] for b in batch], dtype=torch.float32).view(-1, 1)
        with torch.no_grad():
            q_target = r + DISCOUNT * self.target_critic(s2, self.target_actor(s2))
        q = self.critic(s, a)
        self.critic_opt.zero_grad()
        nn.functional.mse_loss(q, q_target).backward()
        self.critic_opt.step()
        self.actor_opt.zero_grad()
        (-self.critic(s, self.actor(s)).mean()).backward()
        self.actor_opt.step()
        for tgt, src in ((self.target_actor, self.actor), (self.target_critic, self.critic)):
            for tp, p in zip(tgt.parameters(), src.parameters()):
                tp.data.copy_((1 - TAU) * tp.data + TAU * p.data)
        self.epsilon = max(0.0, self.epsilon - EPSILON_DECAY)
        self.updates += 1

    def end_episode(self, service, ep):
        pass

    def save(self, path):
        torch.save({k: getattr(self, k).state_dict() for k in
                    ("actor", "critic", "target_actor", "target_critic", "actor_opt", "critic_opt")} |
                   {"epsilon": self.epsilon, "updates": self.updates, "buffer": list(self.buffer)}, path)

    def load(self, path):
        ck = torch.load(path, weights_only=False)
        for k in ("actor", "critic", "target_actor", "target_critic", "actor_opt", "critic_opt"):
            getattr(self, k).load_state_dict(ck[k])
        self.epsilon, self.updates = ck["epsilon"], ck["updates"]
        self.buffer = deque(ck["buffer"], maxlen=BUFFER_SIZE)
