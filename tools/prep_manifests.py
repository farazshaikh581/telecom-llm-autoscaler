"""Prepare upstream workload manifests for the shared-cluster runs.

- Pins every pod to worker nodes (the control node hosts the LLMs).
- Replaces PersistentVolumeClaims with emptyDir (benchmark data need not persist)
  and drops PersistentVolume / PersistentVolumeClaim objects.
- Sets the namespace.

Usage: python3 prep_manifests.py <namespace> <out.yaml> <in.yaml or dir> [...]
"""
import pathlib
import sys

import yaml

WORKER_SELECTOR = {"node.kubernetes.io/microk8s-worker": "microk8s-worker"}

# Upstream fixes, keyed by image repository.
# deathstarbench/hotel-reservation: the repo's manifests start services as "./frontend",
# while the published image (0.3.5, identical to "latest") installs the binaries in
# /go/bin, which is on PATH. Start them by name, and pin the tag.
# Env (harness smoke tests, 2026-09-25): every service has a 1-CPU limit, while the Go runtime
# sees the node's 4 CPUs and gets throttled in bursts. A throttled rate or reservation service
# misses its 2 s memcached deadline and panics (crash loop). GOMAXPROCS matches the limit, and
# MEMC_TIMEOUT (the app's own setting, in s) turns such a stall into a slow answer, not a crash.
IMAGE_FIXES = {
    # image: 0.3.5 rebuilt with the seed-once fix (workloads/hotel), in the in-cluster registry
    "deathstarbench/hotel-reservation": {"tag": "0.3.5", "strip_dot_slash": True,
                                         "image": "localhost:32000/hotel-reservation:0.3.5-seedonce",
                                         "env": {"GOMAXPROCS": "1", "MEMC_TIMEOUT": "10"}},
}

# TeaStore publishes only "latest" (pulled again on every new copy). Pin the digests
# measured on the cluster on 2026-09-25 and pull only when missing.
IMAGE_PINS = {
    "descartesresearch/teastore-auth": "sha256:1babe5ca10bda103daa03f52db3e2357e0d3ec32a9ed549c4b2e2020152b0dac",
    "descartesresearch/teastore-db": "sha256:7a22000cc6b5fa7bfa99f7c8ccca68651ddd44684f5a571341723c839722b60c",
    "descartesresearch/teastore-image": "sha256:8f339e6b8ee7d7b2c3efd173b1a23c7ffda03ab9d0bc5b8904beb6a1f2e2bd98",
    "descartesresearch/teastore-persistence": "sha256:ac6b0fe9aeca04ee31fea20caed0b49af2ddf709c28a2cf1f4b9b041af05279b",
    "descartesresearch/teastore-recommender": "sha256:8cd0cc779929f9a03ac5990d09637f95e24bb333cbcc0b8df139c4c16df93d50",
    "descartesresearch/teastore-registry": "sha256:25000aa7fb7c1798febd464805d363a402cfe4a26877eb4d50874689efcc3e2f",
    "descartesresearch/teastore-webui": "sha256:d0e490bd025167fefaf84d43f978c3c6d14fad9bf3cba9bc49ef902a0d106078",
}

# TeaStore defines no readiness probe. A new front-end copy refuses connections for about
# 4 s and answers its first request only after about 24 s (app deployment and page
# compilation), while Kubernetes marked it Ready after 2 s and sent it traffic.
# The probe is lenient (5 s timeout, 3 failures) to avoid flapping under load.
# memcached (official image, defaults) accepts 1,024 connections. When the hotel app slows down,
# its clients open more connections, memcached refuses them, and the reservation service
# panics on the cache error and crash-loops (harness smoke test, 2026-09-25). Allow 4,096.
MEMCACHED_ARGS = ["-c", "4096"]

READINESS = {
    "teastore-webui": {"httpGet": {"path": "/tools.descartes.teastore.webui/", "port": 8080},
                       "periodSeconds": 2, "timeoutSeconds": 5, "failureThreshold": 3},
}
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}
DROP_KINDS = {"PersistentVolume", "PersistentVolumeClaim"}


# The services controllers scale (decided 2026-09-24). Every one gets the same CPU
# request and limit, and the same placement rule, for every method.
SCALED = {"teastore-webui", "frontend", "search", "reservation", "rate"}
SCALED_CPU = {"requests": "500m", "limits": "1"}


def spread(match_labels):
    """Keep a service's copies evenly spread over the workers (at most 1 copy apart).
    Copies on one worker share physical cores through hyperthreads and slow each
    other down (calibration, 2026-09-24). nodeTaintsPolicy Honor leaves out tainted
    nodes (a powered-off node would otherwise count as an empty domain and block
    scheduling)."""
    return [{
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "DoNotSchedule",
        "nodeTaintsPolicy": "Honor",
        "labelSelector": {"matchLabels": dict(match_labels)},
    }]


def load_all(paths):
    for p in paths:
        p = pathlib.Path(p)
        files = sorted(p.rglob("*.yaml")) + sorted(p.rglob("*.yml")) if p.is_dir() else [p]
        for f in files:
            for doc in yaml.safe_load_all(f.read_text()):
                if doc:
                    yield doc


def prep(doc, namespace):
    kind = doc.get("kind")
    if kind in DROP_KINDS:
        return None
    doc.setdefault("metadata", {})["namespace"] = namespace
    if kind in POD_KINDS:
        spec = doc["spec"]["template"]["spec"]
        spec["nodeSelector"] = dict(WORKER_SELECTOR)
        if kind == "Deployment" and doc["metadata"]["name"] in SCALED:
            doc["metadata"].setdefault("labels", {})["scaled"] = "true"
            spec["topologySpreadConstraints"] = spread(doc["spec"]["selector"]["matchLabels"])
            for c in spec.get("containers", []):
                res = c.setdefault("resources", {})
                for kind_ in ("requests", "limits"):
                    res.setdefault(kind_, {})["cpu"] = SCALED_CPU[kind_]
        for c in spec.get("containers", []):
            repo = c["image"].split(":")[0].split("@")[0]
            if repo in IMAGE_PINS:
                c["image"] = f"{repo}@{IMAGE_PINS[repo]}"
                c["imagePullPolicy"] = "IfNotPresent"
            if doc["metadata"]["name"] in READINESS:
                c["readinessProbe"] = dict(READINESS[doc["metadata"]["name"]])
            if repo == "memcached":
                c["args"] = list(MEMCACHED_ARGS)
            fix = IMAGE_FIXES.get(repo)
            if not fix:
                continue
            c["image"] = fix.get("image", f"{repo}:{fix['tag']}")
            c["imagePullPolicy"] = "IfNotPresent"     # pinned tag: no pull on every restart
            env = [e for e in c.get("env", []) if e["name"] not in fix.get("env", {})]
            c["env"] = env + [{"name": k, "value": v} for k, v in fix.get("env", {}).items()]
            if fix.get("strip_dot_slash") and c.get("command"):
                c["command"] = [a[2:] if a.startswith("./") else a for a in c["command"]]
        for vol in spec.get("volumes", []) or []:
            if "persistentVolumeClaim" in vol:
                del vol["persistentVolumeClaim"]
                vol["emptyDir"] = {}
    return doc


def main():
    namespace, out, *inputs = sys.argv[1:]
    docs = [d for d in (prep(d, namespace) for d in load_all(inputs)) if d]
    pathlib.Path(out).write_text(yaml.safe_dump_all(docs, sort_keys=False))
    kinds = {}
    for d in docs:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    print(f"{out}: {len(docs)} objects {kinds}")


if __name__ == "__main__":
    main()
