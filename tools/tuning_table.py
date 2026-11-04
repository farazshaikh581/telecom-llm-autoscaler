"""Compare tuning runs of one controller: KPIs from each run's summary.json, one row per run.

  python tools/tuning_table.py tune_hpa_          # every results/runs/tune_hpa_* run
  python tools/tuning_table.py tune_hpa_ --pick   # print only the picked controller spec

Pick rule (docs/decisions.md): among runs within 3 points of the lowest SLO violation rate (about
the noise of one run), the one with the fewest mean copies.

Columns: controller spec, SLO violation rate and mean attainment (k = 3), error rate, mean and max
ready copies, time with pods pending, scaling actions and reversals per hour, host package energy.
"""
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]


def row(run):
    s = json.load(open(run / "summary.json"))
    meta = json.load(open(run / "meta.json"))
    o, c, st = s["overall"], s["cost"], s["stability"]
    return {"run": run.name, "controller": meta["controller"], "violation_k3": o["violation_rate_k3"],
            "attainment_k3": o["attainment_k3_mean"], "errors": o["error_rate"],
            "copies_mean": c["mean_ready_total"], "copies_max": c["max_ready_total"],
            "pending_share": c["share_time_pending"], "actions_h": st["scaling_actions_per_hour"],
            "reversals_h": st["reversals_per_hour"], "host_wh": s["energy"].get("host_pkg_total_wh")}


TOLERANCE = 0.03


def pick(t):
    """The rule on each setting's mean over its runs (one run per tuning day; 2026-10-07: two days)."""
    g = t.groupby("controller", as_index=False)[["violation_k3", "copies_mean"]].mean()
    g["runs"] = t.groupby("controller")["run"].count().reindex(g.controller).to_numpy()
    near = g[g.violation_k3 <= g.violation_k3.min() + TOLERANCE]
    return near.sort_values(["copies_mean", "violation_k3"]).iloc[0], g


def main(prefix, only_pick=False):
    runs = sorted(p for p in (ROOT / "results/runs").glob(prefix + "*") if (p / "summary.json").exists()
                  and not json.load(open(p / "summary.json")).get("health", {}).get("harness_failure"))   # clean runs only
    if not runs:
        sys.exit(f"no finished runs match {prefix}*")
    t = pd.DataFrame([row(r) for r in runs]).sort_values(["violation_k3", "copies_mean"])
    best, g = pick(t)
    if only_pick:
        print(best.controller)
        return
    print(t.to_string(index=False))
    print(g.sort_values(["violation_k3", "copies_mean"]).to_string(index=False))
    print(f"picked: {best.controller} (mean of {best.runs} runs)")


if __name__ == "__main__":
    main(sys.argv[1], "--pick" in sys.argv[2:])
