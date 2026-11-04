"""Profile Huawei Private 2023 per-second request traces to choose 3 functions.

For each function over the first N available days: active days, share of seconds with
requests, mean req/s, burstiness (P99 of 1-s rate over the mean of its minute, i.e.
how spiky a second is relative to its surroundings), and minute-level coefficient of
variation. Writes data/traces/huawei_profile.csv.

Usage: python profile_huawei.py <requests_second.zip> [N days]
"""
import io
import pathlib
import sys
import zipfile

import numpy as np
import pandas as pd

ZIP = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
OUT = pathlib.Path(__file__).resolve().parents[2] / "data/traces/huawei_profile.csv"

z = zipfile.ZipFile(ZIP)
days = sorted(n for n in z.namelist() if n.endswith(".csv"))[:N]
stats = {}
for name in days:
    with z.open(name) as f:
        df = pd.read_csv(io.TextIOWrapper(f), index_col=False)
    x = df.drop(columns=["day", "time"]).fillna(0).to_numpy(dtype=np.float32)  # 86400 x 200
    minute = x.reshape(1440, 60, -1)
    mmean = minute.mean(axis=1)                                   # 1440 x 200
    ratio = np.where(mmean[:, None, :] > 0, minute / np.maximum(mmean[:, None, :], 1e-9), 0)
    for j, col in enumerate(df.columns[2:]):
        s = stats.setdefault(col, {"days": 0, "active": [], "mean": [], "burst": [], "cv": [], "peakmin": []})
        tot = x[:, j].sum()
        if tot == 0:
            continue
        s["days"] += 1
        s["active"].append((x[:, j] > 0).mean())
        s["mean"].append(x[:, j].mean())
        busy = mmean[:, j] > 0
        s["burst"].append(np.percentile(ratio[:, :, j][busy], 99) if busy.any() else 0)
        s["cv"].append(mmean[:, j].std() / max(mmean[:, j].mean(), 1e-9))
        s["peakmin"].append(mmean[:, j].max())
    print("profiled", name, flush=True)

rows = []
for col, s in stats.items():
    if s["days"] == 0:
        continue
    rows.append({"function": int(col), "active_days": s["days"], "active_share": float(np.mean(s["active"])),
                 "mean_rps": float(np.mean(s["mean"])), "peak_minute_rps": float(np.max(s["peakmin"])),
                 "burst_p99": float(np.mean(s["burst"])), "minute_cv": float(np.mean(s["cv"]))})
out = pd.DataFrame(rows).sort_values("mean_rps", ascending=False)
OUT.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(OUT, index=False)
print(f"{OUT}: {len(out)} functions over {len(days)} days")
