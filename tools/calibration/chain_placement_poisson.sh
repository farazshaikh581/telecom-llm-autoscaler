#!/bin/bash
# Calibration batch 3 (2026-09-24):
#  A. Placement: 2 copies on the same worker vs one per worker (thumbnailer, imgrec).
#  B. Poisson arrivals, 1 copy (all three functions): realistic capacity per copy.
#  C. TeaStore at higher load (it did not saturate at 150 req/s).
set -uo pipefail
cd "$(dirname "$0")/../.."
R=tools/calibration/run.sh
SAME=worker13

reset() {  # remove placement patches, back to 1 copy
  kubectl -n sebs patch deploy/$1 --type=json \
    -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector/kubernetes.io~1hostname"}]' >/dev/null 2>&1
  kubectl -n sebs patch deploy/$1 --type=json \
    -p='[{"op":"remove","path":"/spec/template/spec/topologySpreadConstraints"}]' >/dev/null 2>&1
  kubectl -n sebs scale deploy/$1 --replicas=1 >/dev/null
  kubectl -n sebs rollout status deploy/$1 --timeout=300s >/dev/null
}

PLACE=same:$SAME $R thumbnailer 2 10,20,25,30;   reset thumbnailer
PLACE=spread      $R thumbnailer 2 10,20,25,30;   reset thumbnailer
PLACE=same:$SAME $R imgrec 2 4,8,12,14,16;       reset imgrec
PLACE=spread      $R imgrec 2 4,8,12,14,16;       reset imgrec

ARRIVALS=poisson $R thumbnailer 1 5,10,12.5,15,17.5,20
ARRIVALS=poisson $R compression 1 1,1.5,2,2.25
ARRIVALS=poisson $R imgrec 1 4,6,7,8,9

$R teastore keep 200,300,400,500,600
echo "batch done"
