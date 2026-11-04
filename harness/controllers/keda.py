"""KEDA, native form: one ScaledObject per scaled service on the request rate of its app
(KEDA metrics-api scaler, AverageValue: copies = ceil(rate / target per copy)).

The rate is the one every controller sees: requests sent by the load generator over the last
`window` seconds. The harness serves it to the KEDA operator from a small HTTP endpoint on the
control node (GET /rate?entry=<entry> -> {"rps": x}); the HTTP add-on is not used because its
proxy would sit in the request path and change the measured latency.
Target per copy = util x the per-copy SLO capacity (config/trace_scaling.yaml; hotel services
per service, in hotel entry req/s), the same capacities the harness uses for start sizes.

Params: util=<fraction of per-copy capacity> (default 0.7), poll=<KEDA polling s> (default 15),
window=<rate window s> (default 30), down=<scale-down stabilization s> (default 300),
up=<scale-up stabilization s> (default 0).
"""
import http.server
import json
import sys
import threading
import urllib.parse
import urllib.request

from common import CFG, ROOT, SERVICES, kubectl, kubectl_json

from .base import Controller

sys.path.insert(0, str(ROOT / "tools/traces"))
from build_traces import SCALED_CAP  # noqa: E402  per-copy capacity of each app's scaled service

PORT = 9902


def node_ip():
    """InternalIP of the control node (where the harness runs), reachable from the KEDA operator
    pod. Found by its label, not by hostname: node names are lowercased hostnames."""
    node = kubectl_json("get", "nodes", "-l", "node.kubernetes.io/microk8s-controlplane")["items"][0]
    return next(a["address"] for a in node["status"]["addresses"] if a["type"] == "InternalIP")


class _RateServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def _handler(window):
    lg_addr = CFG["loadgen"]["listen"]

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path)
            entry = urllib.parse.parse_qs(q.query).get("entry", [""])[0]
            try:
                with urllib.request.urlopen(f"http://{lg_addr}/stats?window={window}", timeout=5) as r:
                    rps = json.load(r)["entries"][entry]["sent_rps"]
                code, body = 200, {"rps": round(rps, 3)}
            except Exception as e:  # noqa: BLE001  KEDA treats a non-200 as a failed poll
                code, body = 503, {"error": str(e)[:200]}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    return H


class KEDA(Controller):
    native = True

    def targets(self):
        util = float(self.params.get("util", 0.7))
        return {name: round(util * SCALED_CAP[s["entry"]], 3) for name, s in SERVICES.items()}

    def _objects(self, ip):
        poll = int(self.params.get("poll", 15))
        down = int(self.params.get("down", 300))
        up = int(self.params.get("up", 0))
        for name, s in SERVICES.items():
            yield {
                "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
                "metadata": {"name": f"harness-{s['deploy']}", "namespace": s["ns"], "labels": {"harness": "keda"}},
                "spec": {"scaleTargetRef": {"name": s["deploy"]}, "pollingInterval": poll,
                         "minReplicaCount": CFG["min_replicas"], "maxReplicaCount": CFG["max_replicas"],
                         "advanced": {"horizontalPodAutoscalerConfig": {"behavior": {
                             "scaleDown": {"stabilizationWindowSeconds": down},
                             "scaleUp": {"stabilizationWindowSeconds": up}}}},
                         "triggers": [{"type": "metrics-api", "metricType": "AverageValue", "metadata": {
                             "url": f"http://{ip}:{PORT}/rate?entry={s['entry']}", "valueLocation": "rps",
                             "targetValue": str(self.targets()[name])}}]}}

    def setup(self, ctx):
        ip = node_ip()
        self.server = _RateServer((ip, PORT), _handler(int(self.params.get("window", 30))))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        for obj in self._objects(ip):
            kubectl("apply", "-f", "-", input_text=json.dumps(obj))

    def decide(self, obs):
        self.last = {"reason": "native"}
        return None

    def teardown(self, ctx):
        remove_all()
        if getattr(self, "server", None):
            self.server.shutdown()

    def info(self):
        return dict(super().info(), target_rps_per_copy=self.targets())


def remove_all():
    for ns in sorted({s["ns"] for s in SERVICES.values()}):
        kubectl("-n", ns, "delete", "scaledobject", "-l", "harness=keda", "--ignore-not-found")
