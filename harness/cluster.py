"""Read and change the scaled services through the Kubernetes API (kubectl)."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from common import CFG, SERVICES, kubectl, kubectl_json

NAMESPACES = sorted({s["ns"] for s in SERVICES.values()})
_POD = {name: re.compile(rf"^{re.escape(s['deploy'])}-[a-z0-9]+-[a-z0-9]+$") for name, s in SERVICES.items()}


def service_of(ns, pod):
    for name, s in SERVICES.items():
        if s["ns"] == ns and _POD[name].match(pod):
            return name
    return None


def replicas():
    """Per service: desired (spec), ready, updated counts."""
    out = {}
    for ns in NAMESPACES:
        for d in kubectl_json("-n", ns, "get", "deploy")["items"]:
            for name, s in SERVICES.items():
                if s["ns"] == ns and s["deploy"] == d["metadata"]["name"]:
                    st = d.get("status", {})
                    out[name] = {"desired": d["spec"]["replicas"], "ready": st.get("readyReplicas", 0),
                                 "available": st.get("availableReplicas", 0)}
    return out


_ROLES = {}


def node_role(node):
    """Role of a node as recorded in results (w0..w3 = workers by their llm-autoscaler/slot label,
    tools/pin_fixed_pods.py; control = the control node), so results hold no host names."""
    if node not in _ROLES:
        for n in kubectl_json("get", "nodes")["items"]:
            labels = n["metadata"].get("labels", {})
            _ROLES[n["metadata"]["name"]] = labels.get("llm-autoscaler/slot") or (
                "control" if "node.kubernetes.io/microk8s-controlplane" in labels else "other")
    return _ROLES.get(node, "other")


def pods():
    """Per service: pods by phase, not-ready count, and node placement."""
    out = {name: {"running_ready": 0, "running_not_ready": 0, "pending": 0, "terminating": 0, "nodes": {}}
           for name in SERVICES}
    for ns in NAMESPACES:
        for p in kubectl_json("-n", ns, "get", "pods")["items"]:
            name = service_of(ns, p["metadata"]["name"])
            if not name:
                continue
            o = out[name]
            if p["metadata"].get("deletionTimestamp"):
                o["terminating"] += 1
                continue
            phase = p["status"].get("phase")
            if phase == "Pending":
                o["pending"] += 1
            elif phase == "Running":
                ready = any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))
                o["running_ready" if ready else "running_not_ready"] += 1
            node = p["spec"].get("nodeName")
            if node:
                node = node_role(node)
                o["nodes"][node] = o["nodes"].get(node, 0) + 1
    return out


def _cpu_m(v):
    if v.endswith("n"):
        return int(v[:-1]) / 1e6
    if v.endswith("u"):
        return int(v[:-1]) / 1e3
    if v.endswith("m"):
        return float(v[:-1])
    return float(v) * 1000


def _mem_mi(v):
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for u, f in units.items():
        if v.endswith(u):
            return float(v[:-2]) * f
    return float(v) / 2**20


def pod_metrics():
    """metrics-server (15 s resolution): per service total CPU (millicores), memory (MiB), pods seen."""
    out = {name: {"cpu_m": 0.0, "mem_mi": 0.0, "pods": 0, "per_pod_cpu_m": []} for name in SERVICES}
    for ns in NAMESPACES:
        raw = kubectl("get", "--raw", f"/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods")
        for p in json.loads(raw)["items"]:
            name = service_of(ns, p["metadata"]["name"])
            if not name:
                continue
            cpu = sum(_cpu_m(c["usage"]["cpu"]) for c in p["containers"])
            mem = sum(_mem_mi(c["usage"]["memory"]) for c in p["containers"])
            o = out[name]
            o["cpu_m"] += cpu
            o["mem_mi"] += mem
            o["pods"] += 1
            o["per_pod_cpu_m"].append(round(cpu, 1))
    for o in out.values():
        o["cpu_m"] = round(o["cpu_m"], 1)
        o["mem_mi"] = round(o["mem_mi"], 1)
    return out


def clamp(n):
    return max(CFG["min_replicas"], min(CFG["max_replicas"], int(n)))


def scale(targets):
    """targets: service -> replicas. Applies all changes in parallel; returns what was applied."""
    applied = {}

    def one(item):
        name, n = item
        s = SERVICES[name]
        kubectl("-n", s["ns"], "scale", f"deploy/{s['deploy']}", f"--replicas={n}")
        return name, n

    items = [(k, clamp(v)) for k, v in targets.items()]
    with ThreadPoolExecutor(len(items) or 1) as ex:
        for name, n in ex.map(one, items):
            applied[name] = n
    return applied


FIXED = CFG.get("fixed", {})


def set_fixed(timeout=300):
    """Unscaled services of the applications (config/harness.yaml "fixed"): set their copies and wait
    until they are rolled out. No controller sees or changes them. Returns {name: copies}."""
    for f in FIXED.values():
        kubectl("-n", f["ns"], "scale", f"deploy/{f['deploy']}", f"--replicas={f['copies']}")
    for f in FIXED.values():
        kubectl("-n", f["ns"], "rollout", "status", f"deploy/{f['deploy']}", f"--timeout={timeout}s", timeout=timeout + 30)
    return {name: f["copies"] for name, f in FIXED.items()}


def wait_ready(targets, timeout=600):
    """Wait until every service has exactly its target of ready pods and nothing terminating."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r, p = replicas(), pods()
        if all(r[k]["ready"] == v and p[k]["terminating"] == 0 and p[k]["pending"] == 0 for k, v in targets.items()):
            return time.time() - t0
        time.sleep(3)
    raise TimeoutError(f"services not ready after {timeout} s: {replicas()}")


def entry_ip(entry_cfg):
    return kubectl("-n", entry_cfg["ns"], "get", "svc", entry_cfg["svc"], "-o", "jsonpath={.spec.clusterIP}")
