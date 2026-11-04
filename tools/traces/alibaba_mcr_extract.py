"""Per-minute HTTP call rate of candidate entry services from the Alibaba 2022 MSRTMCR
tables, for one window per day (15 min warm-up + the day's busiest hour).

MSRTMCR is stored as 3-minute archives (about 175 MB, about 15M rows of per-pod rates).
Each archive is streamed from the trace's public bucket through tar into this parser;
only rows of the candidate services are kept, summed over pods per minute.
Values stay in the trace's normalized units (max-min normalized); only the shape is used.

Usage: python alibaba_mcr_extract.py <day> <busiest_hour> <out.csv> [workers]
"""
import csv
import io
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

URL = "https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2022MicroservicesTraces/MCRRTUpdate/MCRRTUpdate_{}.tar.gz"
SERVICES = {"MS_42222", "MS_61753", "MS_40814", "MS_70053", "MS_50612"}


def one_archive(idx):
    cmd = f"curl -s --retry 5 --retry-delay 5 {URL.format(idx)} | tar xzO"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    r = csv.reader(io.TextIOWrapper(p.stdout))
    h = next(r)
    ti, si, hi = h.index("timestamp"), h.index("msname"), h.index("http_mcr")
    acc = defaultdict(float)
    for row in r:
        if row[si] in SERVICES:
            try:
                acc[(int(row[ti]) // 60000, row[si])] += float(row[hi] or 0)
            except ValueError:
                pass
    p.wait()
    return idx, p.returncode, dict(acc)


def main():
    day, hour, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    start_min = day * 1440 + hour * 60 - 15
    idxs = list(range(start_min // 3, (start_min + 75 + 2) // 3 + 1))
    total = defaultdict(float)
    with ProcessPoolExecutor(workers) as ex:
        for idx, rc, acc in ex.map(one_archive, idxs):
            print(f"day {day}: archive {idx} rc={rc} keys={len(acc)}", flush=True)
            for k, v in acc.items():
                total[k] += v
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "minute", "service", "http_mcr"])
        for (m, s), v in sorted(total.items()):
            if start_min <= m < start_min + 75:
                w.writerow([day, m - start_min, s, round(v, 9)])


if __name__ == "__main__":
    main()
