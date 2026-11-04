"""Pin the fixed (unscaled) pods to the workers listed in workloads/placement.yaml.

Labels the workers with their role (w0..w3, sorted by name, as in tools/cluster_fingerprint.py)
and adds that label to each fixed deployment's nodeSelector. Pods that move are restarted by
Kubernetes; nothing else changes.
  python tools/pin_fixed_pods.py check   -> where each fixed pod runs now vs the plan
  python tools/pin_fixed_pods.py apply   -> label workers, patch deployments, wait for rollout
"""
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAN = yaml.safe_load(open(ROOT / "workloads/placement.yaml"))
LABEL = PLAN.pop("label")
WORKER = "node.kubernetes.io/microk8s-worker"


def kubectl(*a):
    return subprocess.run(["kubectl", *a], capture_output=True, text=True, check=True).stdout


def workers():
    nodes = json.loads(kubectl("get", "nodes", "-l", WORKER, "-o", "json"))["items"]
    return sorted(n["metadata"]["name"] for n in nodes)


def plan():
    """(namespace, deployment) -> role"""
    return {(ns, dep): role for role, apps in PLAN.items() for ns, deps in apps.items() for dep in deps}


def check():
    names = workers()
    role_of = {n: f"w{i}" for i, n in enumerate(names)}
    bad = 0
    for (ns, dep), role in sorted(plan().items()):
        pods = json.loads(kubectl("-n", ns, "get", "pods", "-o", "json"))["items"]
        where = sorted({role_of.get(p["spec"].get("nodeName"), "other") for p in pods
                        if p["metadata"]["name"].rsplit("-", 2)[0] == dep and not p["metadata"].get("deletionTimestamp")})
        ok = where == [role]
        bad += not ok
        print(f"{'ok ' if ok else 'MOVE'} {ns}/{dep}: {','.join(where) or 'none'} -> {role}")
    return bad


def apply():
    names = workers()
    assert len(names) == len(PLAN), f"{len(names)} workers, placement lists {len(PLAN)}"
    for i, n in enumerate(names):
        kubectl("label", "node", n, f"{LABEL}=w{i}", "--overwrite")
    for (ns, dep), role in sorted(plan().items()):
        patch = {"spec": {"template": {"spec": {"nodeSelector": {WORKER: "microk8s-worker", LABEL: role}}}}}
        kubectl("-n", ns, "patch", "deploy", dep, "-p", json.dumps(patch))
    for (ns, dep) in sorted(plan()):
        kubectl("-n", ns, "rollout", "status", f"deploy/{dep}", "--timeout=300s")
    return check()


if __name__ == "__main__":
    sys.exit(1 if {"check": check, "apply": apply}[sys.argv[1]]() else 0)
