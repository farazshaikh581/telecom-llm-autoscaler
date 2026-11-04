"""Recompute the GPU energy of finished runs per GPU (2026-10-06). Before this date gpu_wh integrated
the rows of all GPUs as one series, which gives about the mean of the GPUs, not their sum. Only the
energy block's GPU fields of summary.json change: gpu_wh becomes the sum over GPUs, gpu<i>_wh each GPU.

  python tools/recompute_gpu_energy.py [results/runs/<name> ...]   (default: every run with gpu.csv)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from analyze_run import gpu_energy  # noqa: E402

runs = [pathlib.Path(p) for p in sys.argv[1:]] or sorted(p.parent for p in (ROOT / "results/runs").glob("*/gpu.csv"))
for run in runs:
    sp, mp = run / "summary.json", run / "meta.json"
    if not (sp.exists() and mp.exists() and (run / "gpu.csv").exists()):
        continue
    s, t0 = json.load(open(sp)), json.load(open(mp))["start_epoch"]
    total, per = gpu_energy(run / "gpu.csv", t0 + s["scored_s"][0], t0 + s["scored_s"][1])
    en = {k: v for k, v in s.get("energy", {}).items() if not (k.startswith("gpu") and k.endswith("_wh"))}
    old = s.get("energy", {}).get("gpu_wh")
    s["energy"] = {"gpu_wh": round(total, 2) if total is not None else None,
                   **{f"gpu{i}_wh": (round(v, 2) if v is not None else None) for i, v in per.items()}, **en}
    sp.write_text(json.dumps(s, indent=1))
    print(f"{run.name}: gpu_wh {old} -> {s['energy']['gpu_wh']}  " + "  ".join(f"gpu{i} {v:.2f}" for i, v in per.items() if v is not None))
