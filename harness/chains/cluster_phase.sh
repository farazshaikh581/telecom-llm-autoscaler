#!/bin/bash
# Cluster phase on the final design (2026-10-07): (1) LLM development check, both models in the three
# modes on dev_0; (2) baseline tuning on dev_1 and dev_2; (3) AWARE and FIRM fine-tuning (median
# training seed); (4) our agent: pretraining gate (picked variant, median seed). About 55 h. Every
# run carries the health check; flagged runs are listed at the end of each step.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
health() { $PY - "$1" <<'PYEOF'
import json, pathlib, sys
bad = [p.parent.name for p in pathlib.Path("results/runs").glob(sys.argv[1] + "*/summary.json")
       if json.load(open(p)).get("health", {}).get("harness_failure")]
print(f"health {sys.argv[1]}*: " + (", ".join(bad) + " FLAGGED (rerun)" if bad else "all clean"))
PYEOF
}
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 < /dev/null
}
harness/llm_serve.sh both || exit 1
for m in telecomgpt:8001 qwen35:8002; do
  for mode in joint per-service oracl; do
    run llm:port=${m#*:},mode=$mode llmdev4_${m%:*}_${mode/-/}_dev0 dev_0 || exit 1
  done
done
health llmdev4_
PREFIX=tune4 SMOKE=smoke3 DAYS="1 2" harness/chains/tune_p2.sh || exit 1
health tune4_
PREFIX=4 harness/chains/finetune_lit_rl.sh || exit 1
health finetune4_; health smoke4_
PREFIX=4 harness/chains/ours_finetune.sh || exit 1
health finetune4_ours; health scratch4_
echo "cluster_phase done $(date -u +%FT%TZ)"
