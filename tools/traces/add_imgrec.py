"""New-service shift (docs/decisions.md 2026-10-28): add image recognition as a 5th column to the run traces.

The four existing columns are copied unchanged. Image recognition gets Huawei function 132 (the most variable
minute-to-minute load among the functions active on every profiled day, functions in use excluded) over the
run's Huawei day, in the same window as the other functions (previous day's last 6 h + the day, 24x), scaled so
that its busiest minute of the scored hour needs peak_copies copies at its per-copy capacity
(config/trace_scaling_imgrec.yaml). load_scale does not apply: it corrected the other entries' scaling.
Usage: SCALING_CONFIG=config/trace_scaling_imgrec.yaml python tools/traces/add_imgrec.py <huawei zip>
Writes data/traces/runs_imgrec/<split>_<n>.csv.gz and index.json.
"""
import gzip
import json
import sys
import zipfile

import numpy as np
import pandas as pd

from build_traces import CFG, ROOT, huawei_shapes, minute_peak

FUNCTION = "132"


def main():
    zf = zipfile.ZipFile(sys.argv[1])
    img = CFG["entries"]["imgrec"]
    peak = img["peak_copies"] * img["capacity_per_copy_rps"]
    src, out = ROOT / "data/traces/runs", ROOT / "data/traces/runs_imgrec"
    out.mkdir(parents=True, exist_ok=True)
    index = json.load(open(src / "index.json"))
    index["imgrec"] = {"huawei_function": FUNCTION, "peak_copies": img["peak_copies"],
                       "capacity_per_copy_rps": img["capacity_per_copy_rps"], "peak_rps": peak}
    for meta in index["runs"]:
        df = pd.read_csv(src / f"{meta['run']}.csv.gz")
        shape = huawei_shapes(zf, meta["huawei_day"], {"imgrec": FUNCTION})[0]["imgrec"]
        df["imgrec"] = np.round(shape * peak, 3)
        with gzip.open(out / f"{meta['run']}.csv.gz", "wt") as f:
            df.to_csv(f, index=False)
        meta["peak_rps_scaled"]["imgrec"] = round(minute_peak(df["imgrec"].to_numpy()), 2)
        print(meta["run"], meta["huawei_day"], meta["peak_rps_scaled"]["imgrec"],
              f"mean {df['imgrec'].iloc[900:].mean():.2f} req/s")
    (out / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
