#!/bin/bash
# GPU outage (2026-10-13): the controllers that need no GPU, from the current block of the main matrix
# (test day 2, repeat 2), in the block's order, with its drift reference first. The block's LLM runs
# follow when the GPUs are back (test_matrix_v2.sh skips the runs done here). Recorded in docs/decisions.md.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 $4 < /dev/null
}
run static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5 ref2_t2_r2 data/traces/interference_50pct.csv.gz "--arrivals even --seed 2" || exit 1
$PY -c "
import json
for r in json.load(open('results/test_order_v2.json'))[5]['runs']:
    if not r['controller'].startswith('llm:'): print(r['controller'], r['name'])" |
while read spec name; do run $spec $name data/traces/runs/test_2.csv.gz "--seed 2" || exit 1; done || exit 1
echo "gpu_outage done $(date -u +%FT%TZ)"
