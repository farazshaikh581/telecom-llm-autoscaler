#!/bin/bash
# Qwen3.5-27B (joint) on dev_0, paired with the pilot's HPA and TelecomGPT runs on that day;
# then the HPA CPU-target sweep (50/60/70/80%) on dev_2 for tuning. 5 runs of 75 min, about 6.5 h.
# Qwen stays loaded for the sweep, so GPU idle power matches the pilot's HPA runs.
# Usage: tools/detach.sh weekend_tune harness/chains/weekend_tune.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
harness/llm_serve.sh qwen35-27b || exit 1
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2
}
run llm:port=8002,mode=joint pilot_qwen35_dev0 dev_0
for t in 50 60 70 80; do
  run hpa:target=$t tune_hpa_t${t}_dev2 dev_2
done
echo "weekend_tune done $(date -u +%FT%TZ)"
