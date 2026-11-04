"""Start the Go trace replayer (harness/loadgen) and read its live statistics."""
import json
import pathlib
import signal
import subprocess
import urllib.request

import cluster
from common import CFG, ENTRIES

BIN = pathlib.Path(__file__).resolve().parent / "loadgen" / "loadgen"


class LoadGen:
    def __init__(self, trace, out_dir, seed=None, duration=0, scale=1.0, arrivals="poisson"):
        self.addr = CFG["loadgen"]["listen"]
        targets = ",".join(f"{e}=http://{cluster.entry_ip(c)}:{c['port']}/" for e, c in ENTRIES.items())
        cmd = [str(BIN), "-trace", str(trace), "-out", str(out_dir / "requests.csv.gz"), "-listen", self.addr,
               "-targets", targets, "-seed", str(seed if seed is not None else CFG["loadgen"]["seed"]),
               "-max-inflight", str(CFG["loadgen"]["max_inflight"]), "-duration", str(duration),
               "-ramp", str(CFG["loadgen"]["ramp_s"]), "-scale", str(scale), "-arrivals", arrivals]
        self.log = open(out_dir / "loadgen.log", "w")
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)

    def stats(self, window):
        with urllib.request.urlopen(f"http://{self.addr}/stats?window={window}", timeout=5) as r:
            return json.load(r)

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        if self.running():
            self.proc.send_signal(signal.SIGTERM)

    def wait(self, timeout=None):
        rc = self.proc.wait(timeout)
        self.log.close()
        return rc
