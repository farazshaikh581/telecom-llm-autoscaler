#!/bin/bash
# Run a long command fully detached from the terminal and the login session
# (setsid + nohup, stdin closed). It survives closing the terminal, SSH drops, and logout.
# Writes <name>.log and <name>.pid under runs/. Stop with: kill -- -$(cat runs/<name>.pid)
# Usage: tools/detach.sh <name> <command...>
set -euo pipefail
NAME=$1; shift
DIR=$(cd "$(dirname "$0")/.." && pwd)/runs
mkdir -p "$DIR"
setsid nohup bash -c "$*; echo \"EXIT \$? at \$(date -u +%FT%TZ)\"" > "$DIR/$NAME.log" 2>&1 < /dev/null &
echo $! > "$DIR/$NAME.pid"
echo "started $NAME: pid $(cat "$DIR/$NAME.pid"), log $DIR/$NAME.log"
