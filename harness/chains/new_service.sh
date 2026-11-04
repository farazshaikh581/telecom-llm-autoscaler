#!/bin/bash
# New-service shift (docs/decisions.md 2026-10-28): image recognition joins the cluster as a 5th scaled service,
# never seen in any training, tuning or calibration of the controllers. The 8 controllers of after_matrix.sh,
# 5 test days x 2 repeats (seed = repeat), all in GPU layout A (no switch: both GPU faults came at switches).
# Traces: data/traces/runs_imgrec (the main matrix's 4 columns unchanged, plus image recognition from Huawei
# function 132); config: config/harness_imgrec.yaml and config/trace_scaling_imgrec.yaml. Each run pairs with the
# main-matrix run of its day and repeat. A block is one (test day, repeat): a drift reference run with the
# 4-service config (image recognition idle at 1 copy), then the 8 runs in a random order (results/test_order_ns.json,
# seed 2030). Finished runs are skipped. About 113 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh new_service harness/chains/new_service.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
$PY - <<'PYEOF' || exit 1
import json, pathlib, random
p = pathlib.Path("results/test_order_ns.json")
if not p.exists():
    c = json.load(open("results/test_picks_v2.json"))["controllers"]
    runs = [(f"ns_{k}", c[k]) for k in ("hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint")]
    rng = random.Random(2030)
    blocks = []
    for d in range(5):
        for rep in (1, 2):
            r = runs[:]
            rng.shuffle(r)
            blocks.append({"day": d, "repeat": rep, "runs": [{"name": f"{n}_t{d}_r{rep}", "controller": s} for n, s in r]})
    p.write_text(json.dumps(blocks, indent=1))
PYEOF
[ "$(cut -d' ' -f1 runs/llm_layout.txt 2>/dev/null)" = A ] || { echo "GPU layout is not A; not switching (see decisions.md)"; exit 1; }
harness/llm_serve.sh both < /dev/null || exit 1
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 ${4:-} < /dev/null
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as in the main matrix
$PY -c "
import json
for b in json.load(open('results/test_order_ns.json')):
    print('block', b['day'], b['repeat'])
    for r in b['runs']: print('run', r['controller'], r['name'])" |
while read kind a b; do
  if [ $kind = block ]; then
    d=$a; rep=$b
    kubectl -n sebs scale deploy/imgrec --replicas=1 >/dev/null || exit 1   # idle during the 4-service reference
    run $REF ref7_t${d}_r$rep data/traces/interference_50pct.csv.gz "--arrivals even --seed $rep" || exit 1
  else
    (export HARNESS_CONFIG=config/harness_imgrec.yaml SCALING_CONFIG=config/trace_scaling_imgrec.yaml
     run $a $b data/traces/runs_imgrec/test_$d.csv.gz "--seed $rep") || exit 1
  fi
done || exit 1
echo "new_service done $(date -u +%FT%TZ)"
