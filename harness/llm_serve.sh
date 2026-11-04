#!/bin/bash
# Make sure the local LLM(s) are served. Since 2026-10-06 each model has its own GPU and both are
# served at the same time (harness/llm_containers.sh creates the containers with identical settings);
# nothing is stopped. Before that, one GPU held one 27B model at a time and this stopped the others.
# Usage: harness/llm_serve.sh both | telecomgpt-r1 (port 8001) | qwen35-27b (port 8002)
set -euo pipefail
case $1 in
  both) WANT="telecomgpt-r1 qwen35-27b" ;;
  *) WANT=$1 ;;
esac
WAIT_S=${WAIT_S:-3600}   # a first load from the NAS disk can take about 35 min
for c in $WANT; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || { docker start "$c" >/dev/null; echo "starting $c"; }
done
for c in $WANT; do
  PORT=$(docker port "$c" 8000/tcp | head -1 | sed 's/.*://')
  ok=0
  for i in $(seq 1 $((WAIT_S / 5))); do
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; then echo "$c ready on port $PORT"; ok=1; break; fi
    sleep 5
  done
  [ $ok = 1 ] || { echo "$c not ready after $WAIT_S s" >&2; exit 1; }
done
