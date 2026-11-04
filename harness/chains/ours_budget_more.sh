#!/bin/bash
# Budget-aware variant of our agent (exploratory, docs/decisions.md 2026-10-22): the gate's winner
# (ours_budget_pick + scratch_dev2, as in ours_budget_cluster.sh) on the 5 test days again, to match the
# main agent's runs: repeat 2 (seed 2, pairs with the main matrix's repeat 2) and +25% load (seed 1,
# pairs with the hl_ runs). A drift reference first. Greedy, no learning. About 13 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh ours_budget_more harness/chains/ours_budget_more.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
grep -q "^ours_budget_cluster done" runs/ours_budget_cluster.log || { echo "ours_budget_cluster not finished: not starting"; exit 1; }
C=rl/checkpoints
A=$(cat $C/ours_budget_pick.txt); A=${A#ours_}
USE=$(grep "^gate:" runs/ours_budget_cluster.log | cut -d' ' -f2)
case $USE in pretrained) CK=$C/ours_${A}_ft_dev2 ;; scratch) CK=$C/ours_${A}_scratch_dev2 ;; *) echo "no gate result"; exit 1 ;; esac
echo "checkpoint: $CK"
run() {  # spec, name, trace, extra args
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 ${4:-} < /dev/null
}
REF=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5   # as in the main matrix
run $REF ref4_xb data/traces/interference_50pct.csv.gz "--arrivals even --seed 2" || exit 1
for d in 0 1 2 3 4; do
  run ours:ckpt=$CK xb_ours_t${d}_r2 data/traces/runs/test_$d.csv.gz "--seed 2" || exit 1
  run ours:ckpt=$CK xb_hl_t$d data/traces/runs/test_$d.csv.gz "--seed 1 --scale 1.25" || exit 1
done
echo "ours_budget_more done $(date -u +%FT%TZ)"
