#!/bin/bash
# Scale-up timing: for each scaled service, add one copy (1 -> 2), time how long
# the new copy takes to become ready (Kubernetes timestamps), then send 15
# sequential requests straight to it to see its warm-up (HTTP services only; the
# hotel's search, reservation, and rate speak gRPC). Scale back to 1. 3 repeats.
# Output: results/calibration/scaleup_<ts>/scaleup.csv (no addresses recorded).
set -uo pipefail
cd "$(dirname "$0")/../.."
OUT=${CAL_DIR:-results/calibration}/scaleup_$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"
CSV=$OUT/scaleup.csv
echo "service,rep,node,image_pulled,t_scale,created_s,running_s,ready_s,first_req_ms,req2_ms,req5_ms,req15_ms" > $CSV

B=sebs-benchmarks
body() {
  case $1 in
    thumbnailer) echo '{"object":{"key":"800px-Porsche_991_silver_IAA.jpg","width":200,"height":200},"bucket":{"bucket":"'$B'","input":"411.image-recognition-1-input","output":"210.thumbnailer-0-output"}}' ;;
    graph-bfs)   echo '{"size":10000,"seed":42}' ;;
  esac
}
request() {  # svc ip -> latency ms (or -1)
  local s=$1 ip=$2 t
  case $s in
    thumbnailer|graph-bfs)
      t=$(curl -s -o /dev/null -m 60 -w '%{time_total}' -X POST -H 'Content-Type: application/json' --data "$(body $s)" "http://$ip:8080/") ;;
    teastore-webui) t=$(curl -s -o /dev/null -m 60 -w '%{time_total}' "http://$ip:8080/tools.descartes.teastore.webui/category?category=2&page=1") ;;
    frontend) t=$(curl -s -o /dev/null -m 60 -w '%{time_total}' "http://$ip:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=38.0235&lon=-122.095") ;;
    *) echo -1; return ;;
  esac
  python3 -c "print(round(float('$t')*1000,1))"
}

ALL="sebs/thumbnailer sebs/graph-bfs teastore/teastore-webui hotel/reservation"   # the scaled services (2026-10-01)
for entry in ${ONLY:-$ALL}; do
  ns=${entry%/*}; d=${entry#*/}
  sel=$(kubectl -n $ns get deploy $d -o jsonpath='{.spec.selector.matchLabels}' | python3 -c 'import sys,json; print(",".join(f"{k}={v}" for k,v in json.load(sys.stdin).items()))')
  for rep in $(seq 1 ${REPS:-3}); do
    kubectl -n $ns scale deploy/$d --replicas=1 >/dev/null; kubectl -n $ns rollout status deploy/$d --timeout=300s >/dev/null
    old=$(kubectl -n $ns get pods -l "$sel" -o jsonpath='{.items[*].metadata.name}')
    # All times on the control node's clock (worker clocks can be seconds off), observed by
    # polling every 0.25 s: pod created, container running, pod Ready.
    t0=$(date +%s.%N)
    kubectl -n $ns scale deploy/$d --replicas=2 >/dev/null
    new=""; t_seen=""; t_run=""; t_ready=""
    for i in $(seq 1 2400); do
      if [ -z "$new" ]; then
        new=$(kubectl -n $ns get pods -l "$sel" -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -vxF -f <(echo "$old" | tr ' ' '\n') | head -1)
        [ -n "$new" ] && t_seen=$(date +%s.%N)
      fi
      if [ -n "$new" ]; then
        st=$(kubectl -n $ns get pod $new -o jsonpath='{.status.containerStatuses[0].state.running.startedAt}|{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
        [ -z "$t_run" ] && [ -n "${st%%|*}" ] && t_run=$(date +%s.%N)
        [ "${st##*|}" = True ] && { t_ready=$(date +%s.%N); break; }
      fi
      sleep 0.25
    done
    j=$(kubectl -n $ns get pod $new -o json)
    node=$(echo "$j" | python3 -c 'import sys,json; print(json.load(sys.stdin)["spec"]["nodeName"])' | sed -f <(tools/calibration/roles.sh))
    pulled=$(kubectl -n $ns get events --field-selector involvedObject.name=$new,reason=Pulling --no-headers 2>/dev/null | grep -c . )
    rel() { python3 -c "print(round($1 - $t0, 2))"; }
    sch=$(rel $t_seen); st=$(rel $t_run); rd=$(rel $t_ready)
    ip=$(echo "$j" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"].get("podIP",""))')
    lat=(); for k in $(seq 1 15); do lat+=("$(request $d $ip)"); done
    echo "$d,$rep,$node,$([ "$pulled" -gt 0 ] && echo yes || echo no),$t0,$sch,$st,$rd,${lat[0]},${lat[1]},${lat[4]},${lat[14]}" | tee -a $CSV
    kubectl -n $ns scale deploy/$d --replicas=1 >/dev/null
    kubectl -n $ns wait --for=delete pod/$new --timeout=120s >/dev/null 2>&1
  done
done
echo "done: $CSV"
