#!/bin/bash
# Main test matrix (docs/analysis_plan.md): 12 controllers x 5 held-out test days x 2 repeats.
# A block is one (test day, repeat): a drift reference run (fixed copies, evenly spaced arrivals,
# 15 min), then every controller once in a random order. The repeat sets the load generator seed
# (repeat 1: seed 1, repeat 2: seed 2), the same for every controller in the block.
# GPU layout: each test day has one block in layout A and one in B; blocks are ordered so the
# layout changes every second block (5 container switches, between runs only).
# The picks (tuned baselines, our agent after the gate) and the order are written once
# (results/test_picks_v2.json, results/test_order_v2.json) so a restart keeps them; finished runs
# are skipped. About 175 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh test_matrix_v2 harness/chains/test_matrix_v2.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
$PY - <<'PYEOF' || exit 1
import json, pathlib, random, subprocess, sys
picks_p, order_p = pathlib.Path("results/test_picks_v2.json"), pathlib.Path("results/test_order_v2.json")
def pick(prefix):
    return subprocess.run([sys.executable, "tools/tuning_table.py", prefix, "--pick"], check=True,
                          capture_output=True, text=True).stdout.strip()
def viol(name):
    return json.load(open(f"results/runs/{name}/summary.json"))["overall"]["violation_rate_k3"]
if not picks_p.exists():
    C = "rl/checkpoints"
    hpa = pick("tune4_hpa_")
    t = hpa.split("target=")[1]
    phpa = pick("tune4_phpa_la" if t == "70" else f"tune4_phpa_t{t}_")
    a = open(f"{C}/ours_pick.txt").read().strip().removeprefix("ours_")
    pre = sum(viol(f"finetune4_ours_dev{d}") for d in range(3)) / 3
    scr = sum(viol(f"scratch4_ours_dev{d}") for d in range(3)) / 3
    ours = f"{C}/ours_{a}_ft" if pre < scr else f"{C}/ours_{a}_scratch_dev2"   # gate (decisions.md 2026-09-24)
    ctrls = {"hpa70": "hpa:target=70", "keda": pick("tune4_keda_"), "phpa": phpa,
             "aware": f"aware:ckpt={C}/aware_ft.pt", "firm": f"firm:ckpt={C}/firm_ft.pt", "ours": f"ours:ckpt={ours}"}
    if t != "70":
        ctrls["hpa_tuned"] = hpa
    for m, port in (("tgpt", 8001), ("qwen", 8002)):
        for mode in ("joint", "per-service", "oracl"):
            ctrls[f"{m}_{mode.replace('-', '')}"] = f"llm:port={port},mode={mode}"
    picks_p.write_text(json.dumps({"controllers": ctrls, "gate": {"pretrained": pre, "scratch": scr,
                                   "used": "pretrained" if pre < scr else "scratch"}}, indent=1))
if not order_p.exists():
    ctrls = json.load(open(picks_p))["controllers"]
    # repeat 1 in layout A on even days, B on odd days, repeat 2 the other one: A B | B A | A B | B A | A B
    blocks = [{"day": d, "repeat": r, "layout": "AB"[(d + r - 1) % 2]} for d in range(5) for r in (1, 2)]
    rng = random.Random(2026)
    for b in blocks:
        names = list(ctrls)
        rng.shuffle(names)
        b["runs"] = [{"name": f"test2_{c}_t{b['day']}_r{b['repeat']}", "controller": ctrls[c]} for c in names]
    order_p.write_text(json.dumps(blocks, indent=1))
print(json.dumps(json.load(open(picks_p)), indent=1))
PYEOF
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 $4 < /dev/null   # ssh in the harness must not read the run list
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as the interference check
$PY -c "
import json
import os
for b in json.load(open('results/test_order_v2.json')):
    if all(os.path.exists('results/runs/' + r['name'] + '/summary.json') for r in b['runs']): continue   # done: no layout switch
    print('block', b['day'], b['repeat'], b['layout'])
    for r in b['runs']: print('run', r['controller'], r['name'])" |
while read kind a b c; do
  if [ $kind = block ]; then
    d=$a; r=$b
    if [ "$(cut -d' ' -f1 runs/llm_layout.txt 2>/dev/null)" != "$c" ]; then
      echo "$(date -u +%T) layout $c"; harness/llm_containers.sh $c < /dev/null || exit 1
    fi
    harness/llm_serve.sh both < /dev/null || exit 1
    run $REF ref2_t${d}_r$r data/traces/interference_50pct.csv.gz "--arrivals even --seed $r" || exit 1
  else
    run $a $b data/traces/runs/test_$d.csv.gz "--seed $r" || exit 1
  fi
done || exit 1
echo "test_matrix_v2 done $(date -u +%FT%TZ)"
