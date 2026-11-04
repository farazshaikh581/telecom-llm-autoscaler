#!/bin/bash
# Simulator training with 3 seeds per agent (2026-10-06, docs/decisions.md): AWARE, FIRM and our
# agent's variants (DQN, Double DQN, QR-DQN), same budgets as before. All jobs run at once, one core
# each, at low priority and pinned to the control VM's vCPUs on the socket without the workers
# (CPUS), so they cannot share cache or memory bandwidth with the workers during cluster runs.
# Then rl/eval_lit.py and rl/eval_ours.py pick, by fixed rules, what goes to the cluster.
# Usage: [CPUS=24-55] tools/detach.sh train_seeds harness/chains/train_seeds.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
CPUS=${CPUS:-24-55}
PY="taskset -c $CPUS nice -n 19 $PWD/.venv/bin/python"
C=rl/checkpoints
pids=()
for s in 0 1 2; do
  for k in aware firm; do
    [ -f $C/${k}_s$s.pt ] || { (cd rl && $PY train_sim.py $k --runs $([ $k = firm ] && echo 4500 || echo 360) --seed $s --out checkpoints/${k}_s$s.pt) > runs/train_${k}_s$s.log 2>&1 & pids+=($!); }
  done
  for a in dqn ddqn qrdqn; do
    [ -f $C/ours_${a}_s$s.zip ] || { (cd rl && $PY train_ours.py --algo $a --seed $s --out checkpoints/ours_${a}_s$s) > runs/train_ours_${a}_s$s.log 2>&1 & pids+=($!); }
  done
done
fail=0
for p in "${pids[@]}"; do wait $p || fail=1; done
[ $fail = 0 ] || { echo "a training failed (runs/train_*_s*.log)"; exit 1; }
(cd rl && $PY eval_lit.py) || exit 1
(cd rl && $PY eval_ours.py $(for a in dqn ddqn qrdqn; do for s in 0 1 2; do echo -n "checkpoints/ours_${a}_s$s "; done; done)) || exit 1
echo "train_seeds done $(date -u +%FT%TZ)"
