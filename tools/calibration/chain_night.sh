#!/bin/bash
# Overnight calibration, 2026-09-24:
#  1. multi-copy scaling (TeaStore front end 2 and 3 copies; hotel's four scaled services 2 and 3 copies)
#  2. all entries together vs alone (interference)
#  3. scale-up timing for every scaled service
set -uo pipefail
cd "$(dirname "$0")/../.."
R=tools/calibration/run.sh

ARRIVALS=poisson $R teastore 2 150,250,300,350,400
ARRIVALS=poisson $R teastore 3 250,350,450,500,550
ARRIVALS=poisson OVERSAMPLE=4 $R hotel 2 500,700,900,1000,1100
ARRIVALS=poisson OVERSAMPLE=4 $R hotel 3 900,1200,1400,1600
kubectl -n teastore scale deploy/teastore-webui --replicas=1
for d in frontend search reservation rate; do kubectl -n hotel scale deploy/$d --replicas=1; done
for d in frontend search reservation rate; do kubectl -n hotel rollout status deploy/$d --timeout=300s; done

tools/calibration/together.sh
tools/calibration/scaleup.sh
echo "night chain done"
