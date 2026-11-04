#!/bin/bash
# Interference test: all five entries loaded at once vs each alone, same rates.
# Env: FRACS (load steps as fractions of CAP), ALONE=0 (together only), CAL_DIR (output dir).
# Rates = fractions of each entry's one-copy capacity with random arrivals
# (docs/calibration.md). Poisson arrivals, one copy of every scaled service.
# Output: results/calibration/together_<ts>/{together,alone}_<svc>.*
set -uo pipefail
cd "$(dirname "$0")/../.."
HERE=tools/calibration
OUT=${CAL_DIR:-results/calibration}/together_$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"
read -ra FRACS <<< "${FRACS:-0.3 0.6 0.8}"   # fractions of CAP; override e.g. FRACS="0.6 0.7 0.8 0.9"
# one-copy capacity with random arrivals; thumbnailer (800 px photos) and graph-bfs: first estimates from
# one-copy timings (2026-10-01), refined by their r1 sweeps
declare -A CAP=([teastore]=150 [thumbnailer]=${CAP_THUMB:-60} [graph-bfs]=${CAP_BFS:-28} [hotel]=500 [imgrec]=${CAP_IMGREC:-5})
declare -A NS=([teastore]=teastore [thumbnailer]=sebs [graph-bfs]=sebs [hotel]=hotel [imgrec]=sebs)
declare -A KSVC=([teastore]=teastore-webui [thumbnailer]=thumbnailer [graph-bfs]=graph-bfs [hotel]=frontend [imgrec]=imgrec)
declare -A PORT=([teastore]=8080 [thumbnailer]=8080 [graph-bfs]=8080 [hotel]=5000 [imgrec]=8080)
SVCS=${SVCS:-"teastore thumbnailer graph-bfs hotel"}   # new-service shift (2026-10-28): SVCS adds imgrec

rates() { python3 -c "import sys; print(','.join(str(round($1*float(f),3)) for f in sys.argv[1:]))" "${FRACS[@]}"; }

k6run() {  # svc label
  local s=$1 label=$2
  local ip; ip=$(kubectl -n ${NS[$s]} get svc ${KSVC[$s]} -o jsonpath='{.spec.clusterIP}')
  SERVICE=$s URL="http://$ip:${PORT[$s]}/" RATES=$(rates ${CAP[$s]}) STEP_S=60 GAP_S=20 REPLICAS=1 ARRIVALS=poisson \
    ~/.local/bin/k6 run --quiet --no-usage-report \
    --system-tags=status,method,scenario,expected_response,error,error_code \
    --out "csv=$OUT/${label}_$s.csv" "$HERE/calib.js" > "$OUT/${label}_$s.k6.txt" 2>&1
  python3 "$HERE/sanitize.py" "$OUT/${label}_$s.csv" "$OUT/${label}_$s.k6.txt" >/dev/null
  python3 "$HERE/analyze.py" "$OUT/${label}_$s.csv.gz" 10 60 > "$OUT/${label}_$s.table.txt"
}

sample() {  # CPU of every pod in the three namespaces, every 15 s
  while true; do
    for n in teastore sebs hotel; do
      kubectl top pod -n $n --no-headers 2>/dev/null | awk -v t="$(date -u +%s)" -v n=$n '{print t, n, $1, $2, $3}'
    done
    sleep 15
  done
}

# 1. together
sample > "$OUT/together.podcpu.txt" & S=$!
for s in $SVCS; do k6run $s together & done
wait $(jobs -p | grep -v "^$S$") 2>/dev/null
kill $S 2>/dev/null

# 2. each alone, same schedule (ALONE=0 skips it: repeats of the together part only)
[ "${ALONE:-1}" = 0 ] && SVCS_ALONE="" || SVCS_ALONE=$SVCS
for s in $SVCS_ALONE; do
  sample > "$OUT/alone_$s.podcpu.txt" & S=$!
  k6run $s alone
  kill $S 2>/dev/null
done

# 3. side by side: P95 per step, together vs alone
for s in $SVCS_ALONE; do
  echo "== $s (rates $(rates ${CAP[$s]}) req/s)"
  paste <(awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1, $2, $9, $4}' "$OUT/alone_$s.table.txt") \
        <(awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $9, $4}' "$OUT/together_$s.table.txt") |
    awk 'BEGIN{print "   step  rate   P95_alone  err_alone  P95_together  err_together"} {printf "   %4s %6s %10s %10s %13s %13s\n", $1, $2, $3, $4, $5, $6}'
done | tee "$OUT/compare.txt"
