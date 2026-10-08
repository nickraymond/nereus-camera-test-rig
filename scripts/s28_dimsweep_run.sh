#!/bin/bash
# Dim sweep (EM / Nick 2026-10-08): IMX708 pairs every 10 min in the windows given as args:
#   run.sh "2026-10-08 18:00" "2026-10-08 21:30" ["2026-10-09 06:00" "2026-10-09 10:30" ...]
# Capture only: gain-locked (tuning copy, shutter <= 60 ms, analogue gain 1.0) then stock auto, full sensor + --raw,
# focus locked at the card (1.094 dpt). No metadata is read here.
D=$HOME/nereus-camera-test-rig/results/s28_dimsweep_20261008
TUNE=$D/imx708_wide_lowgain_s60000_g1.json
cd "$D" || exit 1
log() { echo "$(date +%FT%T%z) $*" >> "$D/log.txt"; }
log "loop pid $$ windows: $*"
while [ "$#" -ge 2 ]; do
start=$(date -d "$1" +%s); end=$(date -d "$2" +%s); shift 2
t=$start
while [ "$t" -le "$end" ]; do
  now=$(date +%s); [ "$now" -lt "$t" ] && sleep $((t - now))
  slot=$(date -d "@$t" +%H%M)
  st=$(curl -s -m3 http://localhost:8088/api/runner | grep -o '"state": *"[a-z]*"' | grep -o '[a-z]*"$' | tr -d '"')
  if [ -n "$st" ] && [ "$st" != "idle" ]; then log "slot $slot SKIP workbench=$st"; t=$((t + 600)); continue; fi
  slot=$(date -d "@$t" +%m%d%H%M)
  for prof in lowgain stock; do
    stem=s${slot}_${prof}
    T=(); [ "$prof" = lowgain ] && T=(--tuning-file "$TUNE")
    s=$(date +%s)
    if rpicam-still -n --timeout 2000 --width 4608 --height 2592 --quality 90 --lens-position 1.094 \
        "${T[@]}" --metadata "$stem.json" --raw -o "$stem.jpg" 2> "$stem.err"; then
      log "slot $slot $prof OK $(( $(date +%s) - s )) s"
    else
      log "slot $slot $prof FAIL rc=$?"
    fi
  done
  t=$((t + 600))
done
done
log "loop done"
