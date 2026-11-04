#!/bin/bash
# After the cluster phase (2026-10-08): repeat the flagged HPA tuning run, re-pick the HPA target on
# clean runs only, and redo the predictive HPA sweep if the target changed.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
[ -f results/runs/tune4_hpa_t70_dev1/summary.json ] || \
  $PY harness/run.py --controller hpa:target=70 --trace data/traces/runs/dev_1.csv.gz --name tune4_hpa_t70_dev1 < /dev/null || exit 1
T=$($PY tools/tuning_table.py tune4_hpa_ --pick | sed -n 's/.*target=\([0-9]*\).*/\1/p')
echo "HPA pick on clean runs: $T"
if [ "$T" != 70 ]; then
  for la in 60 120; do for h in 6 12; do for d in 1 2; do
    n=tune4_phpa_t${T}_la${la}_h${h}_dev$d
    [ -f results/runs/$n/summary.json ] || $PY harness/run.py --controller phpa:target=$T,lookahead=$la,history=$h --trace data/traces/runs/dev_$d.csv.gz --name $n < /dev/null || exit 1
  done; done; done
fi
echo "after_cluster_phase done $(date -u +%FT%TZ)"
