#!/bin/bash
# Calibration of the 4-service design (2026-10-01, docs/decisions.md), on the current VM layout:
#  1. thumbnailer (800 px photos) and graph-bfs ("small"), one copy, Poisson sweeps
#  2. all four apps together vs alone, then two together-only repeats at finer steps
#  3. scale-up timing of the four scaled services
#  4. shared base run (SLO base): one warm-up run, then the measured one
# TeaStore and hotel sweeps from 2026-09-30 (results/calibration/p2) still apply to this layout.
# Hotel's fixed tiers (frontend, search, rate) stay at their configured 2 copies throughout.
set -uo pipefail
cd "$(dirname "$0")/../.."
export CAL_DIR=results/calibration/p2
R=tools/calibration/run.sh
PY=.venv/bin/python
echo "start $(date -u +%FT%TZ)"
clean() { sleep 5; $PY -c "import sys; sys.path.insert(0,'harness'); import registry_cleaner as r; print('consul: removed', r.clean())"; }
one() {
  for d in frontend search rate; do kubectl -n hotel scale deploy/$d --replicas=2 >/dev/null; done
  kubectl -n hotel scale deploy/reservation --replicas=1 >/dev/null
  kubectl -n teastore scale deploy/teastore-webui --replicas=1 >/dev/null
  for d in thumbnailer graph-bfs; do kubectl -n sebs scale deploy/$d --replicas=1 >/dev/null; done
  for d in frontend search rate reservation; do kubectl -n hotel rollout status deploy/$d --timeout=300s >/dev/null; done
  clean
}
one
ARRIVALS=poisson $R thumbnailer 1 20,40,60,70,80,90 $CAL_DIR
ARRIVALS=poisson $R graph-bfs 1 10,15,20,25,30,35 $CAL_DIR
tools/calibration/together.sh
FRACS="0.6 0.7 0.8 0.9" ALONE=0 tools/calibration/together.sh
FRACS="0.6 0.7 0.8 0.9" ALONE=0 tools/calibration/together.sh
tools/calibration/scaleup.sh
one
S="static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5"
for n in warm_newdesign fp_newdesign; do
  [ -f results/runs/${n}_20261001/summary.json ] || $PY harness/run.py --controller $S --trace data/traces/runs/dev_0.csv.gz \
    --name ${n}_20261001 --duration 900 --arrivals even < /dev/null
done
echo "end $(date -u +%FT%TZ)"
