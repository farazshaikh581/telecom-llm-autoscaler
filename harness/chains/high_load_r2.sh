#!/bin/bash
# Higher load, second repeat (docs/decisions.md 2026-10-23): every rate x 1.25, seed 2, the 8 controllers of
# after_matrix.sh plus the budget-aware variant, 5 test days. Each day runs in the GPU layout its +25% repeat 1
# did not use (B on even days, A on odd), so every day has one repeat in each layout, as in the main matrix.
# A block is one test day: a drift reference run, then its 9 runs in a random order. Days 0, 2, 4 first, then
# 1, 3 (one layout switch). Order written once (results/test_order_hl_r2.json); finished runs are skipped.
# About 61 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh high_load_r2 harness/chains/high_load_r2.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
$PY - <<'PYEOF' || exit 1
import json, pathlib, random
p = pathlib.Path("results/test_order_hl_r2.json")
if not p.exists():
    c = json.load(open("results/test_picks_v2.json"))["controllers"]
    runs = [(f"hl_{k}", c[k]) for k in ("hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint")]
    pick = open("rl/checkpoints/ours_budget_pick.txt").read().strip()
    gate = next(l for l in open("runs/ours_budget_cluster.log") if l.startswith("gate:")).split()[1]
    runs.append(("xb_hl", f"ours:ckpt=rl/checkpoints/{pick}_{'ft' if gate == 'pretrained' else 'scratch'}_dev2"))
    rng = random.Random(2028)
    blocks = []
    for d in (0, 2, 4, 1, 3):
        r = runs[:]
        rng.shuffle(r)
        blocks.append({"day": d, "layout": "BA"[d % 2],
                       "runs": [{"name": f"{n}_t{d}_r2", "controller": s} for n, s in r]})
    p.write_text(json.dumps(blocks, indent=1))
PYEOF
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 ${4:-} < /dev/null
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as in the main matrix
$PY -c "
import json
import os
for b in json.load(open('results/test_order_hl_r2.json')):
    if all(os.path.exists('results/runs/' + r['name'] + '/summary.json') for r in b['runs']): continue   # done: no layout switch
    print('block', b['day'], b['layout'])
    for r in b['runs']: print('run', r['controller'], r['name'])" |
while read kind a b; do
  if [ $kind = block ]; then
    d=$a
    if [ "$(cut -d' ' -f1 runs/llm_layout.txt 2>/dev/null)" != "$b" ]; then
      echo "$(date -u +%T) layout $b"; harness/llm_containers.sh $b < /dev/null || exit 1
    fi
    harness/llm_serve.sh both < /dev/null || exit 1
    run $REF ref5_t$d data/traces/interference_50pct.csv.gz "--arrivals even --seed 2" || exit 1
  else
    run $a $b data/traces/runs/test_$d.csv.gz "--seed 2 --scale 1.25" || exit 1
  fi
done || exit 1
echo "high_load_r2 done $(date -u +%FT%TZ)"
