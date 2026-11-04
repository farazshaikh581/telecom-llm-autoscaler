"""Build the per-second target rates each run replays (75 min: 15 warm-up + 60 scored).

Shapes (each normalized so that its busiest minute of the scored hour = 1):
- teastore <- Wikipedia hourly views: the test day's 24 h, preceded by the previous
  day's last 6 h as warm-up, all at 150 s per trace hour (24x). Linear interpolation.
- thumbnailer, graph-bfs <- Huawei per-second requests of functions 86, 194 (days.json): like
  Wikipedia, the test day's 24 h preceded by the previous day's last 6 h, at 24x (each second of the
  run is the mean of 24 trace seconds). Until 2026-10-06: functions 60, 183 (and compression <- 190
  until 2026-10-01), the day's busiest hour at 1x; their timer-triggered bursts (up to 2,000 requests
  in one second every 5 min) set the minute's P95 whatever a 60 s controller does (docs/decisions.md).
- hotel <- Alibaba per-minute call rate of service MS_42222 (alibaba_hotel_series.py): the
  day's busiest hour by cluster CPU, preceded by 15 min, at 1x.

Scaling (docs/decisions.md, config/trace_scaling.yaml): the unit is a copy (what controllers
decide; each reserves 0.5 CPU). Copies needed = rate / capacity per copy, summed over the
scaled services.
Each app gets the same number K of copies at its own busiest minute (TeaStore capped at
450 req/s). K is solved per run so that the combined busiest minute needs 125% of the
copies the workers can hold (config/harness.yaml); fixed services' copies are part of that supply. Actual CPU at that minute is reported from the demand model.
Finally every rate is multiplied by load_scale (config/trace_scaling.yaml, 0.45): calibrated
capacities proved too optimistic with the whole cluster busy (docs/harness.md).

Usage: python build_traces.py <huawei zip> [--alibaba data/traces/alibaba_hotel_second.csv] [--train]
Writes data/traces/runs/<split>_<n>.csv.gz (second, then one column per entry in req/s) and data/traces/runs/index.json. With --train: the RL training pool
instead (days.json "train"; Wikipedia and Huawei day n paired, the 2 Alibaba days alternate),
to data/traces/train/ with its own index.json; these days never appear in cluster runs.
"""
import gzip
import io
import json
import os
import pathlib
import sys
import zipfile

import numpy as np
import pandas as pd
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CFG = yaml.safe_load(open(ROOT / os.environ.get("SCALING_CONFIG", "config/trace_scaling.yaml")))
DAYS = json.load(open(ROOT / "data/traces/days.json"))
WARM, SCORED = 900, 3600
TOTAL = WARM + SCORED
HARNESS = yaml.safe_load(open(ROOT / os.environ.get("HARNESS_CONFIG", "config/harness.yaml")))
COPIES_AVAILABLE = HARNESS["copies_available"]   # copies of 0.5 CPU that fit on the workers
TARGET = 1.25 * COPIES_AVAILABLE   # combined busiest minute needs 125% of what fits (fixed copies included)

# Capacity per copy (req/s at SLO, all apps running) of every tier that serves the app's traffic
# (config/trace_scaling.yaml). Hotel: all four tiers count, although only reservation is scaled since
# 2026-10-01 (frontend, search and rate run at fixed copies); this keeps hotel's traffic at its
# phase-2 level, which the fixed tiers handle with room to spare.
CAP = {e: list(v["tier_capacity_rps"].values()) if "tier_capacity_rps" in v else [v["capacity_per_copy_rps"]]
       for e, v in CFG["entries"].items()}
# Capacity per copy of each app's scaled service (start sizes, KEDA targets, RL state scaling)
SCALED_CAP = {e: v["capacity_per_copy_rps"] for e, v in CFG["entries"].items()}
# CPU-seconds per request, only for the info line (cpu_at_combined_peak); estimates for the new functions
CPU_PER_RPS = {"teastore": 0.0085, "hotel": 0.0045, "thumbnailer": 0.010, "graph-bfs": 0.027}
TEASTORE_CAP_RPS = CFG["entries"]["teastore"]["peak_cap_rps"]
LOAD_SCALE = CFG.get("load_scale", 1.0)


def minute_peak(x):
    """Busiest 60-s moving average within the scored hour."""
    s = pd.Series(x[WARM:]).rolling(60, min_periods=60).mean()
    return float(s.max())


def wiki_shape(day):
    w = pd.read_csv(ROOT / "data/traces/wikipedia_en_hourly.csv", parse_dates=["timestamp_utc"])
    w = w.set_index("timestamp_utc")["views"]
    start = pd.Timestamp(day, tz="UTC") - pd.Timedelta(hours=6)
    hours = w[start: start + pd.Timedelta(hours=29)].to_numpy(dtype=float)       # 30 hourly points
    assert len(hours) == 30, (day, len(hours))
    t = np.arange(TOTAL) / 150.0                                                  # trace hours
    x = np.interp(t, np.arange(30) + 0.5, hours)                                  # hour midpoints
    return x / minute_peak(x)


def huawei_shapes(zf, day, fns):
    """The day's 24 h after the previous day's last 6 h, compressed 24x as wiki_shape (150 s per
    trace hour): each run second is the mean of 24 trace seconds."""
    prev = f"day_{int(day[4:]) - 1:03d}"
    names = set(zf.namelist())
    assert f"requests_second/{prev}.csv" in names, (day, "previous day missing")

    def read(d):
        with zf.open(f"requests_second/{d}.csv") as f:
            return pd.read_csv(io.TextIOWrapper(f), index_col=False, usecols=list(fns.values())).fillna(0)
    a, b = read(prev), read(day)
    shapes = {}
    for app, c in fns.items():
        x = np.r_[a[c].to_numpy(dtype=float)[-6 * 3600:], b[c].to_numpy(dtype=float)]   # 30 h
        x = x.reshape(-1, 24).mean(axis=1)                                               # 4,500 s
        assert len(x) == TOTAL, (day, len(x))
        shapes[app] = x / minute_peak(x)
    return shapes, None


def copies(rates):
    """Copies needed per second: rate / capacity, summed over every scaled service."""
    return sum((rates[a][:, None] / np.array(CAP[a])[None, :]).sum(axis=1) for a in rates)


def scale(shapes):
    """Solve K (copies each app needs at its own busiest minute) for the combined target."""
    def rates_for(k):
        r = {}
        for a, s in shapes.items():
            per_rps = sum(1.0 / c for c in CAP[a])               # copies per req/s for this app
            peak_rps = k / per_rps
            if a == "teastore":
                peak_rps = min(peak_rps, TEASTORE_CAP_RPS)
            r[a] = s * peak_rps
        return r

    def combined_peak(k):
        d = copies(rates_for(k))
        return float(pd.Series(d[WARM:]).rolling(60, min_periods=60).mean().max())

    lo, hi = 0.1, 50.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if combined_peak(mid) < TARGET else (lo, mid)
    k = (lo + hi) / 2
    r = rates_for(k)
    cpu = sum(CPU_PER_RPS[a] * r[a] for a in r)
    d = copies(r)
    i = int(pd.Series(d[WARM:]).rolling(60, min_periods=60).mean().idxmax()) + WARM
    return k, r, {"k_copies_per_app_peak": round(k, 2), "combined_peak_copies": round(combined_peak(k), 2),
                  "cpu_at_combined_peak": round(float(pd.Series(cpu).rolling(60).mean().iloc[i]), 2),
                  "peak_rps": {a: round(minute_peak(r[a]), 2) for a in r}}


def main():
    zf = zipfile.ZipFile(sys.argv[1])
    alibaba = None
    if "--alibaba" in sys.argv:
        alibaba = pd.read_csv(ROOT / sys.argv[sys.argv.index("--alibaba") + 1])
    train = "--train" in sys.argv
    out_dir = ROOT / ("data/traces/train" if train else "data/traces/runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {"target_combined_peak_copies": TARGET, "copies_available": COPIES_AVAILABLE, "runs": []}
    for split in (("train",) if train else ("dev", "test")):
        n_days = min(len(DAYS["wikipedia"][split]), len(DAYS["huawei"][split]))
        for n in range(n_days):
            wday, hday = DAYS["wikipedia"][split][n], DAYS["huawei"][split][n]
            try:
                shapes = {"teastore": wiki_shape(wday)}
            except AssertionError:
                if not train:
                    raise
                print(json.dumps({"skipped": wday, "reason": "no previous Wikipedia day for the warm-up"}))
                continue
            hs, _ = huawei_shapes(zf, hday, DAYS["huawei"]["functions"])
            shapes.update(hs)
            meta = {"run": f"{split}_{n}", "wikipedia_day": wday, "huawei_day": hday, "huawei_window": "previous day's last 6 h + the day, 24x"}
            if alibaba is not None:
                aday = DAYS["alibaba"][split][n % len(DAYS["alibaba"][split])]
                x = alibaba[alibaba.day == aday].sort_values("second")["rps"].to_numpy(dtype=float)
                shapes["hotel"] = x / minute_peak(x)
                meta["alibaba_day"] = int(aday)
            k, rates, info = scale(shapes)
            rates = {a: v * LOAD_SCALE for a, v in rates.items()}
            meta.update(info, load_scale=LOAD_SCALE,
                        peak_rps_scaled={a: round(minute_peak(v), 2) for a, v in rates.items()})
            df = pd.DataFrame({"second": np.arange(TOTAL), **{a: np.round(v, 3) for a, v in rates.items()}})
            with gzip.open(out_dir / f"{split}_{n}.csv.gz", "wt") as f:
                df.to_csv(f, index=False)
            index["runs"].append(meta)
            print(json.dumps(meta))
    (out_dir / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
