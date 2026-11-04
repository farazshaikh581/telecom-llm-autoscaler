"""Configuration fingerprint of the testbed (read-only), to see what changes when the VMs are
cloned or moved: Kubernetes and node versions and resources, images (digests), where the fixed pods
run, and on the physical host the CPU, the VMs' vCPUs, memory and pinning, swap settings.
Names are replaced by roles (control, w0..w3 by sorted worker name; VMs by their node role), so
no host names or addresses are written.

  python tools/cluster_fingerprint.py save <label>     -> results/fingerprint/<label>.json
  python tools/cluster_fingerprint.py diff <a> <b>      -> what differs
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from recorder import LOCAL, host_address  # noqa: E402

OUT = ROOT / "results/fingerprint"
APP_NS = ["teastore", "hotel", "sebs"]


def sh(cmd, host=None):
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes", host, cmd]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=isinstance(cmd, str))
    return r.stdout.strip()


def kjson(*args):
    return json.loads(sh(["kubectl", *args, "-o", "json"]))


def collect():
    nodes = kjson("get", "nodes")["items"]
    workers = sorted(n["metadata"]["name"] for n in nodes if n["metadata"]["name"].startswith("worker"))
    role = {w: f"w{i}" for i, w in enumerate(workers)}
    for n in nodes:
        if "node.kubernetes.io/microk8s-controlplane" in n["metadata"].get("labels", {}) and n["metadata"]["name"] not in role:
            ready = any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"])
            role.setdefault(n["metadata"]["name"], "control" if ready else "spare-control")
    fp = {"kubernetes": {}, "nodes": {}, "images": {}, "fixed_pods": {}, "host": {}}
    v = json.loads(sh(["kubectl", "version", "-o", "json"]))
    fp["kubernetes"]["server"] = v.get("serverVersion", {}).get("gitVersion")
    for n in nodes:
        r = role.get(n["metadata"]["name"])
        if not r:
            continue
        info, st = n["status"]["nodeInfo"], n["status"]
        fp["nodes"][r] = {"kubelet": info["kubeletVersion"], "kernel": info["kernelVersion"], "os": info["osImage"],
                          "runtime": info["containerRuntimeVersion"], "capacity": st["capacity"],
                          "allocatable": st["allocatable"], "taints": n["spec"].get("taints", []),
                          "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in st["conditions"])}
    for ns in APP_NS:
        for p in kjson("-n", ns, "get", "pods")["items"]:
            dep = p["metadata"]["name"].rsplit("-", 2)[0]
            for c in p["status"].get("containerStatuses", []):
                fp["images"][f"{ns}/{dep}/{c['name']}"] = c.get("imageID", "").split("@")[-1]
            fp["fixed_pods"].setdefault(f"{ns}/{dep}", set()).add(role.get(p["spec"].get("nodeName"), "other"))
    fp["fixed_pods"] = {k: sorted(v) for k, v in fp["fixed_pods"].items()}
    host = host_address()
    if host:
        h = fp["host"]
        h["cpu_model"] = sh("lscpu | grep 'Model name' | sed 's/.*: *//'", host)
        h["cpus"] = sh("nproc", host)
        h["kernel"] = sh("uname -r", host)
        h["memory_total_mib"] = sh("free -m | awk '/Mem:/{print $2}'", host)
        h["swap_total_mib"] = sh("free -m | awk '/Swap:/{print $2}'", host)
        h["swappiness"] = sh("cat /proc/sys/vm/swappiness", host)
        # VM name -> role, kept outside the repo (~/.config/telecom-llm-autoscaler/vms.json)
        vm_file = LOCAL / "vms.json"
        vm_role = json.loads(vm_file.read_text()) if vm_file.exists() else {}
        h["vms"] = {}
        for vm, r in vm_role.items():
            info = sh(f"virsh dominfo {vm} 2>/dev/null | grep -E 'State|CPU\\(s\\)|Max memory'", host)
            pin = sh(f"virsh vcpupin {vm} 2>/dev/null | tail -n +3 | awk 'NF{{print $2}}' | paste -sd' '", host)
            emu = sh(f"virsh emulatorpin {vm} 2>/dev/null | tail -n +3 | awk 'NF{{print $2}}'", host)
            h["vms"][r] = {"dominfo": " | ".join(" ".join(l.split()) for l in info.splitlines()), "vcpupin": pin,
                           "emulatorpin": emu}
    return fp


def flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        out.update(flat(v, key + ".") if isinstance(v, dict) else {key: v})
    return out


def main():
    cmd = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    if cmd == "save":
        (OUT / f"{sys.argv[2]}.json").write_text(json.dumps(collect(), indent=1, sort_keys=True))
        print("saved", OUT / f"{sys.argv[2]}.json")
    elif cmd == "diff":
        a, b = (flat(json.load(open(OUT / f"{x}.json"))) for x in sys.argv[2:4])
        diffs = [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        for k, x, y in diffs:
            print(f"{k}: {x!r} -> {y!r}")
        print(f"{len(diffs)} differences")


if __name__ == "__main__":
    main()
