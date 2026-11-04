#!/bin/bash
# Our agent: pick the variant in the simulator, then the pretraining gate on the cluster
# (docs/decisions.md): pretrained vs from scratch, 3 fine-tuning runs each (dev_0, dev_1, dev_2),
# interleaved so drifts hit both arms alike. Each run starts from what the previous run of its
# arm saved. About 8 h after the simulator training ends.
# Usage: [PREFIX=3 WAIT="pidfile ..."] tools/detach.sh ours_finetune harness/chains/ours_finetune.sh
# PREFIX is appended to the run names (finetune3_ours_dev0, scratch3_ours_dev0); WAIT lists pid files to wait for.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
X=${PREFIX:-}
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 60; done; done
# The variant and training seed picked by rl/eval_ours.py (harness/chains/train_seeds.sh runs it).
[ -f rl/checkpoints/ours_pick.txt ] || { echo "no ours_pick.txt: run harness/chains/train_seeds.sh first"; exit 1; }
PICK=$(cat rl/checkpoints/ours_pick.txt); A=${PICK#ours_}; ALGO=${A%_s[0-9]}
echo "picked in the simulator: $A"
[ "$ALGO" = ppo ] && { echo "PPO picked: online fine-tuning not implemented for PPO; stopping for a decision"; exit 2; }
C=rl/checkpoints
[ -f $C/ours_${A}_scratch.zip ] || (cd rl && ../$PY train_ours.py --algo $ALGO --steps 0 --out checkpoints/ours_${A}_scratch) || exit 1
cp -n $C/ours_${A}_vecnorm.pkl $C/ours_${A}_scratch_vecnorm.pkl   # same normalization; only the weights differ
run() {  # skip runs that already finished, so the chain can be restarted
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace data/traces/runs/$3.csv.gz --name $2 < /dev/null
}
pre=$C/ours_$A; scr=$C/ours_${A}_scratch
for d in 0 1 2; do
  run ours:ckpt=$pre,learn=1,save=$C/ours_${A}_ft_dev$d finetune${X}_ours_dev$d dev_$d || exit 1
  pre=$C/ours_${A}_ft_dev$d
  run ours:ckpt=$scr,learn=1,scratch=225,save=$C/ours_${A}_scratch_dev$d scratch${X}_ours_dev$d dev_$d || exit 1
  scr=$C/ours_${A}_scratch_dev$d
done
for f in .zip _vecnorm.pkl _replay.pkl; do cp $pre$f $C/ours_${A}_ft$f; done
echo "ours_finetune done $(date -u +%FT%TZ)"
