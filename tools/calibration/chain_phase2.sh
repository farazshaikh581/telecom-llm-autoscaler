#!/bin/bash
# Calibration on the second testbed (2026-09-30), redo list step 3 (docs/testbed_4w.md).
# Repeats the phase-1 measurements that the trace scaling and the simulator use, at the same
# rates (the offered rates of the phase-1 sweeps), into results/calibration/p2/:
#  1. one copy, Poisson arrivals, every entry (per-copy capacity)
#  2. TeaStore and hotel with 2 and 3 copies (latency curve, sim/params.py)
#  3. all entries together vs alone (together.sh)
#  4. scale-up timing (scaleup.sh)
# The harness's registry cleaner does not run here, so consul is cleaned after every change of
# copies (after the rollout, when every pod shows its address).
# Waits for a running job first (PID file as $1).
set -uo pipefail
cd "$(dirname "$0")/../.."
export CAL_DIR=results/calibration/p2
mkdir -p $CAL_DIR
R=tools/calibration/run.sh
WAIT_PID=$([ -n "${1:-}" ] && cat "$1" 2>/dev/null || true)
while [ -n "$WAIT_PID" ] && kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
echo "start $(date -u +%FT%TZ)"

clean() { sleep 5; .venv/bin/python -c "import sys; sys.path.insert(0,'harness'); import registry_cleaner as r; print('consul: removed', r.clean())"; }
one() {  # every scaled service back to one copy
  kubectl -n teastore scale deploy/teastore-webui --replicas=1 >/dev/null
  for d in thumbnailer compression imgrec; do kubectl -n sebs scale deploy/$d --replicas=1 >/dev/null; done
  for d in frontend search reservation rate; do kubectl -n hotel scale deploy/$d --replicas=1 >/dev/null; done
  for d in frontend search reservation rate; do kubectl -n hotel rollout status deploy/$d --timeout=300s >/dev/null; done
  clean
}

one
ARRIVALS=poisson $R teastore 1 50,100,150,200,250,300 $CAL_DIR
ARRIVALS=poisson $R hotel 1 100,200,300,400,500,600 $CAL_DIR; clean
ARRIVALS=poisson $R thumbnailer 1 5,10,12.5,15,17.5,20 $CAL_DIR
ARRIVALS=poisson $R compression 1 1,1.5,2,2.25 $CAL_DIR
ARRIVALS=poisson $R imgrec 1 4,6,7,8,9 $CAL_DIR

ARRIVALS=poisson $R teastore 2 200,250,300,350,400 $CAL_DIR
ARRIVALS=poisson $R teastore 3 300,400,450,500,550 $CAL_DIR
kubectl -n hotel scale deploy frontend search reservation rate --replicas=2 >/dev/null
for d in frontend search reservation rate; do kubectl -n hotel rollout status deploy/$d --timeout=300s >/dev/null; done; clean
ARRIVALS=poisson OVERSAMPLE=4 $R hotel 2 500,700,900,1000,1100 $CAL_DIR
kubectl -n hotel scale deploy frontend search reservation rate --replicas=3 >/dev/null
for d in frontend search reservation rate; do kubectl -n hotel rollout status deploy/$d --timeout=300s >/dev/null; done; clean
ARRIVALS=poisson OVERSAMPLE=4 $R hotel 3 900,1200,1400,1600 $CAL_DIR
one

tools/calibration/together.sh
tools/calibration/scaleup.sh
one
echo "end $(date -u +%FT%TZ)"
