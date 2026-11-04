"""Remove dead hotel-app instances from consul, its service registry.

DeathStarBench's hotel services register in consul when they start and never deregister, and
consul runs no health checks on them. Every copy removed by a scale-down (or a crash) stays
listed, and callers keep sending requests to its dead address (harness smoke test, 2026-09-25:
58 entries for 4 live reservation copies). A production registry removes such entries through
health checks; this does the same: every few seconds, deregister entries whose address is not a
live pod of that service, and duplicates of one address. New copies still
register themselves when they start.
A new copy registers about 1 s after it starts, often before the API server shows it Ready or even
shows its address (second testbed, 2026-09-30: the new reservation copies were removed within a
second and never registered again, so one copy took all the traffic). So a copy that is not
terminating counts as live whether Ready or not, and an address no pod holds is removed only after
GRACE_S; a terminating copy or an address that now belongs to another service is removed at once.
Runs for every controller, from before the reset to the end of the run.
"""
import json
import threading
import time
import urllib.request

from common import kubectl, kubectl_json

NS = "hotel"


def _consul():
    ip = kubectl("-n", NS, "get", "svc", "consul", "-o", "jsonpath={.spec.clusterIP}")
    return f"http://{ip}:8500"


GRACE_S = 30


def pod_ips():
    """Pod address -> (its deployment, terminating). Deployment = pod name minus the two generated suffixes."""
    ips = {}
    for p in kubectl_json("-n", NS, "get", "pods")["items"]:
        if p["status"].get("podIP"):
            ips[p["status"]["podIP"]] = (p["metadata"]["name"].rsplit("-", 2)[0],
                                         bool(p["metadata"].get("deletionTimestamp")))
    return ips


def clean(base=None, unknown_since=None):
    """Deregister dead entries (see module doc). unknown_since: entry id -> first time its address
    held no pod, kept between passes by the caller; None removes such entries at once (manual use).
    Returns the number removed."""
    base = base or _consul()
    with urllib.request.urlopen(base + "/v1/agent/services", timeout=5) as r:
        services = json.load(r)
    ips = pod_ips()
    now = time.time()
    removed, seen = 0, set()
    for sid, s in services.items():
        if not s["Service"].startswith("srv-"):
            continue
        key = (s["Service"], s.get("Address"), s.get("Port"))
        pod = ips.get(s.get("Address"))
        if pod is None:               # no pod at that address yet (API lag) or any more
            if unknown_since is None:
                dead = True
            else:
                dead = now - unknown_since.setdefault(sid, now) >= GRACE_S
        else:
            if unknown_since is not None:
                unknown_since.pop(sid, None)
            # terminating, or the address was reused by a pod of another service
            dead = pod[1] or pod[0] != s["Service"].removeprefix("srv-")
        duplicate = key in seen       # a restarted pod registered twice: it would get double traffic
        seen.add(key)
        if dead or duplicate:
            if unknown_since is not None:
                unknown_since.pop(sid, None)
            req = urllib.request.Request(f"{base}/v1/agent/service/deregister/{sid}", method="PUT")
            urllib.request.urlopen(req, timeout=5).close()
            removed += 1
    return removed


class Cleaner:
    """Background loop; log(rec) receives one record per pass that removed something."""

    def __init__(self, every_s=5, log=None):
        self.every, self.log = every_s, log
        self.stop_ev = threading.Event()
        self.unknown_since = {}
        self.base = _consul()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _loop(self):
        while not self.stop_ev.is_set():
            try:
                n = clean(self.base, self.unknown_since)
                if n and self.log:
                    self.log({"registry_removed": n})
            except Exception as e:
                if self.log:
                    self.log({"registry_error": repr(e)[:200]})
            self.stop_ev.wait(self.every)

    def stop(self):
        self.stop_ev.set()
        self.thread.join(timeout=10)


if __name__ == "__main__":
    print(f"removed {clean()} dead entries")
