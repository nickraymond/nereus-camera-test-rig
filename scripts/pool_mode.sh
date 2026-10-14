#!/usr/bin/env bash
# pool_mode.sh — arm / start / stop / status for the unattended pool capture loop (nereus002).
#
# Purpose: one command before the housing is sealed, and the rig captures by itself until
#   power-off — including after a power cycle, with no network (scripts/pool_capture.py).
#
#   pool_mode.sh arm [--hours 8] [--lens-position 0.8]   # boot-start ON (crontab + flag)
#   pool_mode.sh start [--mac-time ISO]                  # start the loop NOW (background)
#   pool_mode.sh status                                  # armed? running? last health line
#   pool_mode.sh stop                                    # clean stop after the set in flight
#   pool_mode.sh disarm                                  # boot-start OFF, crontab restored
#   (internal) boot | supervise
#
# From the Mac (the Mac's clock is recorded as the run's time anchor):
#   ssh pi@nereus002.local "bash ~/nereus-rig-poolcam/scripts/pool_mode.sh start --mac-time $(date -Iseconds)"
#
# Arm does two reversible things (Nick OK 2026-10-13 3:05 PM): it creates ~/.no_dive_autostart
#   — Nick's own stand-down switch for dive-autostart.service, so the workbench's ci-record does
#   not take the cameras at boot (only if absent; its pre-arm state is recorded and a flag Nick
#   made himself is never modified) — and adds one marked @reboot line to the pi crontab (backup
#   in ~/pool_backup/<ts>/). Disarm restores both and fails if the crontab differs from the backup.
# No root needed. Never touches Nick's services, the workbench or its recipes.
#
# Supervisor ("watchdog"): the loop runs under `supervise`, which restarts it (new run folder)
#   if it dies abnormally, at most MAX_RESTARTS times. A clean stop (STOP file, duration, disk,
#   battery) or a refusal (exit 3 storage / 4 battery) is final.
set -uo pipefail
# POOL_MODE_PATH_PREFIX: tests put fake crontab / lifepo4wered-cli first (tests/unit).
export PATH="${POOL_MODE_PATH_PREFIX:+$POOL_MODE_PATH_PREFIX:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${POOL_PYTHON:-$HOME/nereus-camera-test-rig/.venv/bin/python}"
POOL="$ROOT/results/pool"
ARMED="$HOME/.pool_armed"            # holds the run args; its presence = boot-start on
DIVE_FLAG="$HOME/.no_dive_autostart"
FLAG_MARK="created by nereus-camera-test-rig scripts/pool_mode.sh"
PIDFILE="$HOME/.pool_capture.pid"    # the supervisor's pid
NO_RESTART="$HOME/.pool_no_restart"  # stop asks the supervisor not to restart
CRON_MARK="# POOL_MODE nereus-camera-test-rig"
WB="http://127.0.0.1:8088"
MAX_RESTARTS=5

say() { printf 'pool_mode: %s\n' "$*"; }
die() { say "FAIL: $*"; exit 1; }
ts() { date -u +%Y%m%dT%H%M%SZ; }

running_pid() {  # the live supervisor pid, or nothing
  local pid; pid="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && grep -q "pool_mode.sh" "/proc/$pid/cmdline" 2>/dev/null; then
    echo "$pid"
  fi
}

workbench_state() {  # idle | live | starting | ... | "" when the workbench does not answer
  curl -fsS -m 5 "$WB/api/runner" 2>/dev/null | "$PY" -c 'import json,sys
try: print(json.load(sys.stdin).get("state",""))
except Exception: print("")' 2>/dev/null
}

newest_run() { ls -1d "$POOL"/pool[0-9]* 2>/dev/null | sort | tail -1; }

cmd_arm() {
  local args=("$@")
  [ -x "$PY" ] || die "python not found: $PY (set POOL_PYTHON)"
  "$PY" "$ROOT/scripts/pool_capture.py" run --help >/dev/null || die "pool_capture.py does not load"
  local bk="$HOME/pool_backup/$(ts)"; mkdir -p "$bk"
  if crontab -l >"$bk/crontab_before.txt" 2>/dev/null; then :; else : >"$bk/crontab_before.txt"; fi
  # Flag pre-arm state, recorded so disarm restores exactly it (QE #97): "absent" -> pool mode
  # creates it (with the marker) and disarm removes it; "ours" (marker from an earlier arm) ->
  # same; "nick" (any flag without our marker, including an empty `touch`) -> never modified.
  local flag_state="absent"
  if [ -e "$DIVE_FLAG" ]; then
    if grep -q "$FLAG_MARK" "$DIVE_FLAG"; then flag_state="ours"; else flag_state="nick"; fi
  fi
  echo "$flag_state $(sha256sum "$DIVE_FLAG" 2>/dev/null | cut -d' ' -f1)" >"$bk/dive_flag_before.txt"
  [ "$flag_state" = "absent" ] && echo "$FLAG_MARK $(ts)" >"$DIVE_FLAG"
  { grep -v "$CRON_MARK" "$bk/crontab_before.txt"
    echo "@reboot /bin/bash $ROOT/scripts/pool_mode.sh boot >>$POOL/boot.log 2>&1 $CRON_MARK"
  } | crontab - || die "crontab install failed"
  mkdir -p "$POOL"
  : >"$ARMED"  # one arg per line; no args = an empty file (never a blank line)
  [ "${#args[@]}" -gt 0 ] && printf '%s\n' "${args[@]}" >"$ARMED"
  echo "$bk" >>"$ARMED.backups"
  # Clock anchor while the Pi still has NTP: Pi clock vs the UPS RTC (the RTC keeps running on
  # the battery in the pool; the Pi clock does not survive a power-off).
  echo "{\"armed_pi_utc\": \"$(date -u -Iseconds)\", \"pi_epoch\": $(date +%s), \"ups_rtc_epoch\": $(lifepo4wered-cli get RTC_TIME 2>/dev/null | tr -dc '0-9' || echo null), \"ntp\": \"$(timedatectl show -p NTPSynchronized --value 2>/dev/null)\"}" >"$POOL/arm_clock_$(ts).json"
  # Surface pressure for depth (fail-soft: an error is recorded, the loop then uses its run start).
  /usr/bin/python3 "$ROOT/scripts/depth_ms5837.py" --samples 5 >"$POOL/surface_ref.json" 2>/dev/null \
    || echo '{"ok": false, "error": "depth script failed"}' >"$POOL/surface_ref.json"
  say "surface ref (depth zero): $(head -c 160 "$POOL/surface_ref.json")"
  say "ARMED: boot-start on; args: ${args[*]+${args[*]}}; crontab backup $bk"
  crontab -l | grep "$CRON_MARK"
  say "dive-autostart stood down: $DIVE_FLAG (pre-arm: $flag_state; $(head -c 80 "$DIVE_FLAG"))"
}

cmd_disarm() {
  local bk rc=0; bk="$(tail -1 "$ARMED.backups" 2>/dev/null)"
  crontab -l 2>/dev/null | grep -v "$CRON_MARK" >"/tmp/pool_cron.$$"
  if [ -s "/tmp/pool_cron.$$" ]; then crontab "/tmp/pool_cron.$$"; else crontab -r 2>/dev/null; fi
  rm -f "/tmp/pool_cron.$$"
  if [ -n "$bk" ] && [ -f "$bk/crontab_before.txt" ]; then
    if diff -q <(crontab -l 2>/dev/null | grep -v "$CRON_MARK") <(grep -v "$CRON_MARK" "$bk/crontab_before.txt") >/dev/null; then
      say "crontab restored (matches $bk/crontab_before.txt)"
    else
      say "FAIL: crontab differs from $bk/crontab_before.txt — check by hand"; rc=1
    fi
  fi
  # Restore the flag's pre-arm state. Pre-arm "nick" (or no record of it) -> never touched.
  local before; before="$(cut -d' ' -f1 "$bk/dive_flag_before.txt" 2>/dev/null)"
  if [ -e "$DIVE_FLAG" ] && grep -q "$FLAG_MARK" "$DIVE_FLAG" && { [ "$before" = "absent" ] || [ "$before" = "ours" ]; }; then
    rm -f "$DIVE_FLAG"; say "removed $DIVE_FLAG (pre-arm: $before; dive-autostart runs ci-record at the next boot)"
  elif [ -e "$DIVE_FLAG" ]; then
    say "left $DIVE_FLAG as it was (pre-arm: ${before:-unknown}; not pool mode's to remove)"
  fi
  rm -f "$ARMED"
  say "DISARMED${bk:+ (backup $bk)}; a running loop is NOT stopped — use: pool_mode.sh stop"
  return $rc
}

cmd_supervise() {  # runs detached; restarts an abnormal exit
  echo $$ >"$PIDFILE"; rm -f "$NO_RESTART"
  local restarts=0 rc
  while :; do
    "$PY" "$ROOT/scripts/pool_capture.py" run "$@"; rc=$?
    case "$rc" in
      0) say "loop ended cleanly"; break ;;
      3|4) say "loop refused to start (rc=$rc), not restarting"; break ;;
    esac
    [ -e "$NO_RESTART" ] && { say "stop requested, not restarting (rc=$rc)"; break; }
    restarts=$((restarts + 1))
    [ "$restarts" -gt "$MAX_RESTARTS" ] && { say "FAIL: $MAX_RESTARTS restarts used, giving up"; break; }
    say "WARN: loop died rc=$rc — restart $restarts/$MAX_RESTARTS in 15 s (new run folder)"
    sleep 15
  done
  rm -f "$PIDFILE"
}

launch() {  # launch <started-by> [extra args]
  local by="$1"; shift
  [ -n "$(running_pid)" ] && die "already running (supervisor pid $(running_pid)); pool_mode.sh status"
  local st; st="$(workbench_state)"
  case "$st" in
    ""|idle|failed) ;;
    *) die "the workbench runner is '$st' (a recipe owns the cameras) — stop it on :8088 first" ;;
  esac
  local saved=(); [ -f "$ARMED" ] && mapfile -t saved < <(grep -v '^$' "$ARMED")
  mkdir -p "$POOL"
  rm -f "$NO_RESTART"
  setsid nohup /bin/bash "$ROOT/scripts/pool_mode.sh" supervise ${saved[@]+"${saved[@]}"} --started-by "$by" "$@" \
    >>"$POOL/supervisor.log" 2>&1 </dev/null &
  sleep 3
  [ -n "$(running_pid)" ] || die "loop did not start — see $POOL/supervisor.log"
  say "STARTED (supervisor pid $(running_pid)); args: ${saved[*]+${saved[*]}} $*"
  sleep 5; local r; r="$(newest_run)"; [ -n "$r" ] && say "run folder: $r"
}

cmd_boot() {
  say "boot $(date -u -Iseconds) uptime $(cut -d' ' -f1 /proc/uptime)s"
  [ -f "$ARMED" ] || { say "not armed — nothing to do"; exit 0; }
  # Let the workbench come up first so a recipe (if one was started anyway) is visible, and the
  # boards finish enumerating. Bounded: the loop also waits per set for missing boards.
  local t0; t0=$(date +%s)
  while [ -z "$(workbench_state)" ] && [ $(( $(date +%s) - t0 )) -lt 60 ]; do sleep 3; done
  sleep 10
  launch boot
}

cmd_start() { launch manual "$@"; }

cmd_stop() {
  local pid; pid="$(running_pid)"
  touch "$NO_RESTART"
  local r; r="$(newest_run)"
  [ -n "$r" ] && touch "$r/STOP"
  [ -z "$pid" ] && { say "not running${r:+ (STOP placed in $r)}"; return 0; }
  say "STOP placed in $r; waiting for the set in flight (up to 300 s)…"
  local i; for i in $(seq 1 100); do kill -0 "$pid" 2>/dev/null || break; sleep 3; done
  if kill -0 "$pid" 2>/dev/null; then say "WARN: still running after 300 s (pid $pid)"; return 1; fi
  say "stopped"; tail -1 "$r/pool.log" 2>/dev/null
}

cmd_status() {
  local pid r; pid="$(running_pid)"; r="$(newest_run)"
  say "armed:    $([ -f "$ARMED" ] && echo "YES ($(tr '\n' ' ' <"$ARMED"))" || echo no)"
  say "crontab:  $(crontab -l 2>/dev/null | grep -c "$CRON_MARK") pool line(s)"
  say "dive flag: $([ -e "$DIVE_FLAG" ] && echo present || echo absent)  workbench: $(workbench_state)"
  say "running:  ${pid:-no}"
  if [ -n "$r" ]; then
    say "newest run: $r"
    "$PY" -c 'import json,sys; m=json.load(open(sys.argv[1])); print("pool_mode: sets", m.get("sets"), "ok", m.get("ok_counts"), "stop", m.get("stop_reason"))' "$r/run_manifest.json" 2>/dev/null
    tail -1 "$r/pool.log" 2>/dev/null
  fi
  df -h "$ROOT" | tail -1
}

sub="${1:-status}"; shift || true
case "$sub" in
  arm) cmd_arm "$@" ;;
  disarm) cmd_disarm ;;
  start) cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  boot) cmd_boot ;;
  supervise) cmd_supervise "$@" ;;
  *) die "unknown command '$sub' (arm|start|status|stop|disarm)" ;;
esac
