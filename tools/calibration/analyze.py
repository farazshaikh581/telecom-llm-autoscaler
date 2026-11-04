"""Per-step latency statistics from a k6 CSV (calib.js runs).

Drops the first WARMUP_S seconds of each step. Reports offered and served rate
(successful requests), errors, and latency of successful requests: average, P50, P75, P90, P95, P99, max (ms). Writes a JSON
summary next to the CSV.

Usage: python3 analyze.py <k6.csv[.gz]> [WARMUP_S] [STEP_S]
"""
import csv
import gzip
import json
import statistics
import sys
from collections import defaultdict


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    path = sys.argv[1]
    warmup = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    step_s = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0   # served rate uses the known step length
    opener = gzip.open if path.endswith(".gz") else open
    lat = defaultdict(list)       # step -> [(ts, ms)]
    failed = defaultdict(list)    # step -> [(ts, 0/1)]
    rate_of = {}
    dropped = defaultdict(float)  # step -> requests k6 could not start in time
    with opener(path, "rt") as f:
        for row in csv.DictReader(f):
            # k6 writes custom tags as "step=3&rate=5" in the extra_tags column
            tags = dict(kv.split("=", 1) for kv in (row.get("extra_tags") or "").split("&") if "=" in kv)
            step = tags.get("step")
            if not step and (row.get("scenario") or "").startswith("step"):
                step = row["scenario"][4:]
            if not step or not step.isdigit():
                continue
            if row["metric_name"] == "dropped_iterations":
                # In Poisson mode only 1 in "os" iterations sends a request.
                dropped[step] += float(row["metric_value"]) / float(tags.get("os", 1))
                continue
            if "rate" in tags:
                rate_of[step] = float(tags["rate"])
            ts = float(row["timestamp"])
            if row["metric_name"] == "http_req_duration":
                # Latency percentiles use successful requests only. A refused or failed
                # request has a near-zero duration and would pull the percentiles down.
                ok = row.get("status") == "200"
                failed[step].append((ts, 0.0 if ok else 1.0))
                if ok:
                    lat[step].append((ts, float(row["metric_value"])))

    out = []
    hdr = f"{'step':>4} {'offered':>8} {'served':>7} {'err%':>6} {'avg':>8} {'p50':>8} {'p75':>8} {'p90':>8} {'p95':>8} {'p99':>8} {'max':>8} {'dropped':>8}"
    print(hdr)
    for step in sorted(lat, key=int):
        t0 = min(t for t, _ in lat[step])
        t1 = max(t for t, _ in lat[step])
        vals = sorted(v for t, v in lat[step] if t >= t0 + warmup)
        errs = [v for t, v in failed[step] if t >= t0 + warmup]
        span = step_s - warmup   # k6 timestamps are whole seconds; the step length is exact
        s = {
            "step": int(step), "offered_rps": rate_of[step], "served_rps": len(vals) / span,
            "requests": len(vals), "error_pct": 100.0 * sum(errs) / max(len(errs), 1), "dropped": dropped[step],
            "avg": statistics.fmean(vals) if vals else float("nan"),
            **{f"p{p}": pct(vals, p) for p in (50, 75, 90, 95, 99)},
            "max": vals[-1] if vals else float("nan"),
        }
        out.append(s)
        print(f"{s['step']:>4} {s['offered_rps']:>8.2f} {s['served_rps']:>7.2f} {s['error_pct']:>6.1f} "
              f"{s['avg']:>8.1f} {s['p50']:>8.1f} {s['p75']:>8.1f} {s['p90']:>8.1f} {s['p95']:>8.1f} {s['p99']:>8.1f} {s['max']:>8.1f} {s['dropped']:>8.0f}")
    summary = path.replace(".csv.gz", "").replace(".csv", "") + ".summary.json"
    with open(summary, "w") as f:
        json.dump(out, f, indent=1)
    print(f"summary: {summary}")


if __name__ == "__main__":
    main()
