#!/bin/bash
# Cluster fine-tuning of AWARE and FIRM (docs/decisions.md: 3 runs per RL agent, one per
# development day, learning online). Each run starts from the agent the previous run saved:
# simulator checkpoint -> dev_0 -> dev_1 -> dev_2 -> <agent>_ft.pt, the start of every test run.
# A 10-min smoke run per agent first (from the simulator checkpoint, nothing saved).
# Waits for the tuning chain so runs never overlap. About 8.5 h.
# Usage: [PREFIX=3 WAIT="pidfile ..."] tools/detach.sh finetune_lit_rl harness/chains/finetune_lit_rl.sh
# PREFIX is appended to the run names (smoke3_aware, finetune3_aware_dev0, ...); WAIT lists pid files to wait for.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
X=${PREFIX:-}
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
while [ -f runs/tune_after_smoke.pid ] && kill -0 "$(cat runs/tune_after_smoke.pid)" 2>/dev/null; do sleep 60; done
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 ${4:-} < /dev/null
}
# The training seed picked by rl/eval_lit.py (<agent>_pick.txt, median seed) when there is one.
src() { [ -f rl/checkpoints/$1_pick.txt ] && echo rl/checkpoints/$(cat rl/checkpoints/$1_pick.txt).pt || echo rl/checkpoints/$1.pt; }
for agent in aware firm; do
  run $agent:ckpt=$(src $agent) smoke${X}_$agent dev_0 "--duration 600" || exit 1
done
for agent in aware firm; do
  prev=$(src $agent)
  for d in 0 1 2; do
    out=rl/checkpoints/${agent}_ft_dev$d.pt
    run $agent:ckpt=$prev,save=$out finetune${X}_${agent}_dev$d dev_$d || exit 1
    [ -f $out ] || { echo "no checkpoint saved: $out"; exit 1; }
    prev=$out
  done
  cp $prev rl/checkpoints/${agent}_ft.pt
done
echo "finetune_lit_rl done $(date -u +%FT%TZ)"
