#!/bin/bash
# New-service shift (docs/decisions.md 2026-10-28): SLO base of image recognition. The drift reference (fixed
# copies 3/3/3/5, every entry at 50% of its per-copy capacity, evenly spaced arrivals, 15 min) plus image
# recognition at 3 copies and 4.5 req/s (50% of 3.0 per copy). One warm-up run, then the measured one.
# A first try on dev_0 (warm_imgrec/fp_imgrec_20261028) is not a base: dev_0's warm-up has queued thumbnailer
# at up to 190 req/s since the trace rebuild of 2026-10-06.
set -uo pipefail
cd "$(dirname "$0")/../.."
export HARNESS_CONFIG=config/harness_imgrec.yaml SCALING_CONFIG=config/trace_scaling_imgrec.yaml
S=static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5,image-recognition=3
for n in warm_imgrec50_20261028 fp_imgrec50_20261028; do
  [ -f results/runs/$n/summary.json ] || .venv/bin/python harness/run.py --controller $S \
    --trace data/traces/interference_50pct_imgrec.csv.gz --name $n --duration 900 --arrivals even < /dev/null || exit 1
done
echo "imgrec_base done $(date -u +%FT%TZ)"
