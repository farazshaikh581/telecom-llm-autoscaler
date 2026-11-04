#!/bin/bash
# History-order test (docs/decisions.md 2026-10-27): the LLMs with the history newest first and age-labeled
# (order=newest), ORACL-style and joint, both models, 5 test days, 1 repeat, all in GPU layout A. Each run pairs
# with the main-matrix run in layout A on its day: repeat 1 (seed 1) on even days, repeat 2 (seed 2) on odd days.
# A block is one test day: a drift reference run, then its 4 runs in a random order (results/test_order_ho.json,
# seed 2029). Finished runs are skipped. About 27 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh history_order harness/chains/history_order.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
$PY - <<'PYEOF' || exit 1
import json, pathlib, random
p = pathlib.Path("results/test_order_ho.json")
if not p.exists():
    runs = [(f"ho_{m}_{mode}", f"llm:port={port},mode={mode},order=newest")
            for m, port in (("tgpt", 8001), ("qwen", 8002)) for mode in ("joint", "oracl")]
    rng = random.Random(2029)
    blocks = []
    for d in range(5):
        r = runs[:]
        rng.shuffle(r)
        blocks.append({"day": d, "seed": 1 if d % 2 == 0 else 2,
                       "runs": [{"name": f"{n}_t{d}", "controller": s} for n, s in r]})
    p.write_text(json.dumps(blocks, indent=1))
PYEOF
[ "$(cut -d' ' -f1 runs/llm_layout.txt 2>/dev/null)" = A ] || { echo "GPU layout is not A; not switching (see decisions.md)"; exit 1; }
harness/llm_serve.sh both < /dev/null || exit 1
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 ${4:-} < /dev/null
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as in the main matrix
$PY -c "
import json
for b in json.load(open('results/test_order_ho.json')):
    print('block', b['day'], b['seed'])
    for r in b['runs']: print('run', r['controller'], r['name'])" |
while read kind a b; do
  if [ $kind = block ]; then
    d=$a; seed=$b
    run $REF ref6_t$d data/traces/interference_50pct.csv.gz "--arrivals even --seed $seed" || exit 1
  else
    run $a $b data/traces/runs/test_$d.csv.gz "--seed $seed" || exit 1
  fi
done || exit 1
echo "history_order done $(date -u +%FT%TZ)"
