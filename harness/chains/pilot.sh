#!/bin/bash
# Run-length pilot (docs/decisions.md): HPA and TelecomGPT (joint), each on development days
# dev_0 and dev_1, interleaved so slow drifts affect both controllers alike. 4 runs of 75 min
# plus resets, about 5.5 h. TelecomGPT stays loaded for the whole chain.
# Usage: [PREFIX=pilot2] harness/chains/pilot.sh  (detached: tools/detach.sh pilot harness/chains/pilot.sh)
# PREFIX names the runs (<prefix>_hpa_dev0, ...); "pilot" = phase 1, "pilot2" = second testbed.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
P=${PREFIX:-pilot}
harness/llm_serve.sh telecomgpt-r1 || exit 1
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/dev_$3.csv.gz --name $2 < /dev/null
}
for day in 0 1; do
  run hpa:target=70 ${P}_hpa_dev$day $day
  run llm:port=8001,mode=joint ${P}_telecomgpt_dev$day $day
done
echo "pilot done $(date -u +%FT%TZ)"
