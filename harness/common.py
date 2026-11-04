"""Shared helpers: configuration, kubectl, JSON-lines logs."""
import json
import os
import pathlib
import subprocess
import threading
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
# HARNESS_CONFIG / SCALING_CONFIG select full alternative copies (new-service shift, docs/decisions.md 2026-10-28)
CFG = yaml.safe_load(open(ROOT / os.environ.get("HARNESS_CONFIG", "config/harness.yaml")))
SCALING = yaml.safe_load(open(ROOT / os.environ.get("SCALING_CONFIG", "config/trace_scaling.yaml")))
SERVICES = CFG["services"]
ENTRIES = CFG["entries"]
SLO_MS = {e: SCALING["entries"][e]["slo_ms"] for e in ENTRIES}
CAPACITY = {e: SCALING["entries"][e]["capacity_per_copy_rps"] for e in ENTRIES}


def kubectl(*args, timeout=30, input_text=None):
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=timeout, input=input_text)
    if r.returncode:
        raise RuntimeError(f"kubectl {' '.join(args[:4])}: {r.stderr.strip()[:300]}")
    return r.stdout


def kubectl_json(*args, timeout=30):
    return json.loads(kubectl(*args, "-o", "json", timeout=timeout))


class JsonLog:
    """Append-only JSON-lines file, safe to write from several threads."""

    def __init__(self, path):
        self.f = open(path, "a", buffering=1)
        self.lock = threading.Lock()

    def write(self, rec):
        with self.lock:
            self.f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def close(self):
        self.f.close()


class Clock:
    """Run time on the control node's clock, from the load generator's start."""

    def __init__(self):
        self.t0 = time.time()

    def now(self):
        return time.time() - self.t0
