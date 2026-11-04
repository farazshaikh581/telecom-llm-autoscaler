#!/bin/bash
# Waits for the KEDA/PHPA smoke tests, checks them, picks the tuned HPA target from the dev_2
# sweep (tools/tuning_table.py), then runs harness/chains/tune_rule_based.sh.
# Gate per smoke run: summary written, error rate under 1%, at least one scaling action.
# Usage: tools/detach.sh tune_after_smoke harness/chains/tune_after_smoke.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
while [ -f runs/smoke_native.pid ] && kill -0 "$(cat runs/smoke_native.pid)" 2>/dev/null; do sleep 60; done
for r in smoke_keda smoke_phpa; do
  $PY - "$r" <<'PYEOF' || { echo "gate failed: $r"; exit 1; }
import json, sys
s = json.load(open(f"results/runs/{sys.argv[1]}/summary.json"))
ok = s["overall"]["error_rate"] < 0.01 and s["stability"]["scaling_actions_per_hour"] > 0
print(sys.argv[1], "errors", s["overall"]["error_rate"], "actions/h", s["stability"]["scaling_actions_per_hour"], "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
PYEOF
done
$PY tools/tuning_table.py tune_hpa_
SPEC=$($PY tools/tuning_table.py tune_hpa_ --pick) || exit 1
export HPA_TARGET=${SPEC#hpa:target=}
echo "tuned HPA target: $HPA_TARGET"
harness/chains/tune_rule_based.sh
