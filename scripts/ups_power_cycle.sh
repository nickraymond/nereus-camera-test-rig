#!/usr/bin/env bash
# ups_power_cycle.sh — full power cycle of the rig through the LiFePO4wered/Pi+ UPS (no root).
#
# Purpose: clear a wedged USB board (an AE3 that stops reading its USB input cannot be fixed in
#   software and must never be USB-reset) or anything else a reboot alone does not clear: the
#   UPS cuts its output, which powers the Pi AND the boards, then turns it back on by timer.
#
# How (from ~/LiFePO4wered-Pi/lifepo4wered-daemon.c + README, verified on nereus002 2026-10-13):
#   1. `lifepo4wered-cli set WAKE_TIME <min>` — the UPS turns the Pi back on <min> minutes after
#      it is off (README: "set wake_time 60" = one hour).
#   2. `lifepo4wered-cli set PI_RUNNING 0` — the root daemon polls this flag every second; on 0
#      it logs "Signal from LiFePO4wered module to shut down" and runs `systemctl poweroff`
#      (a CLEAN shutdown — filesystems synced), after which the UPS cuts its output.
#   Nothing is written to UPS flash (no CFG_WRITE); both registers are volatile.
#
# Usage (on the Pi):
#   bash scripts/ups_power_cycle.sh --yes [--wake-min 1] [--reason "AE3 wedged"]
#   bash scripts/ups_power_cycle.sh            # dry run: prints the checks, changes nothing
# Expect: the ssh session drops within ~5 s; the Pi answers ping again ~2-4 min later
#   (shutdown ~20-60 s + wake timer + ~40 s boot). lsusb device numbers change = boards were
#   power-cycled. Log: ~/ups_power_cycle.log (+ syslog tag ups_power_cycle).
#
# Guards (refuses unless all hold):
#   - the UPS answers and reports PI_RUNNING=1;
#   - on battery, VBAT >= MIN_VBAT_MV (3300): below VBAT_BOOT (3150 here) the UPS will not boot
#     the Pi again, and Nick measured boot-load resets on a 3.23 V cell (dive_autostart.sh);
#   - uptime >= MIN_UPTIME_S (300) unless --force: a caller at boot can never make a cycle loop;
#   - at most MAX_PER_DAY (3) cycles per 24 h per --tag (counted in the log), so manual/bench
#     cycles never use up the pool loop's own budget (tag pool_auto, MAX_PER_DAY=2).
# Failure mode to know: if the wake timer did not fire, the rig stays OFF until someone presses
#   the UPS touch button or unplugs/replugs its input. Test on the bench before relying on it.
set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

WAKE_MIN=1; YES=0; FORCE=0; REASON="manual"; TAG="manual"
MIN_VBAT_MV="${MIN_VBAT_MV:-3300}"; MIN_UPTIME_S="${MIN_UPTIME_S:-300}"; MAX_PER_DAY="${MAX_PER_DAY:-3}"
LOG="$HOME/ups_power_cycle.log"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1 ;; --force) FORCE=1 ;;
    --wake-min) WAKE_MIN="$2"; shift ;;
    --reason) REASON="$2"; shift ;;
    --tag) TAG="$2"; shift ;;
    *) echo "usage: $0 [--yes] [--wake-min N] [--reason TEXT] [--tag NAME] [--force]"; exit 2 ;;
  esac; shift
done

say() { printf 'ups_power_cycle: %s\n' "$*"; }
get() {  # one register: the CLI prints the bare number (e.g. "3391"); "" if it fails
  lifepo4wered-cli get "$1" 2>/dev/null | awk -F= '{v=$NF; gsub(/[^0-9]/,"",v); print v}'; }
refuse() { say "REFUSED: $*"; echo "$(date -u -Iseconds) REFUSED reason=\"$REASON\" $*" >>"$LOG"; exit 3; }

command -v lifepo4wered-cli >/dev/null || refuse "lifepo4wered-cli not found"
[ "$WAKE_MIN" -ge 1 ] 2>/dev/null && [ "$WAKE_MIN" -le 30 ] || refuse "--wake-min must be 1..30"
RUNNING=$(get PI_RUNNING); VIN=$(get VIN); VBAT=$(get VBAT); THR=$(get VIN_THRESHOLD)
BOOT=$(get VBAT_BOOT); AUTO=$(get AUTO_BOOT); UP=$(cut -d. -f1 /proc/uptime)
say "UPS: PI_RUNNING=$RUNNING VIN=${VIN}mV VBAT=${VBAT}mV (boot >= ${BOOT}) AUTO_BOOT=$AUTO uptime=${UP}s"
[ "$RUNNING" = "1" ] || refuse "UPS does not report PI_RUNNING=1 (got '$RUNNING')"
if [ "$VIN" -lt "$THR" ] && [ "$VBAT" -lt "$MIN_VBAT_MV" ]; then
  refuse "on battery at ${VBAT} mV < ${MIN_VBAT_MV} mV: it might not boot again"
fi
[ "$FORCE" = 1 ] || [ "$UP" -ge "$MIN_UPTIME_S" ] || refuse "uptime ${UP}s < ${MIN_UPTIME_S}s (loop guard; --force overrides)"
since=$(date -u -d '24 hours ago' +%s)
n24=$(awk -v s="$since" -v tag="tag=$TAG" '$2=="CYCLE" && index($0, tag){cmd="date -u -d "$1" +%s"; cmd | getline t; close(cmd); if (t>=s) n++} END{print n+0}' "$LOG" 2>/dev/null)
[ "${n24:-0}" -lt "$MAX_PER_DAY" ] || refuse "already ${n24} '$TAG' cycles in 24 h (max ${MAX_PER_DAY})"

if [ "$YES" != 1 ]; then
  say "dry run OK — would set WAKE_TIME=$WAKE_MIN then PI_RUNNING=0 (add --yes)"; exit 0
fi
lifepo4wered-cli set WAKE_TIME "$WAKE_MIN" >/dev/null
[ "$(get WAKE_TIME)" = "$WAKE_MIN" ] || refuse "WAKE_TIME read back $(get WAKE_TIME), not $WAKE_MIN"
echo "$(date -u -Iseconds) CYCLE tag=$TAG boot_id=$(cat /proc/sys/kernel/random/boot_id) wake_min=$WAKE_MIN vin=$VIN vbat=$VBAT reason=\"$REASON\"" >>"$LOG"
logger -t ups_power_cycle "power cycle requested: wake in ${WAKE_MIN} min, reason: $REASON" 2>/dev/null
sync
say "WAKE_TIME=$WAKE_MIN set; asking the UPS daemon to power off now (back in ~$((WAKE_MIN + 2)) min)"
lifepo4wered-cli set PI_RUNNING 0 >/dev/null
exit 0
