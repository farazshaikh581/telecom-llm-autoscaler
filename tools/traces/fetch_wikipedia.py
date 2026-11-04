"""Hourly human page views of English Wikipedia (Wikimedia Pageviews API, CC0).

Drives TeaStore. Writes data/traces/wikipedia_en_hourly.csv with columns
timestamp_utc (ISO hour), views.

Usage: python3 fetch_wikipedia.py [start YYYYMMDD] [end YYYYMMDD, exclusive]
"""
import csv
import json
import pathlib
import sys
import urllib.request

START = sys.argv[1] if len(sys.argv) > 1 else "20250701"
END = sys.argv[2] if len(sys.argv) > 2 else "20251001"
URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/"
       f"en.wikipedia/all-access/user/hourly/{START}00/{END}00")
OUT = pathlib.Path(__file__).resolve().parents[2] / "data/traces/wikipedia_en_hourly.csv"

req = urllib.request.Request(URL, headers={"User-Agent": "llm-autoscaler-study trace fetch"})
items = json.load(urllib.request.urlopen(req, timeout=60))["items"]
OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp_utc", "views"])
    for it in items:
        t = it["timestamp"]  # YYYYMMDDHH
        w.writerow([f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:00Z", it["views"]])
print(f"{OUT}: {len(items)} hours, {START} to {END}")
