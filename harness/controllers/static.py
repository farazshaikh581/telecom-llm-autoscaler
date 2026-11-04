"""Static provisioning: fixed replicas for the whole run.
Params: replicas=<n> for every service, or <service>=<n> per service (others keep their start value).
"""
from common import SERVICES

from .base import Controller


class Static(Controller):
    def decide(self, obs):
        if "replicas" in self.params:
            want = {s: int(self.params["replicas"]) for s in SERVICES}
        else:
            want = {s: int(v) for s, v in self.params.items() if s in SERVICES}
        self.last = {"reason": "static"}
        return want
