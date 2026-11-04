#!/bin/bash
# Smoke tests for the native KEDA and predictive HPA controllers (15 min each, dev_0),
# started after the weekend chain so runs never overlap.
# Usage: tools/detach.sh smoke_native harness/chains/smoke_native.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
while [ -f runs/weekend_tune.pid ] && kill -0 "$(cat runs/weekend_tune.pid)" 2>/dev/null; do sleep 60; done
for spec in "keda:util=0.7 smoke_keda" "phpa:target=70,lookahead=60,history=6 smoke_phpa"; do
  set -- $spec
  [ -f results/runs/$2/summary.json ] && continue
  $PY harness/run.py --controller $1 --trace data/traces/runs/dev_0.csv.gz --name $2 --duration 900
done
echo "smoke_native done $(date -u +%FT%TZ)"
