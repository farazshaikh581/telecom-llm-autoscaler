#!/bin/bash
# Budget-aware variant of our agent on the cluster (exploratory, docs/decisions.md 2026-10-15), after
# every planned run: the gate as for the main agent (pretrained vs from scratch, 3 fine-tuning runs each
# on dev_0-2, interleaved), the gate's winner on the 5 test days (seed 1, as the main matrix's
# repeat 1; greedy, no learning). About 15 h.
# Usage: [WAIT="pidfile ..."] tools/detach.sh ours_budget_cluster harness/chains/ours_budget_cluster.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
grep -q "^after_matrix done" runs/after_matrix.log || { echo "after_matrix not finished: not starting"; exit 1; }
grep -q "^train_budget done" runs/train_budget.log || { echo "budget training not finished: not starting"; exit 1; }
C=rl/checkpoints
A=$(cat $C/ours_budget_pick.txt); A=${A#ours_}                 # budget_ddqn_s<n>
echo "picked in the simulator: $A"
[ -f $C/ours_${A}_scratch.zip ] || (cd rl && ../$PY train_ours.py --algo ddqn --budget --steps 0 --out checkpoints/ours_${A}_scratch) || exit 1
cp -n $C/ours_${A}_vecnorm.pkl $C/ours_${A}_scratch_vecnorm.pkl
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 ${4:-} < /dev/null
}
pre=$C/ours_$A; scr=$C/ours_${A}_scratch
for d in 0 1 2; do
  run ours:ckpt=$pre,learn=1,save=$C/ours_${A}_ft_dev$d xb_finetune_dev$d dev_$d || exit 1
  pre=$C/ours_${A}_ft_dev$d
  run ours:ckpt=$scr,learn=1,scratch=225,save=$C/ours_${A}_scratch_dev$d xb_scratch_dev$d dev_$d || exit 1
  scr=$C/ours_${A}_scratch_dev$d
done
USE=$($PY -c "
import json
v = lambda n: json.load(open(f'results/runs/{n}/summary.json'))['overall']['violation_rate_k3']
pre = sum(v(f'xb_finetune_dev{d}') for d in range(3)) / 3; scr = sum(v(f'xb_scratch_dev{d}') for d in range(3)) / 3
print('pretrained' if pre < scr else 'scratch', pre, scr)")
echo "gate: $USE"
case $USE in pretrained*) CK=$C/ours_${A}_ft_dev2 ;; *) CK=$C/ours_${A}_scratch_dev2 ;; esac
for d in 0 1 2 3 4; do run ours:ckpt=$CK xb_ours_t$d test_$d "--seed 1" || exit 1; done
echo "ours_budget_cluster done $(date -u +%FT%TZ)"
