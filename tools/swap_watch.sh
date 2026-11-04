#!/bin/bash
# Host swap guard (docs/cluster.md). Since 2026-10-01 host swap is OFF, so no VM is ever swapped
# during a run (user decision: fairness). This loop is the emergency brake: if the host's available
# memory drops below BRAKE_MIB it turns swap back on (swapon -a), so the host slows down instead of
# killing the biggest process (the control-node VM), and logs it: runs live at that time are invalid.
# It also still resets swap when a harness run waits for it (swap back on after a brake).
# Standing user permission 2026-09-30/10-01. Usage: tools/detach.sh swap_watch tools/swap_watch.sh
cd "$(dirname "$0")/.."
HOST=$(cat ~/.config/telecom-llm-autoscaler/host)
BRAKE_MIB=${BRAKE_MIB:-8192}
i=0
while true; do
  read avail swap_dev <<< "$(ssh -o BatchMode=yes "$HOST" 'echo $(( $(awk "/MemAvailable/ {print \$2}" /proc/meminfo) / 1024 )) $(swapon --show --noheadings | wc -l)')"
  if [ -n "$avail" ] && [ "$avail" -lt "$BRAKE_MIB" ] && [ "$swap_dev" = 0 ]; then
    ssh -o BatchMode=yes "$HOST" 'sudo -n swapon -a' \
      && echo "$(date -u +%FT%TZ) BRAKE: host MemAvailable $avail MiB < $BRAKE_MIB, swap turned ON; runs live now are invalid" \
      || echo "$(date -u +%FT%TZ) BRAKE FAILED: MemAvailable $avail MiB"
  fi
  # every 60 s: low-memory trend line, and the old reset for a run waiting on swap
  if [ $((i % 6)) = 0 ]; then
    [ $((i % 360)) = 0 ] && echo "$(date -u +%FT%TZ) host MemAvailable $avail MiB, swap devices $swap_dev"
    waiting=$(find runs -maxdepth 1 -name '*.log' -mmin -15 -exec sh -c 'tail -1 "$1" | grep -q "waiting for host swap reset" && echo "$1"' _ {} \;)
    if [ -n "$waiting" ]; then
      ssh -o BatchMode=yes "$HOST" 'sudo -n sh -c "swapoff -a && swapon -a"' && echo "$(date -u +%FT%TZ) reset for $waiting"
    fi
  fi
  i=$((i + 1)); sleep 10
done
