"""Cluster-wide load per minute of the Alibaba 2022 microservices trace, from its
NodeMetrics tables (normalized CPU per node). Used to find each day's busiest hour,
which is then the only hour of call-rate data we download for that day.

Streams each 12-hour archive (about 665 MB) straight from the trace's public bucket
through tar into this parser: nothing raw is kept on disk.
Writes data/traces/alibaba_part_<first>_<last>.csv (minute, nodes, mean_cpu);
parts are merged (node-weighted mean where minutes overlap) into alibaba_cluster_cpu_minute.csv.

Usage: python alibaba_cluster_load.py [first_idx] [last_idx]   (idx = day*2 + half; 0..25)
"""
import csv
import io
import pathlib
import subprocess
import sys
from collections import defaultdict

URL = "https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2022MicroservicesTraces/NodeMetricsUpdate/NodeMetricsUpdate_{}.tar.gz"
first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
last = int(sys.argv[2]) if len(sys.argv) > 2 else 25
# one output part per archive range, so ranges can run in parallel; merge_alibaba_parts joins them
OUT = pathlib.Path(__file__).resolve().parents[2] / f"data/traces/alibaba_part_{first}_{last}.csv"

sums, counts = defaultdict(float), defaultdict(int)
for idx in range(first, last + 1):
    cmd = f"curl -s --retry 5 --retry-delay 5 {URL.format(idx)} | tar xzO"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    r = csv.reader(io.TextIOWrapper(p.stdout))
    header = next(r)
    ti, ci = header.index("timestamp"), header.index("cpu_utilization")
    n = 0
    for row in r:
        try:
            minute = int(row[ti]) // 60000
            sums[minute] += float(row[ci]); counts[minute] += 1; n += 1
        except (ValueError, IndexError):
            continue
    p.wait()
    print(f"archive {idx}: {n} rows, rc={p.returncode}", flush=True)
    with open(OUT, "w", newline="") as f:          # rewrite after each archive (resumable view)
        w = csv.writer(f)
        w.writerow(["minute", "nodes", "mean_cpu"])
        for m in sorted(sums):
            w.writerow([m, counts[m], round(sums[m] / counts[m], 6)])
print(f"{OUT}: {len(sums)} minutes")
