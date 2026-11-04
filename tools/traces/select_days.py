"""Choose development, test, and training days for each trace, reproducibly.

1. Screen out anomalous days.
   Wikipedia: peak/trough ratio above 2.2, or a daily total more than 25% off the median.
   Huawei: any chosen function absent that day, or its daily total below 0.5x or above 2x
   the median of its 7 neighbouring days (the functions trend over months), or the previous
   day missing from the trace (its last 6 h are the warm-up, as for Wikipedia).
   Functions (2026-10-06): 86 and 194, chosen among the functions present on every day for having
   no timer-triggered bursts (requests spread evenly over each minute, no 5-minute spikes) and a
   clear daily cycle; 60 and 183, used before, have timer bursts that no 60 s controller can act on
   (docs/decisions.md, docs/traces.md).
2. With a fixed seed, pick 3 development days and 5 test days among the clean days.
3. All other clean days form the RL training pool.
   Alibaba: a day with missing minutes. Window = busiest hour by cluster-wide CPU.
Writes data/traces/days.json.

Usage: python select_days.py <huawei requests_second.zip>
"""
import csv
import io
import json
import pathlib
import random
import statistics
import sys
import zipfile
from collections import defaultdict

import pandas as pd

SEED = 2026
ROOT = pathlib.Path(__file__).resolve().parents[2]
HUAWEI_FNS = {"thumbnailer": "86", "graph-bfs": "194"}


def split(clean):
    rnd = random.Random(SEED)
    pick = rnd.sample(sorted(clean), 8)
    dev, test = sorted(pick[:3]), sorted(pick[3:])
    train = sorted(set(clean) - set(pick))
    return dev, test, train


# Wikipedia
days = defaultdict(list)
for r in csv.DictReader(open(ROOT / "data/traces/wikipedia_en_hourly.csv")):
    days[r["timestamp_utc"][:10]].append(int(r["views"]))
full = {d: v for d, v in days.items() if len(v) == 24}
med = statistics.median(sum(v) for v in full.values())
wiki_excluded = {d: f"peak/trough {max(v)/min(v):.2f}, total {sum(v)/med:.2f}x median"
                 for d, v in full.items() if max(v) / min(v) > 2.2 or abs(sum(v) / med - 1) > 0.25}
wiki_clean = [d for d in full if d not in wiki_excluded]
wdev, wtest, wtrain = split(wiki_clean)

# Huawei
z = zipfile.ZipFile(sys.argv[1])
totals = {}
for name in sorted(n for n in z.namelist() if n.endswith(".csv")):
    with z.open(name) as f:
        df = pd.read_csv(io.TextIOWrapper(f), index_col=False, usecols=list(HUAWEI_FNS.values())).fillna(0)
    totals[name.split("/")[-1][:-4]] = {fn: float(df[fn].sum()) for fn in HUAWEI_FNS.values()}
# The functions grow over the months (the trace's SoCC 2023 paper studies these trends), so
# each day is compared with the median of its 7 neighbouring available days, not a global one.
order = sorted(totals)
hw_excluded = {}
for i, d in enumerate(order):
    near = order[max(0, i - 3): i + 4]
    bad = []
    for fn in HUAWEI_FNS.values():
        m = statistics.median(totals[x][fn] for x in near)
        r = totals[d][fn] / m if m else 0
        if totals[d][fn] == 0 or r < 0.5 or r > 2.0:
            bad.append(f"fn {fn}: {r:.2f}x its 7-day median")
    prev = f"day_{int(d[4:]) - 1:03d}"
    if prev not in totals:
        bad.append("previous day missing (warm-up)")
    if bad:
        hw_excluded[d] = "; ".join(bad)
hw_clean = [d for d in totals if d not in hw_excluded]
hdev, htest, htrain = split(hw_clean)

# Alibaba (13 days). Cluster-wide CPU per minute from NodeMetrics (alibaba_cluster_load.py).
# Days with missing minutes are excluded. Each day's window is its busiest hour by cluster CPU
# (hours 1-23, 15 min of warm-up must precede it). Cluster CPU is nearly flat (busiest hour only
# 5-7% above the quietest), so it only locates the hour; the service's own shape is used later.
ali = pd.read_csv(ROOT / "data/traces/alibaba_cluster_cpu_minute.csv")
ali["day"], ali["hour"] = ali.minute // 1440, (ali.minute % 1440) // 60
per_day = ali.groupby("day").size()
ali_excluded = {int(d): f"{int(n)} of 1440 minutes present" for d, n in per_day.items() if n < 1440}
hourly = ali.groupby(["day", "hour"]).mean_cpu.mean().unstack()
ali_hour = {int(d): int(row.iloc[1:].idxmax()) for d, row in hourly.iterrows()}
ali_clean = [int(d) for d in per_day.index if int(d) not in ali_excluded]
adev, atest, atrain = split(ali_clean)

out = {
    "seed": SEED,
    "rule": "3 dev + 5 test days sampled from clean days; the rest is the RL training pool",
    "wikipedia": {"source": "en.wikipedia, all-access, user agents, hourly", "dev": wdev, "test": wtest,
                  "train": wtrain, "excluded": wiki_excluded},
    "huawei": {"source": "Huawei Private 2023, requests per second", "functions": HUAWEI_FNS,
               "dev": hdev, "test": htest, "train": htrain, "excluded": hw_excluded},
    "alibaba": {"source": "Alibaba microservices 2022, per-minute HTTP call rate of one entry service",
                "dev": adev, "test": atest, "train": atrain, "excluded": ali_excluded,
                "busiest_hour": {str(d): ali_hour[d] for d in adev + atest + atrain}},
}
(ROOT / "data/traces/days.json").write_text(json.dumps(out, indent=1))
print(f"wikipedia: {len(full)} days, {len(wiki_excluded)} excluded; dev {wdev}; test {wtest}; train pool {len(wtrain)}")
print(f"huawei: {len(totals)} days, {len(hw_excluded)} excluded; dev {hdev}; test {htest}; train pool {len(htrain)}")
print(f"alibaba: {len(per_day)} days, {len(ali_excluded)} excluded; dev {adev}; test {atest}; train pool {atrain}; hours {[ali_hour[d] for d in adev + atest]}")
