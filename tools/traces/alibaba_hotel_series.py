"""Per-second shape for the hotel app from the per-minute call rate of MS_42222
(output of alibaba_mcr_extract.py, one file per day in data/traces/alibaba_mcr/).

Why MS_42222: of the five candidates it has one row per pod and minute, as documented.
MS_61753 repeats each pod's row 16,384 or 65,536 times per minute (a join error in the
published table), so its per-minute sum follows the row count, not the load.

Cleaning: in some minutes about a quarter of the pods report almost exactly 4x their rate
while the rest and the response time stay unchanged (e.g. day 3, minute 4541). All five
services jump in the same minute and fall back the next. We treat this as a collection
artifact: a minute more than 1.15x the median of the 7 minutes around it (itself excluded)
is replaced by that median. Normal minute-to-minute change is under 2%. Replaced minutes
are listed in the output log.

Per second: linear interpolation between minute midpoints (as for Wikipedia hours).
Writes data/traces/alibaba_hotel_second.csv (day, second, rps) with rps in the trace's
normalized units; build_traces.py rescales it.

Usage: python alibaba_hotel_series.py
"""
import glob
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICE = "MS_42222"
MINUTES = 75
SPIKE = 1.15


def clean(x):
    x = x.copy()
    fixed = []
    for i in range(len(x)):
        near = np.r_[x[max(0, i - 3): i], x[i + 1: i + 4]]
        m = float(np.median(near))
        if x[i] > SPIKE * m:
            fixed.append((i, round(x[i] / m, 2)))
            x[i] = m
    return x, fixed


rows = []
for f in sorted(glob.glob(str(ROOT / "data/traces/alibaba_mcr/day_*.csv"))):
    d = pd.read_csv(f)
    s = d[d.service == SERVICE].set_index("minute").http_mcr.reindex(range(MINUTES))
    day = int(d.day.iloc[0])
    assert not s.isna().any(), (day, s[s.isna()].index.tolist())
    x, fixed = clean(s.to_numpy(dtype=float))
    t = np.arange(MINUTES * 60) / 60.0
    per_s = np.interp(t, np.arange(MINUTES) + 0.5, x)
    rows.append(pd.DataFrame({"day": day, "second": np.arange(MINUTES * 60), "rps": per_s}))
    sc = x[15:]
    print(f"day {day}: replaced minutes {fixed}; scored hour cv {sc.std() / sc.mean():.3f}, "
          f"peak/mean {sc.max() / sc.mean():.3f}, last/first {sc[-1] / sc[0]:.3f}")
out = pd.concat(rows)
out.to_csv(ROOT / "data/traces/alibaba_hotel_second.csv", index=False)
print(f"{len(rows)} days -> data/traces/alibaba_hotel_second.csv")
