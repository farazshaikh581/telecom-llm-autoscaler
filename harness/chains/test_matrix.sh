#!/bin/bash
# Main test runs (docs/decisions.md): every ready controller once on each of the 5 held-out test
# days, in one fixed random order (results/test_order.json, written once, seed 2026) so a restart
# keeps it; finished runs are skipped. First a performance fingerprint (the shared base run:
# fixed copies, evenly spaced arrivals, 15 min) to compare the testbed before and after changes.
# No run starts after DEADLINE (UTC, e.g. 2026-09-28T05:00:00) so nothing runs when the VMs go down.
# Usage: DEADLINE=<iso> FP=<label> tools/detach.sh test_matrix harness/chains/test_matrix.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
: "${DEADLINE:?set DEADLINE}" "${FP:?set FP (fingerprint label)}"
end=$(date -u -d "$DEADLINE" +%s)
while [ -f runs/ours_finetune.pid ] && kill -0 "$(cat runs/ours_finetune.pid)" 2>/dev/null; do sleep 60; done
harness/llm_serve.sh telecomgpt-r1 || exit 1
time_left() { [ $(( $(date -u +%s) + ${1:-5000} )) -lt $end ]; }
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  time_left "${5:-5000}" || { echo "deadline: not starting $2"; return 3; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 ${4:-} < /dev/null   # ssh in the harness must not read the run list
}
run static:teastore-webui=3,thumbnailer=3,compression=3,imgrec=3,hotel-frontend=2,hotel-search=2,hotel-reservation=4,hotel-rate=2 \
    fp_${FP} dev_0 "--duration 900 --arrivals even" 1500 || exit 1
$PY - <<'PYEOF'
import json, pathlib, random
p = pathlib.Path("results/test_order.json")
if not p.exists():
    ctrls = {"hpa_default": "hpa:target=70", "hpa_tuned": "hpa:target=80", "keda": "keda:util=0.7",
             "phpa": "phpa:target=80,lookahead=120,history=6", "aware": "aware:ckpt=rl/checkpoints/aware_ft.pt",
             "firm": "firm:ckpt=rl/checkpoints/firm_ft.pt", "ours": "ours:ckpt=rl/checkpoints/ours_qrdqn_ft",
             "telecomgpt": "llm:port=8001,mode=joint"}
    runs = [{"name": f"test_{c}_t{d}", "controller": spec, "trace": f"test_{d}"} for c, spec in ctrls.items() for d in range(5)]
    random.Random(2026).shuffle(runs)
    p.write_text(json.dumps(runs, indent=1))
PYEOF
$PY -c "import json;[print(r['controller'],r['name'],r['trace']) for r in json.load(open('results/test_order.json'))]" |
while read spec name trace; do
  run "$spec" "$name" "$trace"; rc=$?
  [ $rc = 3 ] && break
done
echo "test_matrix stopped $(date -u +%FT%TZ)"
