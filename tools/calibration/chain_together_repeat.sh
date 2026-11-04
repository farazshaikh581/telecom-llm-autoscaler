#!/bin/bash
# Second testbed (2026-09-30): the together test's single 60 s steps near the per-copy
# capacities were noisy (thumbnailer 136 ms at 7.5 req/s, 618 ms at 10). Two repeats of the
# together part at finer steps around the capacities, alone part skipped.
set -uo pipefail
cd "$(dirname "$0")/../.."
WAIT_PID=$([ -n "${1:-}" ] && cat "$1" 2>/dev/null || true)
while [ -n "$WAIT_PID" ] && kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
export CAL_DIR=results/calibration/p2 FRACS="0.6 0.7 0.8 0.9" ALONE=0
for r in 1 2; do tools/calibration/together.sh; done
echo "end $(date -u +%FT%TZ)"
