#!/bin/bash
# Load level for the burst-free function traces (2026-10-06): HPA 70% on dev_0 at the current
# load_scale; its peak demand (copies wanted, pending included) against the copies available
# sets the new load_scale (docs/decisions.md). Waits for the pid files in WAIT first.
set -uo pipefail
cd "$(dirname "$0")/../.."
for f in ${WAIT:-}; do while [ -f $f ] && kill -0 "$(cat $f)" 2>/dev/null; do sleep 30; done; done
S=$(.venv/bin/python -c "import yaml; print(yaml.safe_load(open('config/trace_scaling.yaml'))['load_scale'])")
.venv/bin/python harness/run.py --controller hpa:target=70 --trace data/traces/runs/dev_0.csv.gz --name lscale_hpa_dev0_s${S/./} < /dev/null
echo "load_scale_pilot done $(date -u +%FT%TZ)"
