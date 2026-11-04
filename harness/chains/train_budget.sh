#!/bin/bash
# Budget-aware variant of our agent (exploratory, docs/decisions.md 2026-10-15): simulator training,
# Double DQN, 3 seeds, the same budget as the main agent, pinned and at low priority on the control VM's
# vCPUs away from the workers (as harness/chains/train_seeds.sh); then rl/eval_ours.py picks the median
# seed (rl/checkpoints/ours_budget_pick.txt).
# Usage: [CPUS=24-55] tools/detach.sh train_budget harness/chains/train_budget.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
CPUS=${CPUS:-24-55}
PY="taskset -c $CPUS nice -n 19 $PWD/.venv/bin/python"
C=rl/checkpoints
pids=()
for s in 0 1 2; do
  [ -f $C/ours_budget_ddqn_s$s.zip ] || { (cd rl && $PY train_ours.py --algo ddqn --budget --seed $s --out checkpoints/ours_budget_ddqn_s$s) > runs/train_ours_budget_s$s.log 2>&1 & pids+=($!); }
done
fail=0
for p in "${pids[@]}"; do wait $p || fail=1; done
[ $fail = 0 ] || { echo "a training failed (runs/train_ours_budget_s*.log)"; exit 1; }
(cd rl && $PY eval_ours.py checkpoints/ours_budget_ddqn_s0 checkpoints/ours_budget_ddqn_s1 checkpoints/ours_budget_ddqn_s2) || exit 1
echo "train_budget done $(date -u +%FT%TZ)"
