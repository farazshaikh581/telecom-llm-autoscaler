#!/bin/bash
# After the load level is set (2026-10-06): rebuild the run and training traces at the new
# load_scale, start the 3-seed simulator training, and confirm the level with HPA 70% on two
# development days. Waits for the pid files in WAIT (the interference check: no disk work under it).
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 30; done; done
Z=$HOME/trace_raw/huawei/requests_second.zip
ionice -c3 nice -n 19 $PY tools/traces/build_traces.py $Z --alibaba data/traces/alibaba_hotel_second.csv > runs/build_traces_s028.log 2>&1 || exit 1
ionice -c3 nice -n 19 $PY tools/traces/build_traces.py $Z --alibaba data/traces/alibaba_hotel_second.csv --train > runs/build_train_s028.log 2>&1 || exit 1
tools/detach.sh train_seeds harness/chains/train_seeds.sh
for d in 0 1; do
  [ -f results/runs/lscale_hpa_dev${d}_s028/summary.json ] || \
    $PY harness/run.py --controller hpa:target=70 --trace data/traces/runs/dev_$d.csv.gz --name lscale_hpa_dev${d}_s028 < /dev/null || exit 1
done
echo "after_load_scale done $(date -u +%FT%TZ)"
