#!/bin/bash
# Smoke tests on the second testbed (redo list step 6, docs/testbed_4w.md): every controller
# family whose code runs without a trained checkpoint, 15 min each on dev_0, then Qwen3.5
# (model switch between runs, never during one: loading stalls the control node's disk).
# Ends with TelecomGPT loaded again for the pilot. RL controllers are smoke-tested in step 10.
# Usage: [PREFIX=smoke3] tools/detach.sh smoke_p2 harness/chains/smoke_p2.sh  (default prefix smoke2)
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
P=${PREFIX:-smoke2}
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/dev_0.csv.gz --name $2 --duration 900 < /dev/null
}
harness/llm_serve.sh telecomgpt-r1 || exit 1
run static:$(.venv/bin/python -c "import sys; sys.path.insert(0,'harness'); from common import SERVICES; print(','.join(f'{s}=3' for s in SERVICES))") ${P}_static
run hpa:target=70 ${P}_hpa
run keda:util=0.7 ${P}_keda
run phpa:target=70,lookahead=60,history=6 ${P}_phpa
run llm:port=8001,mode=joint ${P}_telecomgpt
run llm:port=8001,mode=per-service ${P}_telecomgpt_ps
run llm:port=8001,mode=oracl ${P}_oracl
harness/llm_serve.sh qwen35-27b || exit 1
run llm:port=8002,mode=joint ${P}_qwen35
harness/llm_serve.sh telecomgpt-r1 || exit 1
echo "smoke_p2 done $(date -u +%FT%TZ)"
