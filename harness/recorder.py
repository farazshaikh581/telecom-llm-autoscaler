"""Record everything during a run, independent of the controller:
- fast.jsonl (every 5 s): load generator stats over the last 5 s, replicas, pod states
- metrics.jsonl (every 15 s): metrics-server CPU and memory per service
- gpu.csv (every 1 s): per GPU (index, PCI bus): power, utilization, memory (nvidia-smi on the control node)
- host_power.csv (every 5 s): RAPL package power and per-VM CPU on the physical host
  (harness/host_power_probe.sh over ssh; VM names replaced by role names before saving)
Prometheus keeps its own long-term record in parallel.

The host's ssh address and the VM-to-role map are read from ~/.config/telecom-llm-autoscaler/
(host, vm_roles.yaml), which stay out of the repo. Without them, host power is skipped.
"""
import csv
import io
import pathlib
import subprocess
import threading
import time

import yaml

import cluster
from common import CFG, JsonLog

HERE = pathlib.Path(__file__).resolve().parent
LOCAL = pathlib.Path.home() / ".config/telecom-llm-autoscaler"


def host_address():
    p = LOCAL / "host"
    return p.read_text().strip() if p.exists() else None


class Recorder:
    def __init__(self, out_dir, clock, loadgen, minutes, tag):
        self.out, self.clock, self.lg = out_dir, clock, loadgen
        self.stop_ev = threading.Event()
        self.fast = JsonLog(out_dir / "fast.jsonl")
        self.metrics = JsonLog(out_dir / "metrics.jsonl")
        self.threads = [threading.Thread(target=self._loop, args=(CFG["recorder"]["fast_s"], self._fast), daemon=True),
                        threading.Thread(target=self._loop, args=(CFG["recorder"]["metrics_s"], self._metrics), daemon=True)]
        self.gpu = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=timestamp,index,pci.bus_id,power.draw,utilization.gpu,memory.used",
             "--format=csv,nounits", "-lms", str(CFG["recorder"]["gpu_ms"])],
            stdout=open(out_dir / "gpu.csv", "w"), stderr=subprocess.DEVNULL)
        self.host = host_address()
        self.remote_csv = f"/tmp/hostpower_{tag}.csv"
        self.host_proc = None
        if self.host:
            probe = open(HERE / "host_power_probe.sh", "rb").read()
            self.host_proc = subprocess.Popen(
                ["ssh", "-o", "BatchMode=yes", self.host, "bash", "-s", "--", self.remote_csv, str(minutes), tag,
                 str(CFG["recorder"]["host_power_s"])], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=open(out_dir / "host_power.log", "w"))
            self.host_proc.stdin.write(probe)
            self.host_proc.stdin.close()

    def start(self):
        for t in self.threads:
            t.start()

    def _loop(self, every, fn):
        nxt = time.time()
        while not self.stop_ev.is_set():
            try:
                fn()
            except Exception as e:  # a failed sample is logged, the run goes on
                self.fast.write({"t": round(self.clock.now(), 2), "recorder_error": repr(e)[:300]})
            nxt += every
            self.stop_ev.wait(max(0, nxt - time.time()))

    def _fast(self):
        t = self.clock.now()
        rec = {"t": round(t, 2), "epoch": round(time.time(), 2)}
        try:
            rec["load"] = self.lg.stats(CFG["recorder"]["fast_s"])["entries"]
        except Exception as e:
            rec["load_error"] = repr(e)[:200]
        rec["replicas"] = cluster.replicas()
        rec["pods"] = cluster.pods()
        self.fast.write(rec)

    def _metrics(self):
        self.metrics.write({"t": round(self.clock.now(), 2), "epoch": round(time.time(), 2),
                            "services": cluster.pod_metrics()})

    def stop(self):
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=30)
        self.gpu.terminate()
        self.fast.close()
        self.metrics.close()
        if self.host_proc:
            self.host_proc.terminate()
            self._fetch_host_power()

    def _clean_probe_log(self):
        """The probe prints the host's name and every VM name at start; keep only the rest."""
        p = self.out / "host_power.log"
        if p.exists():
            keep = [l for l in p.read_text().splitlines()
                    if not any(k in l for k in ("host=", "domains:", "/sys/class/powercap"))]
            p.write_text("\n".join(keep) + ("\n" if keep else ""))

    def _fetch_host_power(self):
        self._clean_probe_log()
        roles_file = LOCAL / "vm_roles.yaml"
        roles = yaml.safe_load(open(roles_file)) if roles_file.exists() else {}
        raw = subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, "cat", self.remote_csv, "&&", "rm", "-f",
                              self.remote_csv], capture_output=True, text=True, timeout=60).stdout
        rows = list(csv.reader(io.StringIO(raw)))
        if not rows:
            return
        keep, header = [], []
        for i, h in enumerate(rows[0]):
            if h.startswith("vm_") and h.endswith("_cpu_pct"):
                role = roles.get(h[3:-8])
                if not role:
                    continue          # another project's VM: not recorded
                h = f"vm_{role}_cpu_pct"
            if h == "tag":
                continue
            keep.append(i)
            header.append(h)
        with open(self.out / "host_power.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows[1:]:
                w.writerow([r[i] for i in keep if i < len(r)])
