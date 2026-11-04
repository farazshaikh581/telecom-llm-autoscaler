"""LLM decision cost per service setup (2026-10-01): the real controller code (harness/controllers/llm.py)
on real observations recorded in a pilot run, with the scaled-service list cut to each setup
(all8, no_comp = without compression, four = TeaStore front end, hotel reservation, thumbnailer,
image recognition). Modes joint, per-service and oracl. Reports decision time (wall clock, as the
harness measures it), prompt and completion tokens, valid answers.

  python tools/llm_setup_timing.py <port> [run] [n_obs]  -> table, results/sim_setups/llm_timing.csv
"""
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import common  # noqa: E402
from controllers import llm as L  # noqa: E402

SETUPS = {"all8": list(common.SERVICES),
          "no_comp": [s for s in common.SERVICES if s != "compression"],
          "four": ["teastore-webui", "hotel-reservation", "thumbnailer", "imgrec"]}


def main():
    port = sys.argv[1]
    run = sys.argv[2] if len(sys.argv) > 2 else "pilot2b_telecomgpt_dev0"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    recs = [json.loads(l) for l in open(ROOT / "results/runs" / run / "decisions.jsonl")]
    recs = [r for r in recs if r["k"] >= 15]
    obs = [recs[i]["obs"] for i in np.linspace(0, len(recs) - 1, n).astype(int)]
    rows = []
    for setup, svcs in SETUPS.items():
        L.SERVICES = {s: common.SERVICES[s] for s in svcs}
        for mode in ("joint", "per-service", "oracl"):
            c = L.LLM(port=port, mode=mode)
            c.setup({})
            for i, o in enumerate(obs):
                t = time.time()
                c.decide(o)
                calls = c.last["calls"]
                rows.append({"setup": setup, "services": len(svcs), "mode": mode, "obs": i,
                             "seconds": round(time.time() - t, 3), "calls": len(calls),
                             "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in calls),
                             "completion_tokens": sum(x.get("completion_tokens", 0) + x.get("reasoning_tokens", 0) for x in calls),
                             "valid": c.last["valid"]})
                print(json.dumps(rows[-1]), flush=True)
    t = pd.DataFrame(rows)
    out = ROOT / "results/sim_setups"
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "llm_timing.csv", index=False)
    g = t.groupby(["mode", "setup"], sort=False)
    print(pd.DataFrame({"services": g.services.first(), "sec_median": g.seconds.median(), "sec_p95": g.seconds.quantile(0.95),
                        "sec_max": g.seconds.max(), "prompt_tok": g.prompt_tokens.mean().round(0),
                        "completion_tok": g.completion_tokens.mean().round(0), "valid": g.valid.mean()}).round(2).to_string())


if __name__ == "__main__":
    main()
