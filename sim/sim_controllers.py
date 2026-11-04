"""Controllers inside the simulator, for closed-loop checks and training.

HPA: the Kubernetes algorithm (autoscaling/v2) at its default 15 s sync: desired = ceil(ready x
utilization / target), skipped within the 10% tolerance; scale-up limited to max(4 copies, 100%)
per 15 s; scale-down to the highest recommendation of the stabilization window (300 s).
Utilization over the last 30 s (metrics-server resolution 15 s, scrape lag).
"""
import collections
import math

from cluster_sim import SCALED as SERVICES   # HPA acts on the scaled services only


class SimHPA:
    sync_s = 15

    def __init__(self, target=70, down=300, window=30, tolerance=0.1):
        """target: CPU % for every service, or {service: %} (changeable at run time via .targets)."""
        t = target if isinstance(target, dict) else {s: target for s in SERVICES}
        self.targets = {s: v / 100 for s, v in t.items()}
        self.down, self.window, self.tol = down, window, tolerance
        self.recs = {s: collections.deque() for s in SERVICES}

    def tick(self, sim):
        want = {}
        for s in SERVICES:
            ready = sim.ready(s, sim.t)
            cur = sim.desired[s]
            if ready == 0:
                continue
            ratio = sim.cpu_util(s, self.window) / self.targets[s]
            rec = cur if abs(ratio - 1) <= self.tol else math.ceil(ready * ratio)
            rec = max(sim.lo, min(sim.hi, rec))
            q = self.recs[s]
            q.append((sim.t, rec))
            while q and q[0][0] < sim.t - self.down:
                q.popleft()
            if rec < cur:
                rec = min(cur, max(r for _, r in q))          # stabilized scale-down
            else:
                rec = min(rec, max(cur * 2, cur + 4))           # scale-up rate limit
            if rec != cur:
                want[s] = rec
        return want


def run_closed_loop(sim, ctrl, start, duration):
    """Drive the simulator with a native-style controller that acts every sync_s seconds."""
    sim.reset(start)
    actions = 0
    while sim.t < duration:
        sim.advance(ctrl.sync_s)
        want = ctrl.tick(sim)
        if want:
            actions += len(want)
            sim.scale(want)
    return actions
