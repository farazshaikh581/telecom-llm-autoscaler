#!/bin/bash
# Tuning runs for KEDA and predictive HPA on dev_2: 4 settings each, the same budget as the HPA
# CPU-target sweep (harness/chains/weekend_tune.sh). PHPA uses the tuned HPA's CPU target.
# 8 runs of 75 min, about 10.5 h. Compare with: python tools/tuning_table.py tune_keda_ (tune_phpa_)
# Usage: HPA_TARGET=<tuned %> tools/detach.sh tune_rule_based harness/chains/tune_rule_based.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
: "${HPA_TARGET:?set HPA_TARGET to the tuned HPA CPU target}"
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/dev_2.csv.gz --name $2
}
for u in 0.5 0.6 0.7 0.8; do
  run keda:util=$u tune_keda_u${u/./}_dev2
done
for la in 60 120; do
  for h in 6 12; do
    run phpa:target=$HPA_TARGET,lookahead=$la,history=$h tune_phpa_la${la}_h${h}_dev2
  done
done
echo "tune_rule_based done $(date -u +%FT%TZ)"
