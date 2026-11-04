#!/bin/bash
# Confirm load_scale 0.28 with HPA 70% on dev_0 and dev_1, then repeat the invalid idle run of the
# interference check (2026-10-07). Each run's health check is printed; a flagged run is reported.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
run() {
  [ -f results/runs/$2/summary.json ] && { echo "skip $2 (done)"; return 0; }
  $PY harness/run.py --controller $1 --trace $3 --name $2 ${4:-} < /dev/null
  $PY -c "import json; print('$2 health', json.load(open('results/runs/$2/summary.json')).get('health'))"
}
for d in 0 1; do run hpa:target=70 lscale_hpa_dev${d}_s028 data/traces/runs/dev_$d.csv.gz || exit 1; done
run static:teastore-webui=3,thumbnailer=3,graph-bfs=3,hotel-reservation=5 intf_idle_5 data/traces/interference_50pct.csv.gz "--arrivals even" || exit 1
echo "confirm_s028 done $(date -u +%FT%TZ)"
