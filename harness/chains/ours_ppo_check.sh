#!/bin/bash
# Our agent: does the simulator's pick (PPO, 9.4% vs QR-DQN 12.6% on the development days) hold on
# the cluster? Both as trained in the simulator, greedy, no learning, on the 3 development days,
# interleaved so drifts hit both alike. About 7.5 h.
# Usage: [PREFIX=3] tools/detach.sh ours_ppo_check harness/chains/ours_ppo_check.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
X=${PREFIX:-}
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 < /dev/null
}
for d in 0 1 2; do
  for a in ppo qrdqn; do
    run ours:ckpt=rl/checkpoints/ours_$a check${X}_ours_${a}_dev$d dev_$d || exit 1
  done
done
echo "ours_ppo_check done $(date -u +%FT%TZ)"
