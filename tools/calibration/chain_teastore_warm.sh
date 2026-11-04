#!/bin/bash
# Second testbed (2026-09-30): TeaStore's 2- and 3-copy sweeps in chain_phase2.sh started on
# cold copies (Java JIT): P95 fell as the sweep went on (3 copies: 1930 ms at 300 req/s, 374 at
# 500). Each is repeated after 5 min of moderate load on the same copies; the warm-up sweep is
# kept apart (p2/warmup/) and not used.
set -uo pipefail
cd "$(dirname "$0")/../.."
R=tools/calibration/run.sh
CAL_DIR=results/calibration/p2
WAIT_PID=$([ -n "${1:-}" ] && cat "$1" 2>/dev/null || true)
while [ -n "$WAIT_PID" ] && kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
mkdir -p $CAL_DIR/warmup
ARRIVALS=poisson GAP_S=0 $R teastore 2 150,150,150,150,150 $CAL_DIR/warmup
ARRIVALS=poisson $R teastore 2 200,250,300,350,400 $CAL_DIR
ARRIVALS=poisson GAP_S=0 $R teastore 3 250,250,250,250,250 $CAL_DIR/warmup
ARRIVALS=poisson $R teastore 3 300,400,450,500,550 $CAL_DIR
kubectl -n teastore scale deploy/teastore-webui --replicas=1 >/dev/null
echo "end $(date -u +%FT%TZ)"
