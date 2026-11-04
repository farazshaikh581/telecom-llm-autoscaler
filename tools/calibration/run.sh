#!/bin/bash
# Run one calibration: optionally scale the service, run calib.js, sample the CPU of every
# pod in the service's namespace every 15 s (finds the bottleneck, and checks storage),
# then analyze.
# Usage: run.sh <service> <replicas|keep> <rates comma list> [out dir]
#   service: thumbnailer | graph-bfs | imgrec | teastore | hotel
#   replicas: a number scales the scaled deployment first; "keep" leaves the app as deployed
set -euo pipefail
SVC=$1; REPLICAS=$2; RATES=$3
OUT=${4:-$(dirname "$0")/../../results/calibration}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"

case $SVC in
  thumbnailer|graph-bfs|imgrec)   NS=sebs;     KSVC=$SVC;          PORT=8080; DEPLOY=$SVC ;;
  teastore)                       NS=teastore; KSVC=teastore-webui; PORT=8080; DEPLOY=teastore-webui ;;
  hotel)                          NS=hotel;    KSVC=frontend;       PORT=5000; DEPLOY=frontend
                                  SCALE_ALSO="search reservation rate" ;;
  *) echo "unknown service $SVC"; exit 1 ;;
esac
TAG="${SVC}_r${REPLICAS}_$(date -u +%Y%m%dT%H%M%SZ)"

# PLACE (optional, placement test): "same:<node>" pins all copies to one worker,
# "spread" forces one copy per worker. Reset afterwards by re-applying the manifests.
case ${PLACE:-} in
  same:*) kubectl -n $NS patch deploy/$DEPLOY --type=merge -p \
            "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"kubernetes.io/hostname\":\"${PLACE#same:}\"}}}}}" >/dev/null
          TAG="${TAG}_same" ;;
  spread) kubectl -n $NS patch deploy/$DEPLOY --type=merge -p \
            "{\"spec\":{\"template\":{\"spec\":{\"topologySpreadConstraints\":[{\"maxSkew\":1,\"topologyKey\":\"kubernetes.io/hostname\",\"whenUnsatisfiable\":\"DoNotSchedule\",\"labelSelector\":{\"matchLabels\":{\"app\":\"$DEPLOY\"}}}]}}}}" >/dev/null
          TAG="${TAG}_spread" ;;
esac
[ "${ARRIVALS:-even}" = poisson ] && TAG="${TAG}_poisson"
if [ "$REPLICAS" != keep ]; then
  # hotel: all four scaled services move together (frontend, search, reservation, rate)
  for d in $DEPLOY ${SCALE_ALSO:-}; do
    kubectl -n $NS scale deploy/$d --replicas="$REPLICAS" >/dev/null
  done
  for d in $DEPLOY ${SCALE_ALSO:-}; do
    kubectl -n $NS rollout status deploy/$d --timeout=300s >/dev/null
  done
fi
kubectl -n $NS get pods -o custom-columns=POD:.metadata.name,NODE:.spec.nodeName,READY:.status.phase --no-headers 2>/dev/null \
  | sed -f <("$HERE/roles.sh") > "$OUT/$TAG.placement.txt" || true   # node names replaced by roles (w0..w3, control)
IP=$(kubectl -n $NS get svc $KSVC -o jsonpath='{.spec.clusterIP}')

( while true; do
    kubectl top pod -n $NS --no-headers 2>/dev/null | awk -v t="$(date -u +%s)" '{print t, $1, $2, $3}'
    kubectl top pod -n sebs --no-headers 2>/dev/null | awk -v t="$(date -u +%s)" '$1 ~ /^minio/ {print t, $1, $2, $3}'
    sleep 15
  done ) > "$OUT/$TAG.podcpu.txt" &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

N=$([ "$REPLICAS" = keep ] && echo 1 || echo "$REPLICAS")
# --system-tags leaves out url and name (they hold the cluster IP); sanitize.py masks the rest.
SERVICE=$SVC URL="http://$IP:$PORT/" RATES=$RATES STEP_S=${STEP_S:-60} GAP_S=${GAP_S:-20} REPLICAS=$N \
  ARRIVALS=${ARRIVALS:-even} ~/.local/bin/k6 run --quiet --no-usage-report \
  --system-tags=status,method,scenario,expected_response,error,error_code \
  --out "csv=$OUT/$TAG.csv" "$HERE/calib.js" > "$OUT/$TAG.k6.txt" 2>&1
python3 "$HERE/sanitize.py" "$OUT/$TAG.csv" "$OUT/$TAG.k6.txt" >/dev/null
python3 "$HERE/analyze.py" "$OUT/$TAG.csv.gz" 10 "${STEP_S:-60}" | tee "$OUT/$TAG.table.txt"
