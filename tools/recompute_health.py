"""Add the testbed-health block (control-node stalls, harness/analyze_run.py) to finished runs'
summary.json without recomputing anything else (2026-10-06).

  python tools/recompute_health.py [results/runs/<name> ...]   (default: every run with fast.jsonl)
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from common import CFG  # noqa: E402

runs = [pathlib.Path(p) for p in sys.argv[1:]] or sorted(p.parent for p in (ROOT / "results/runs").glob("*/fast.jsonl"))
for run in runs:
    sp = run / "summary.json"
    if not sp.exists():
        continue
    s = json.load(open(sp))
    t = []
    for line in open(run / "fast.jsonl"):
        try:
            t.append(json.loads(line)["t"])
        except (ValueError, KeyError):
            pass
    t = np.array(t)
    if len(t) < 3:
        continue
    extra = np.diff(t) - CFG["recorder"]["fast_s"]
    scored = t[1:] > s["scored_s"][0]
    st = extra[scored]
    s["health"] = {"control_stall_max_s": round(float(max(st.max(), 0)) if len(st) else 0.0, 1),
                   "control_stalls_2s": int((st >= 2).sum()),
                   "control_stalls_2s_warmup": int((extra[~scored] >= 2).sum()),
                   "harness_failure": bool((extra >= 2).any())}
    sp.write_text(json.dumps(s, indent=1))
    if s["health"]["harness_failure"] or s["health"]["control_stalls_2s_warmup"]:
        print(run.name, s["health"])
