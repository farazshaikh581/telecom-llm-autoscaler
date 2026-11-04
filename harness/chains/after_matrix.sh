#!/bin/bash
# After the main test matrix (docs/analysis_plan.md, robustness checks), in one set of blocks:
#  - higher load: every rate x 1.25, 8 controllers (HPA 70%, KEDA, predictive HPA, AWARE, FIRM, ours,
#    TelecomGPT joint, Qwen3.5 joint; the picks of results/test_picks_v2.json), 5 test days, 1 repeat;
#  - prompt wording: both LLMs, joint, the alternative system prompt (prompt=alt), nominal load;
#  - prompt ablation: both LLMs, joint, ablate=zeroshot|history|domain|cot, nominal load.
# A block is one test day: a drift reference run, then its 18 runs in a random order. Seed 1 and the
# GPU layout of the main matrix's repeat 1 on that day (A on even days, B on odd), so each prompt run
# pairs with its default-prompt run there. Days 0, 2, 4 first, then 1, 3 (one layout switch).
# Order written once (results/test_order_after.json); finished runs are skipped. About 118 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh after_matrix harness/chains/after_matrix.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
grep -q "^test_matrix_v2 done" runs/test_matrix_v2.log || { echo "main matrix not finished: not starting"; exit 1; }
$PY - <<'PYEOF' || exit 1
import json, pathlib, random
p = pathlib.Path("results/test_order_after.json")
if not p.exists():
    c = json.load(open("results/test_picks_v2.json"))["controllers"]
    runs = [(f"hl_{k}", c[k], "--scale 1.25") for k in
            ("hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint")]
    runs += [("pa_tgpt_joint", "llm:port=8001,mode=joint,prompt=alt", ""),
             ("pa_qwen_joint", "llm:port=8002,mode=joint,prompt=alt", "")]
    runs += [(f"ab_{m}_{v}", f"llm:port={port},mode=joint,ablate={v}", "")
             for m, port in (("tgpt", 8001), ("qwen", 8002)) for v in ("zeroshot", "history", "domain", "cot")]
    rng = random.Random(2027)
    blocks = []
    for d in (0, 2, 4, 1, 3):
        r = runs[:]
        rng.shuffle(r)
        blocks.append({"day": d, "layout": "AB"[d % 2],
                       "runs": [{"name": f"{n}_t{d}", "controller": s, "args": a} for n, s, a in r]})
    p.write_text(json.dumps(blocks, indent=1))
PYEOF
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 $4 < /dev/null   # ssh in the harness must not read the run list
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as in the main matrix
$PY -c "
import json
import os
for b in json.load(open('results/test_order_after.json')):
    if all(os.path.exists('results/runs/' + r['name'] + '/summary.json') for r in b['runs']): continue   # done: no layout switch
    print('block', b['day'], b['layout'], '-')
    for r in b['runs']: print('run', r['controller'], r['name'], r['args'] or '-')" |
while read kind a b c; do
  [ "$c" = - ] && c=""
  if [ $kind = block ]; then
    d=$a
    if [ "$(cut -d' ' -f1 runs/llm_layout.txt 2>/dev/null)" != "$b" ]; then
      echo "$(date -u +%T) layout $b"; harness/llm_containers.sh $b < /dev/null || exit 1
    fi
    harness/llm_serve.sh both < /dev/null || exit 1
    run $REF ref3_t$d data/traces/interference_50pct.csv.gz "--arrivals even --seed 1" || exit 1
  else
    run $a $b data/traces/runs/test_$d.csv.gz "--seed 1 $c" || exit 1
  fi
done || exit 1
echo "after_matrix done $(date -u +%FT%TZ)"
