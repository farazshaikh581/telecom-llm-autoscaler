"""Kubernetes Horizontal Pod Autoscaler, native form: one HPA per scaled service on CPU
utilization of the 500m request. The HPA controller runs its own loop (cluster default: 15 s);
the harness only creates and removes the objects.
Params: target=<cpu % of request> (default 70), down=<scale-down stabilization s> (default 300),
up=<scale-up stabilization s> (default 0).
"""
import json

from common import CFG, SERVICES, kubectl

from .base import Controller


class HPA(Controller):
    native = True

    def _objects(self):
        target = int(self.params.get("target", 70))
        down = int(self.params.get("down", 300))
        up = int(self.params.get("up", 0))
        for name, s in SERVICES.items():
            yield s["ns"], {
                "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
                "metadata": {"name": f"harness-{s['deploy']}", "namespace": s["ns"], "labels": {"harness": "hpa"}},
                "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": s["deploy"]},
                         "minReplicas": CFG["min_replicas"], "maxReplicas": CFG["max_replicas"],
                         "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {
                             "type": "Utilization", "averageUtilization": target}}}],
                         "behavior": {"scaleDown": {"stabilizationWindowSeconds": down},
                                      "scaleUp": {"stabilizationWindowSeconds": up}}}}

    def setup(self, ctx):
        for ns, obj in self._objects():
            kubectl("apply", "-f", "-", input_text=json.dumps(obj))

    def decide(self, obs):
        self.last = {"reason": "native"}
        return None

    def teardown(self, ctx):
        remove_all()


def remove_all():
    for ns in sorted({s["ns"] for s in SERVICES.values()}):
        kubectl("-n", ns, "delete", "hpa", "-l", "harness=hpa", "--ignore-not-found")
