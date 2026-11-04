#!/bin/bash
# LLM runs missing on the 4-service design: TelecomGPT (joint) on dev_2, Qwen3.5-27B (joint) on
# the 3 development days (it had only the smoke run). One model served at a time; the switch
# happens between runs. About 5.5 h.
# Usage: [PREFIX=3] tools/detach.sh llm_fill harness/chains/llm_fill.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
X=${PREFIX:-}
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 < /dev/null
}
harness/llm_serve.sh telecomgpt-r1 || exit 1
run llm:port=8001,mode=joint pilot${X}_telecomgpt_dev2 dev_2 || exit 1
harness/llm_serve.sh qwen35-27b || exit 1
for d in 0 1 2; do run llm:port=8002,mode=joint pilot${X}_qwen35_dev$d dev_$d || exit 1; done
echo "llm_fill done $(date -u +%FT%TZ)"
