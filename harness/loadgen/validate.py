"""Check the Go load generator against k6: replay the spot-check steps (one entry alone, one copy,
60 s steps with 20 s gaps) and compare latency percentiles with the k6 runs of 2026-09-25.

Usage: python validate.py <entry> <rates comma list> <out dir> [extra loadgen flags]
Needs the entry at one copy. Writes <out dir>/<entry>_steps.csv (trace), <entry>_requests.csv.gz,
and prints one row per step, analyzed like tools/calibration/analyze.py (first 10 s of a step
dropped, successful requests only).
"""
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ENTRIES = ["teastore", "thumbnailer", "compression", "imgrec", "hotel"]
SVC = {"teastore": ("teastore", "teastore-webui", 8080), "thumbnailer": ("sebs", "thumbnailer", 8080),
       "compression": ("sebs", "compression", 8080), "imgrec": ("sebs", "imgrec", 8080),
       "hotel": ("hotel", "frontend", 5000)}
WARM, STEP, GAP = 20, 60, 20

entry, rates, out = sys.argv[1], [float(r) for r in sys.argv[2].split(",")], pathlib.Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
# warm-up like calib.js: one request every 2 s for 20 s, then the steps
series = [0.5] * WARM
for r in rates:
    series += [0.0] * GAP + [r] * STEP
series += [0.0] * 5
df = pd.DataFrame({"second": range(len(series)), **{e: (series if e == entry else [0.0] * len(series)) for e in ENTRIES}})
trace = out / f"{entry}_steps.csv"
df.to_csv(trace, index=False)

ns, svc, port = SVC[entry]
ip = subprocess.check_output(["kubectl", "-n", ns, "get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}"], text=True)
log = out / f"{entry}_requests.csv.gz"
subprocess.run([str(HERE / "loadgen"), "-trace", str(trace), "-out", str(log), "-seed", "7",
                "-listen", "127.0.0.1:9911", "-targets", f"{entry}=http://{ip}:{port}/", *sys.argv[4:]], check=True)

d = pd.read_csv(log)
d["done_s"] = (d.sched_ms / 1000 + d.send_delay_us / 1e6 + d.lat_us / 1e6)
print(f"{'step':>4} {'rate':>7} {'sent':>7} {'err%':>5} {'avg':>7} {'p50':>7} {'p75':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>7} {'delay_p99_ms':>12}")
for i, r in enumerate(rates):
    t0 = WARM + GAP + i * (STEP + GAP)
    sent = d[(d.sched_ms >= t0 * 1000) & (d.sched_ms < (t0 + STEP) * 1000)]
    w = sent[sent.done_s >= t0 + 10]
    ok = w[w.status == 200].lat_us / 1000
    q = np.percentile(ok, [50, 75, 90, 95, 99]) if len(ok) else [np.nan] * 5
    print(f"{i:>4} {r:>7.2f} {len(sent) / STEP:>7.2f} {100 * (w.status != 200).mean():>5.1f} {ok.mean():>7.1f} "
          + " ".join(f"{v:>7.1f}" for v in q) + f" {ok.max():>7.1f} {np.percentile(sent.send_delay_us, 99) / 1000:>12.2f}")
