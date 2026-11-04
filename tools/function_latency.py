"""Latency of candidate SeBS functions at one copy, no load (2026-10-01): which functions fit the
edge range (SLO = 3 x shared no-queue P95, wanted within about 20-200 ms; decisions.md).
Sequential requests from the control node (where the load generator runs) through each function's
Service, after a warm-up; P50/P95/P99 of the client-side latency and of the handler time.
Candidates run as test Deployments cand-<name> (sebs namespace), configured like thumbnailer.

  python tools/function_latency.py [case substring ...]  -> results/functions/latency_<date>.csv + table,
  and the per-request latencies of each case in results/functions/raw/<case>.csv (for sim/params.py --provisional)
"""
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUCKET = "sebs-benchmarks"
THUMBS = [f"{i}_{n}" for i, n in enumerate([
    "action-adrenaline-adventure-1047051.jpg", "adult-adventure-back-view-2819546.jpg",
    "aerial-shot-architecture-bird-s-eye-view-2440013.jpg", "aerial-shot-architecture-bridge-2887493.jpg",
    "altitude-astrology-astronomy-1819650.jpg", "asphalt-atmosphere-cloudy-sky-2739010.jpg",
    "astronomy-desktop-wallpaper-evening-1624438.jpg", "beach-bird-s-eye-view-coast-2499700.jpg",
    "close-up-elder-elderly-2050990.jpg", "cobblestone-granite-pebbles-1029604.jpg"])]
SMALL = ["512px-Cacatua_moluccensis_-Cincinnati_Zoo-8a.jpg", "782px-Pumiforme.JPG",
         "800px-20180630_Tesla_Model_S_70D_2015_midnight_blue_left_front.jpg", "800px-7weeks_old.JPG",
         "800px-Jammlich_crop.jpg", "800px-Porsche_991_silver_IAA.jpg", "800px-Sardinian_Warbler.jpg",
         "800px-Welsh_Springer_Spaniel.jpg"]


def thumb(keys, folder):
    return lambda i: {"object": {"key": keys[i % len(keys)], "width": 200, "height": 200},
                      "bucket": {"bucket": BUCKET, "input": folder, "output": "210.thumbnailer-0-output"}}


CASES = [  # (label, service, event(i), measured requests)
    ("thumbnailer, current photos (0.8-4 MB)", "thumbnailer", thumb(THUMBS, "210.thumbnailer-0-input"), 200),
    ("thumbnailer, 800 px photos (60-240 KB)", "thumbnailer", thumb(SMALL, "411.image-recognition-1-input"), 300),
    ("dynamic-html, test (10)", "cand-dynamic-html", lambda i: {"username": "testname", "random_len": 10}, 300),
    ("dynamic-html, small (1,000)", "cand-dynamic-html", lambda i: {"username": "testname", "random_len": 1000}, 300),
    ("dynamic-html, large (100,000)", "cand-dynamic-html", lambda i: {"username": "testname", "random_len": 100000}, 50),
    ("graph-bfs, test (10)", "cand-graph-bfs", lambda i: {"size": 10, "seed": 42}, 300),
    ("graph-bfs, small (10,000)", "cand-graph-bfs", lambda i: {"size": 10000, "seed": 42}, 200),
    ("graph-bfs, large (100,000)", "cand-graph-bfs", lambda i: {"size": 100000, "seed": 42}, 30),
    ("graph-pagerank, test (10)", "cand-graph-pagerank", lambda i: {"size": 10, "seed": 42}, 300),
    ("graph-pagerank, small (10,000)", "cand-graph-pagerank", lambda i: {"size": 10000, "seed": 42}, 200),
    ("graph-pagerank, large (100,000)", "cand-graph-pagerank", lambda i: {"size": 100000, "seed": 42}, 30),
]


def ip(svc):
    return subprocess.run(["kubectl", "-n", "sebs", "get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}"],
                          capture_output=True, text=True, check=True).stdout


def call(url, ev):
    req = urllib.request.Request(url, json.dumps(ev).encode(), {"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.load(r)
    return (time.perf_counter() - t) * 1000, body["handler_ms"]


def main():
    rows = []
    want = sys.argv[1:]
    raw = ROOT / "results/functions/raw"
    raw.mkdir(parents=True, exist_ok=True)
    for label, svc, ev, n in CASES:
        if want and not any(w in label for w in want):
            continue
        url = f"http://{ip(svc)}:8080/"
        for i in range(min(30, n)):
            call(url, ev(i))                                  # warm-up
        lat, han = zip(*(call(url, ev(i)) for i in range(n)))
        lat, han = np.array(lat), np.array(han)
        pd.DataFrame({"latency_ms": lat.round(3), "handler_ms": han.round(3)}).to_csv(
            raw / (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") + ".csv"), index=False)
        rows.append({"case": label, "requests": n, "p50_ms": np.percentile(lat, 50), "p95_ms": np.percentile(lat, 95),
                     "p99_ms": np.percentile(lat, 99), "handler_p50_ms": np.percentile(han, 50),
                     "handler_p95_ms": np.percentile(han, 95)})
        print(json.dumps({k: (round(v, 1) if isinstance(v, float) else v) for k, v in rows[-1].items()}), flush=True)
    t = pd.DataFrame(rows).round(1)
    out = ROOT / "results/functions"
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / f"latency_{time.strftime('%Y%m%d')}.csv", index=False)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
