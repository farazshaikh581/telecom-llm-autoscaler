"""Evaluate the literature RL agents' training seeds in the simulator on the development days
(dev_0-2, 3 simulator seeds each), greedy, no learning, and pick the seed that goes to the cluster:
the median seed by mean violation rate (not the best, so the cluster sees a typical training run).

  python rl/eval_lit.py   -> rl/eval_lit.csv, rl/checkpoints/<agent>_pick.txt (e.g. aware_s1)
"""
import pathlib
import sys

import pandas as pd
import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rl"))
import train_sim as T  # noqa: E402

DEV = [ROOT / f"data/traces/runs/dev_{d}.csv.gz" for d in range(3)]
SIM_SEEDS = [1, 2, 3]


def main():
    torch.set_num_threads(1)
    rows = []
    for kind in ("aware", "firm"):
        for ck in sorted((ROOT / "rl/checkpoints").glob(f"{kind}_s[0-9].pt")):
            ag = T.make_agent(kind, 0)
            ag.load(str(ck))
            for d, path in enumerate(DEV):
                trace = pd.read_csv(path)
                for seed in SIM_SEEDS:
                    rows.append({"agent": ck.stem, "kind": kind, "day": d, "seed": seed,
                                 **T.episode_run(ag, trace, seed=seed, learn=False)})
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "rl/eval_lit.csv", index=False)
    t = df.groupby(["kind", "agent"]).agg(violation_k3=("violation_k3", "mean"), copies_mean=("copies_mean", "mean"))
    print(t.round(3).to_string())
    for kind, g in t.groupby(level="kind"):
        g = g.droplevel("kind").sort_values("violation_k3")
        pick = g.index[len(g) // 2]
        (ROOT / f"rl/checkpoints/{kind}_pick.txt").write_text(pick + "\n")
        print(f"{kind}: picked {pick} (median of {len(g)} seeds)")


if __name__ == "__main__":
    main()
