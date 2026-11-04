#!/bin/bash
# host_power_probe.sh - runs ON THE HYPERVISOR (bare metal), not inside a VM.
# Samples true package power from RAPL (/sys/class/powercap) alongside per-VM CPU
# time from libvirt, so in-VM Kepler readings can be calibrated against ground truth.
# Installs nothing. Output is a CSV to be timestamp-joined with the in-VM measurements.
#
# Usage: sudo ./host_power_probe.sh <output.csv> <duration_mins> <tag> [interval_s]

set -u
OUT=${1:-host_power.csv}
DUR_MIN=${2:-5}
TAG=${3:-calib}
INT=${4:-5}

# ---------- preflight ----------
RAPL_ROOT=/sys/class/powercap
if [ ! -d "$RAPL_ROOT" ]; then
  echo "FATAL: $RAPL_ROOT missing. No RAPL on this host (or not bare metal)." >&2; exit 1
fi
mapfile -t ZONES < <(ls -d "$RAPL_ROOT"/intel-rapl:[0-9] 2>/dev/null)
if [ ${#ZONES[@]} -eq 0 ]; then
  echo "FATAL: no intel-rapl:N package zones under $RAPL_ROOT." >&2
  echo "Present: $(ls "$RAPL_ROOT" 2>/dev/null | tr '\n' ' ')" >&2; exit 1
fi
if [ ! -r "${ZONES[0]}/energy_uj" ]; then
  echo "FATAL: ${ZONES[0]}/energy_uj not readable. Run with sudo." >&2; exit 1
fi
HAVE_VIRSH=1; command -v virsh >/dev/null 2>&1 || HAVE_VIRSH=0
[ $HAVE_VIRSH -eq 0 ] && echo "WARN: virsh absent, per-VM CPU columns will be empty." >&2

NCPU=$(nproc)
echo "[probe] host=$(hostname) cpus=$NCPU zones: ${ZONES[*]}" >&2
for z in "${ZONES[@]}"; do echo "[probe]   $z name=$(cat "$z/name" 2>/dev/null) max=$(cat "$z/max_energy_range_uj" 2>/dev/null)" >&2; done

# Domains fixed at start so the CSV keeps a stable column layout.
DOMS=()
if [ $HAVE_VIRSH -eq 1 ]; then mapfile -t DOMS < <(virsh list --name 2>/dev/null | sed '/^$/d'); fi
echo "[probe] domains: ${DOMS[*]:-none}" >&2

# ---------- readers ----------
read_energy() { local z=$1; cat "$z/energy_uj" 2>/dev/null || echo 0; }
read_max()    { local z=$1; cat "$z/max_energy_range_uj" 2>/dev/null || echo 0; }
# cumulative host busy jiffies (total - idle - iowait)
read_hostcpu() { awk '/^cpu /{idle=$5+$6; tot=0; for(i=2;i<=NF;i++) tot+=$i; print tot" "idle}' /proc/stat; }
# cumulative cpu_time ns per domain, printed in DOMS order
read_domcpu() {
  local d
  for d in "${DOMS[@]}"; do
    virsh domstats --cpu-total "$d" 2>/dev/null | awk -F= '/cpu\.time=/{print $2; found=1} END{if(!found) print 0}'
  done
}

MAXES=(); for z in "${ZONES[@]}"; do MAXES+=("$(read_max "$z")"); done

# ---------- header ----------
HDR="timestamp,epoch,tag,pkg_total_W"
for z in "${ZONES[@]}"; do HDR="$HDR,$(basename "$z" | tr ':' '_')_W"; done
HDR="$HDR,host_cpu_busy_pct,host_ncpu"
for d in "${DOMS[@]}"; do HDR="$HDR,vm_${d}_cpu_pct"; done
echo "$HDR" > "$OUT"

# ---------- prime ----------
PREV_E=(); for z in "${ZONES[@]}"; do PREV_E+=("$(read_energy "$z")"); done
read PREV_TOT PREV_IDLE < <(read_hostcpu)
mapfile -t PREV_DOM < <(read_domcpu)
PREV_T=$(date +%s.%N)

ITER=$(( DUR_MIN * 60 / INT ))
echo "[probe] $ITER samples every ${INT}s -> $OUT" >&2

for (( i=1; i<=ITER; i++ )); do
  sleep "$INT"
  NOW_T=$(date +%s.%N)
  DT=$(awk -v a="$NOW_T" -v b="$PREV_T" 'BEGIN{d=a-b; print (d>0?d:1)}')

  # RAPL, with counter-wrap handling
  PKG_SUM=0; PER_ZONE=""
  for idx in "${!ZONES[@]}"; do
    z=${ZONES[$idx]}; e=$(read_energy "$z"); pe=${PREV_E[$idx]}; mx=${MAXES[$idx]}
    w=$(awk -v e="$e" -v pe="$pe" -v mx="$mx" -v dt="$DT" 'BEGIN{d=e-pe; if(d<0) d+=mx; printf "%.4f", d/1e6/dt}')
    PER_ZONE="$PER_ZONE,$w"
    PKG_SUM=$(awk -v a="$PKG_SUM" -v b="$w" 'BEGIN{printf "%.4f", a+b}')
    PREV_E[$idx]=$e
  done

  read TOT IDLE < <(read_hostcpu)
  BUSY=$(awk -v t="$TOT" -v pt="$PREV_TOT" -v i="$IDLE" -v pi="$PREV_IDLE" \
    'BEGIN{dt=t-pt; di=i-pi; if(dt<=0){print "0.00"} else {printf "%.2f", 100*(dt-di)/dt}}')
  PREV_TOT=$TOT; PREV_IDLE=$IDLE

  DOMCOLS=""
  if [ ${#DOMS[@]} -gt 0 ]; then
    mapfile -t CUR_DOM < <(read_domcpu)
    for idx in "${!DOMS[@]}"; do
      c=${CUR_DOM[$idx]:-0}; p=${PREV_DOM[$idx]:-0}
      # ns of vCPU time per second of wall clock, as % of ONE core
      pct=$(awk -v c="$c" -v p="$p" -v dt="$DT" 'BEGIN{d=c-p; if(d<0)d=0; printf "%.2f", 100*(d/1e9)/dt}')
      DOMCOLS="$DOMCOLS,$pct"; PREV_DOM[$idx]=$c
    done
  fi

  PREV_T=$NOW_T
  echo "$(date +'%Y-%m-%d %H:%M:%S'),$NOW_T,$TAG,$PKG_SUM$PER_ZONE,$BUSY,$NCPU$DOMCOLS" | tee -a "$OUT"
done
echo "[probe] done -> $OUT" >&2
