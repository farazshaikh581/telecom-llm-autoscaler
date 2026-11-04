"""AWARE's meta-learning PPO agent, ported from AWARE `testing/ppo/meta_ppo.py` and
`testing/ppo/rnn.py` (gitlab.engr.illinois.edu/DEPEND/aware, commit 7cd13d6, Apache-2.0,
Qiu et al., USENIX ATC 2023).

Kept as published: the networks (actor and critic with an RNN embedding of the episode buffer,
including the GRU that runs over the feature axis), the 'best' episode buffer (32), PPO
hyperparameters, 10-step episodes and an update every 5 episodes. Changed: 4 state features
and 3 actions (stay, +1 copy, -1 copy; vertical actions removed), and the training loop is
split into act() and end_episode() so the same agent runs in the simulator and on the cluster.

Retraining trigger: published thresholds (mean episode reward >= 100-120, std < 10) can never
be met with rewards <= 1 per step over 10 steps, so the published agent never stops learning;
we keep that (online updates continue on the cluster, docs/decisions.md).
"""
import heapq

import numpy as np
import torch
from torch import nn

from features import N_STATE

N_ACTIONS = 3                  # 0 stay, 1 scale out, 2 scale in
ACTION_DELTA = {0: 0, 1: +1, 2: -1}

EPISODES_PER_ITERATION = 5
EPISODE_LENGTH = 10
DISCOUNT = 0.99
HIDDEN_SIZE = 64
LR = 3e-4
SGD_EPOCHS = 5
MINI_BATCH_SIZE = 5
CLIP = 0.2
ENTROPY_COEFFICIENT = 0.01
CRITIC_LOSS_DISCOUNT = 0.05
MAX_TIMESTEPS_PER_EPISODE = 10
BUFFER_SIZE = 32
RNN_HIDDEN_SIZE = 256
RNN_NUM_LAYERS = 2
EMBEDDING_DIM = 32
REWARD_AVG_THRESHOLD = 120
REWARD_STD_THRESHOLD = 10
MAX_NUM_REWARDS_TO_CHECK = 10


class RNNEmbedding(nn.Module):
    def __init__(self, num_channels=MAX_TIMESTEPS_PER_EPISODE):
        super().__init__()
        self.hidden_size, self.num_layers, self.directions = RNN_HIDDEN_SIZE, RNN_NUM_LAYERS, 2
        self.gru = nn.GRU(num_channels, self.hidden_size, num_layers=self.num_layers, batch_first=True,
                          bidirectional=True)
        self.embedding_layer = nn.Linear(self.hidden_size * self.num_layers * self.directions, EMBEDDING_DIM)
        self.relu = nn.ReLU()
        self.embedding_dim = EMBEDDING_DIM

    def init_hidden(self, num_sequences=1):
        return torch.zeros((self.directions * self.num_layers, num_sequences, self.hidden_size))


def padded_trajectories(buffer, state_dim, action_dim):
    batch = max(len(buffer), 1)
    s = torch.zeros(batch, state_dim, MAX_TIMESTEPS_PER_EPISODE)
    a = torch.zeros(batch, action_dim, MAX_TIMESTEPS_PER_EPISODE)
    r = torch.zeros(batch, 1, MAX_TIMESTEPS_PER_EPISODE)
    for i, traj in enumerate(buffer.values()):
        for j in range(len(traj["states"])):
            s[i, :, j] = torch.tensor(traj["states"][j], dtype=torch.float32)
            a[i, 0, j] = float(traj["actions"][j])
            r[i, 0, j] = float(traj["rewards"][j])
    return s, a, r


class MetaNet(nn.Module):
    """Actor (softmax over actions) or critic (value), input = state + buffer embedding."""

    def __init__(self, agent, output_size, softmax):
        super().__init__()
        self.agent = agent
        self.rnn = RNNEmbedding()
        self.fc1 = nn.Linear(N_STATE + EMBEDDING_DIM, HIDDEN_SIZE)
        self.fc2 = nn.Linear(HIDDEN_SIZE, HIDDEN_SIZE)
        self.fc3 = nn.Linear(HIDDEN_SIZE, output_size)
        self.relu = nn.ReLU()
        self.softmax = nn.Softmax(dim=-1) if softmax else None

    def embedding(self):
        s, a, r = padded_trajectories(self.agent.buffer, N_STATE, N_ACTIONS)
        x = torch.cat((s, a, r), dim=-2)
        hidden = self.rnn.init_hidden(num_sequences=x.shape[0])
        _, h = self.rnn.gru(x, hidden)
        h = torch.mean(h, dim=1, keepdim=True).view((1, 1, -1))
        return self.rnn.relu(self.rnn.embedding_layer(h[0][0]))

    def forward(self, x):
        x = torch.as_tensor(np.asarray(x), dtype=torch.float32)
        emb = self.embedding()
        if x.dim() > 1:
            x = torch.cat((x, emb.reshape(1, -1).repeat(x.size(0), 1)), dim=1)
        else:
            x = torch.cat((x, emb), dim=-1)
        out = self.fc3(self.relu(self.fc2(self.relu(self.fc1(x)))))
        return self.softmax(out) if self.softmax else out


class AwareAgent:
    """One shared meta-policy; one episode buffer (the embedding's input) per service."""

    def __init__(self, seed=0):
        torch.manual_seed(seed)
        self.rng = np.random.default_rng(seed)
        self.buffers = {}
        self.buffer = {}
        self.actor = MetaNet(self, N_ACTIONS, softmax=True)
        self.critic = MetaNet(self, 1, softmax=False)
        self.optimizer = torch.optim.Adam(list(self.actor.parameters()) + list(self.critic.parameters()), lr=LR)
        self.learning = True
        self.recent_rewards = {}
        self.pending = {}          # per service: finished episodes waiting for the next update
        self.updates = 0

    def use(self, service):
        """Point the embedding at this service's episode buffer."""
        if service not in self.buffers:
            self.buffers[service] = {"_rewards": [], "eps": {}}
        self.buffer = self.buffers[service]["eps"]

    @torch.no_grad()
    def act(self, service, state, greedy=False):
        self.use(service)
        probs = self.actor(state)
        dist = torch.distributions.Categorical(probs)
        a = torch.argmax(probs) if greedy else dist.sample()
        return int(a), float(dist.log_prob(a))

    def _add_to_buffer(self, service, ep):
        b = self.buffers[service]
        reward = round(float(np.sum(ep["rewards"])), 3)
        if len(b["eps"]) >= BUFFER_SIZE:
            if reward >= b["_rewards"][0]:
                removed = heapq.heappop(b["_rewards"])
                del b["eps"][removed]
            else:
                return
        if reward in b["eps"]:
            return
        heapq.heappush(b["_rewards"], reward)
        b["eps"][reward] = {"states": ep["states"], "actions": ep["actions"], "rewards": ep["rewards"]}

    def end_episode(self, service, ep):
        """ep: dict of lists states, actions, rewards, log_probs (one episode of <= 10 steps).
        Updates the buffer and runs a PPO update every 5 episodes of this service."""
        self.use(service)
        self._add_to_buffer(service, ep)
        rr = self.recent_rewards.setdefault(service, [])
        rr.append(float(np.sum(ep["rewards"])))
        del rr[:-MAX_NUM_REWARDS_TO_CHECK]
        if len(rr) >= MAX_NUM_REWARDS_TO_CHECK:
            done = np.mean(rr) >= REWARD_AVG_THRESHOLD and np.std(rr) < REWARD_STD_THRESHOLD
            self.learning = not done
        p = self.pending.setdefault(service, [])
        p.append(ep)
        if len(p) >= EPISODES_PER_ITERATION:
            self.pending[service] = []
            if self.learning:
                self._update(service, p)

    def _update(self, service, episodes):
        self.use(service)
        states = torch.as_tensor(np.array([s for ep in episodes for s in ep["states"]]), dtype=torch.float32)
        actions = torch.as_tensor([a for ep in episodes for a in ep["actions"]], dtype=torch.int64)
        log_probs = torch.as_tensor([lp for ep in episodes for lp in ep["log_probs"]], dtype=torch.float32)
        returns = []
        for ep in reversed(episodes):
            g = 0.0
            for r in reversed(ep["rewards"]):
                g = r + g * DISCOUNT
                returns.insert(0, g)
        returns = torch.as_tensor(returns, dtype=torch.float32)
        values = self.critic(states).squeeze()
        adv = returns - values.detach()
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)
        n = states.size(0)
        for _ in range(SGD_EPOCHS):
            for _ in range(n // MINI_BATCH_SIZE):
                ids = torch.as_tensor(self.rng.integers(0, n, MINI_BATCH_SIZE))
                v = self.critic(states[ids]).squeeze()
                dist = torch.distributions.Categorical(self.actor(states[ids]))
                lp = dist.log_prob(actions[ids])
                ratio = (lp - log_probs[ids]).exp()
                s1, s2 = ratio * adv[ids], torch.clamp(ratio, 1 - CLIP, 1 + CLIP) * adv[ids]
                loss = -torch.min(s1, s2).mean() + CRITIC_LOSS_DISCOUNT * (returns[ids] - v).pow(2).mean() \
                    - ENTROPY_COEFFICIENT * dist.entropy().mean()
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()
        self.updates += 1

    def save(self, path):
        torch.save({"actor": self.actor.state_dict(), "critic": self.critic.state_dict(),
                    "optimizer": self.optimizer.state_dict(), "buffers": self.buffers,
                    "updates": self.updates}, path)

    def load(self, path):
        ck = torch.load(path, weights_only=False)
        self.actor.load_state_dict(ck["actor"])
        self.critic.load_state_dict(ck["critic"])
        self.optimizer.load_state_dict(ck["optimizer"])
        self.buffers, self.updates = ck["buffers"], ck["updates"]
