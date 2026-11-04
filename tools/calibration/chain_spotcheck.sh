#!/bin/bash
# Spot check after the host swap fix (2026-09-25, docs/cluster.md): repeat three Poisson
# baselines at one copy and their original rates, to confirm calibration still holds.
# Waits for a running job (PID file given as $1) so the control node is as idle as in calibration.
# Baselines: hotel_rkeep_20260924T205715Z_poisson, imgrec_r1_20260924T195344Z_poisson,
# teastore_rkeep_20260924T204218Z_poisson.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/../../results/calibration/spotcheck_20260925
WAIT_PID=$([ -n "${1:-}" ] && cat "$1" 2>/dev/null || true)
while [ -n "$WAIT_PID" ] && kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
echo "start $(date -u +%FT%TZ)"
ARRIVALS=poisson "$HERE/run.sh" hotel keep 100,300,500 "$OUT"
ARRIVALS=poisson "$HERE/run.sh" imgrec 1 4,6,7 "$OUT"
ARRIVALS=poisson "$HERE/run.sh" teastore keep 50,100,150 "$OUT"
echo "end $(date -u +%FT%TZ)"
