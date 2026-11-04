#!/bin/bash
# GPU outage (2026-10-25): the controllers that need no GPU, from the two blocks left in high_load_r2.sh
# (test days 1 and 3, +25% load, repeat 2), in each block's order, with its drift reference first. The
# blocks' LLM runs follow when the GPUs are back (high_load_r2.sh skips the runs done here).
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 $4 < /dev/null
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5
for d in 1 3; do
  run $REF ref5_t$d data/traces/interference_50pct.csv.gz "--arrivals even --seed 2" || exit 1
  $PY -c "
import json
for b in json.load(open('results/test_order_hl_r2.json')):
    if b['day'] == $d:
        for r in b['runs']:
            if not r['controller'].startswith('llm:'): print(r['controller'], r['name'])" |
  while read spec name; do run $spec $name data/traces/runs/test_$d.csv.gz "--seed 2 --scale 1.25" || exit 1; done || exit 1
done
echo "gpu_outage_hl done $(date -u +%FT%TZ)"
