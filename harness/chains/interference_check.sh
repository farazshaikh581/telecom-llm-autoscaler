#!/bin/bash
# Do the LLMs disturb the workers? (2026-10-06, docs/decisions.md) Fixed copies, evenly spaced
# arrivals at 50% of their capacity (data/traces/interference_50pct.csv.gz, 15 min, no queueing), so
# latency shows any interference directly. Alternating: both LLMs idle (loaded) / both answering
# requests back to back (tools/llm_busy.py, worst case), 3 pairs. Waits for the pid files in WAIT.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 30; done; done
harness/llm_serve.sh both || exit 1
STATIC=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5
run() {
  [ -f results/runs/$1/summary.json ] && { echo "skip $1 (done)"; return 0; }
  $PY harness/run.py --controller $STATIC --trace data/traces/interference_50pct.csv.gz --name $1 --arrivals even < /dev/null
}
for i in 1 2 3; do
  run intf_idle_$i || exit 1
  $PY tools/llm_busy.py 8001 8002 > runs/llm_busy_$i.log 2>&1 & B=$!
  sleep 20
  kill -0 $B 2>/dev/null || { echo "llm_busy stopped early"; cat runs/llm_busy_$i.log; exit 1; }
  run intf_busy_$i; rc=$?
  kill $B; wait $B; cat runs/llm_busy_$i.log
  [ $rc = 0 ] || exit 1
done
echo "interference_check done $(date -u +%FT%TZ)"
