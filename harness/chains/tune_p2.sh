#!/bin/bash
# Baseline tuning on the second testbed (redo list step 9, docs/testbed_4w.md), on the days in DAYS
# (default "2"; 2026-10-07: "1 2", each setting's mean over the days is ranked), 4 settings
# per controller as in phase 1: HPA CPU target 50-80, KEDA utilization 0.5-0.8, predictive HPA
# look-ahead 60/120 s x history 6/12 at the picked HPA target. Pick rule (tools/tuning_table.py
# --pick, docs/decisions.md): within 3 points of the lowest violation rate, fewest mean copies.
# 12 runs of 75 min plus resets, about 17 h. Waits for the KEDA smoke test (runs/smoke_keda3.pid)
# and skips the KEDA sweep if that smoke test did not finish.
# Usage: [PREFIX=tune3 SMOKE=smoke3 DAYS="1 2"] tools/detach.sh tune_p2 harness/chains/tune_p2.sh
# PREFIX names the runs (default tune2); SMOKE is the smoke-run prefix whose KEDA run gates the KEDA sweep.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
P=${PREFIX:-tune2}
SM=${SMOKE:-smoke2}
while [ -f runs/smoke_keda3.pid ] && kill -0 "$(cat runs/smoke_keda3.pid)" 2>/dev/null; do sleep 60; done
DAYS=${DAYS:-2}
run() {  # one setting on every tuning day; skips runs that already finished, so the chain can be restarted
  for d in $DAYS; do
    [ -f results/runs/${2}_dev$d/summary.json ] && { echo "skip ${2}_dev$d (done)"; continue; }
    $PY harness/run.py --controller $1 --trace data/traces/runs/dev_$d.csv.gz --name ${2}_dev$d < /dev/null
  done
}
for t in 50 60 70 80; do run hpa:target=$t ${P}_hpa_t${t}; done
PICK=$($PY tools/tuning_table.py ${P}_hpa_ --pick)
HPA_TARGET=$(echo "$PICK" | sed -n 's/.*target=\([0-9]*\).*/\1/p')
[ -n "$HPA_TARGET" ] || { echo "no HPA pick ($PICK)"; exit 1; }
echo "HPA pick: $PICK"
if [ -f results/runs/${SM}_keda/summary.json ]; then
  for u in 0.5 0.6 0.7 0.8; do run keda:util=$u ${P}_keda_u${u/./}; done
else
  echo "KEDA smoke test missing: KEDA sweep skipped"
fi
for la in 60 120; do
  for h in 6 12; do run phpa:target=$HPA_TARGET,lookahead=$la,history=$h ${P}_phpa_la${la}_h${h}; done
done
echo "tune_p2 done $(date -u +%FT%TZ)"
