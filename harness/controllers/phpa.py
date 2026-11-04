"""Predictive HPA (github.com/jthomperoo/predictive-horizontal-pod-autoscaler v0.13.2), native form:
one PredictiveHorizontalPodAutoscaler per scaled service, operator from workloads/phpa/phpa.yaml.

Every sync period it computes the replicas plain HPA would pick (CPU utilization of the 500m
request, same as harness/controllers/hpa.py), keeps the last `history` of them, fits a linear
regression, and scales to the larger of the HPA value and the prediction `lookahead` seconds ahead
(decisionType maximum, the upstream default). Linear, not Holt-Winters: a 75-min run never holds
one full season (decided 2026-09-25, docs/decisions.md). The replica history lives in a ConfigMap
owned by each object, so it is deleted with it and every run starts empty.

Params: target=<cpu % of request> (default 70), lookahead=<s> (default 60), history=<evaluations>
(default 6), sync=<s> (default 15), down=<scale-down stabilization s> (default 300),
up=<scale-up stabilization s> (default 0).
"""
import json

from common import CFG, SERVICES, kubectl

from .base import Controller


class PHPA(Controller):
    native = True

    def _objects(self):
        p = self.params
        for name, s in SERVICES.items():
            yield {
                "apiVersion": "jamiethompson.me/v1alpha1", "kind": "PredictiveHorizontalPodAutoscaler",
                "metadata": {"name": f"harness-{s['deploy']}", "namespace": s["ns"], "labels": {"harness": "phpa"}},
                "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": s["deploy"]},
                         "minReplicas": CFG["min_replicas"], "maxReplicas": CFG["max_replicas"],
                         "syncPeriod": int(p.get("sync", 15)) * 1000, "decisionType": "maximum",
                         "behavior": {"scaleDown": {"stabilizationWindowSeconds": int(p.get("down", 300))},
                                      "scaleUp": {"stabilizationWindowSeconds": int(p.get("up", 0))}},
                         "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {
                             "type": "Utilization", "averageUtilization": int(p.get("target", 70))}}}],
                         "models": [{"type": "Linear", "name": "linear", "perSyncPeriod": 1, "linear": {
                             "lookAhead": int(p.get("lookahead", 60)) * 1000,
                             "historySize": int(p.get("history", 6))}}]}}

    def setup(self, ctx):
        for obj in self._objects():
            kubectl("apply", "-f", "-", input_text=json.dumps(obj))

    def decide(self, obs):
        self.last = {"reason": "native"}
        return None

    def teardown(self, ctx):
        remove_all()


def remove_all():
    for ns in sorted({s["ns"] for s in SERVICES.values()}):
        kubectl("-n", ns, "delete", "predictivehorizontalpodautoscalers.jamiethompson.me", "-l", "harness=phpa",
                "--ignore-not-found", "--wait=true", timeout=90)
