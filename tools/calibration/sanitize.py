"""Remove infrastructure addresses from calibration outputs before they go into git.

k6 CSV (.csv or .csv.gz): drops the url and name columns (they hold the service's
cluster IP) and masks any other IPv4 address. Text files: masks IPv4 addresses.
Files are rewritten in place (a .csv becomes .csv.gz).

Usage: python3 sanitize.py <file> [...]
"""
import csv
import gzip
import os
import re
import sys

IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
DROP = {"url", "name"}


def sanitize_csv(path):
    opener = gzip.open if path.endswith(".gz") else open
    out = path if path.endswith(".gz") else path + ".gz"
    tmp = out + ".tmp"
    with opener(path, "rt", newline="") as fin, gzip.open(tmp, "wt", newline="") as fout:
        r = csv.DictReader(fin)
        cols = [c for c in r.fieldnames if c not in DROP]
        w = csv.DictWriter(fout, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in r:
            w.writerow({c: IP.sub("<ip>", row[c] or "") for c in cols})
    os.replace(tmp, out)
    if out != path:
        os.remove(path)


def sanitize_text(path):
    with open(path) as f:
        s = f.read()
    with open(path, "w") as f:
        f.write(IP.sub("<ip>", s))


for p in sys.argv[1:]:
    if p.endswith(".csv") or p.endswith(".csv.gz"):
        sanitize_csv(p)
    else:
        sanitize_text(p)
    print("sanitized", p)
