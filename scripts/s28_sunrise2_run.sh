#!/bin/bash
# Sunrise loop 2 (Sprint28, nereus002, 2026-10-09 06:00-10:30): the 4 still arms per slot, then
# on selected slots a still -> video white-balance hand-off. Capture only; nothing is sent.
#   run.sh "2026-10-09 06:00" "2026-10-09 10:30"
# Per slot (every 10 min):
#   stills (always first): A stock, B gain-locked (tuning copy, shutter <= 60 ms, AG 1.0),
#                          C --shutter 250000 --gain 1.12, D --ev 1   (full res, --raw, focus 1.094)
#   hand-off (selected slots: every slot 06:00-08:00, then every 3rd slot to 10:30), only if
#     CmaFree >= CMA_MIN_KB, >= 6 min left in the slot, and video is not disabled:
#     1. card-grey WB on B's DNG (scripts/s28_card_wb.py, lean crop-first, single-scale detect,
#        ulimit -v 1,000,000, oom_score_adj 1000):
#        red_gain = G/R, blue_gain = G/B of gray_mid; too dark / no card -> the last good gains
#        ("held"), or auto WB if there are none yet
#     2. clip_card: 30 s (300 frames) 1280x720 10 fps H.264, floor gain, --awb custom R,B
#     3. clip_auto (alternate selected slots): the same with auto WB (the control)
#   guard: a still that fails after any clip disables video for the rest of the loop
#   (2026-10-08 finding: rpicam-vid holds ~75 MB CMA after first use).
D=$HOME/nereus-camera-test-rig/results/s28_sunrise2_20261009
TUNE=$D/imx708_wide_lowgain_s60000_g1.json
GEOM=$D/geometry.json
RIG=$HOME/nereus-rig-v3truth
PY=$HOME/nereus-camera-test-rig/.venv/bin/python
CMA_MIN_KB=${CMA_MIN_KB:-61440}
cd "$D" || exit 1
log() { echo "$(date +%FT%T%z) $*" >> "$D/log.txt"; }
cma() { grep CmaFree /proc/meminfo | awk '{print $2}'; }
VIDEO=1; CLIPS=0; SEL=0; LAST_R=""; LAST_B=""
VID=(--codec h264 --inline --mode 2304:1296:10:P --width 1280 --height 720 --framerate 10
     --bitrate 2000000 --roi 0.000000,0.000000,1.000000,1.000000 --profile baseline --intra 30
     --autofocus-mode manual --lens-position 1.094 --gain 1.0 --frames 300)

clip() {  # clip <stem> <awb args...>
  local stem=$1; shift
  local c0 t0 t1 rc
  c0=$(cma); t0=$(date +%s.%N)
  rpicam-vid -n "${VID[@]}" "$@" --metadata "$stem.meta.json" --metadata-format json \
    -o "$stem.h264" 2> "$stem.err"; rc=$?
  t1=$(date +%s.%N)
  ffmpeg -v error -y -framerate 10 -i "$stem.h264" -c copy "$stem.mp4" && rm -f "$stem.h264"
  log "clip $stem rc=$rc wall=$(python3 -c "print(round($t1-$t0,1))") s size=$(stat -c %s "$stem.mp4" 2>/dev/null) cma_before=$c0 cma_after=$(cma) args=$*"
  CLIPS=$((CLIPS + 1))
}

log "loop pid $$ windows: $* CMA_MIN_KB=$CMA_MIN_KB"
while [ "$#" -ge 2 ]; do
start=$(date -d "$1" +%s); end=$(date -d "$2" +%s); shift 2
t=$start
while [ "$t" -le "$end" ]; do
  now=$(date +%s); [ "$now" -lt "$t" ] && sleep $((t - now))
  hm=$(date -d "@$t" +%H%M); slot=$(date -d "@$t" +%m%d%H%M)
  st=$(curl -s -m3 http://localhost:8088/api/runner | grep -o '"state": *"[a-z]*"' | grep -o '[a-z]*"$' | tr -d '"')
  if [ -n "$st" ] && [ "$st" != "idle" ]; then log "slot $slot SKIP workbench=$st"; t=$((t + 600)); continue; fi
  failed=0
  for prof in stock lowgain long250 ev1; do
    stem=s${slot}_${prof}
    case "$prof" in
      lowgain) T=(--tuning-file "$TUNE") ;;
      long250) T=(--shutter 250000 --gain 1.12) ;;
      ev1)     T=(--ev 1) ;;
      *)       T=() ;;
    esac
    s=$(date +%s)
    if rpicam-still -n --timeout 2000 --width 4608 --height 2592 --quality 90 --lens-position 1.094 \
        "${T[@]}" --metadata "$stem.json" --raw -o "$stem.jpg" 2> "$stem.err"; then
      log "slot $slot $prof OK $(( $(date +%s) - s )) s"
    else
      log "slot $slot $prof FAIL rc=$?"; failed=1
    fi
  done
  if [ "$failed" = 1 ] && [ "$CLIPS" -gt 0 ] && [ "$VIDEO" = 1 ]; then
    VIDEO=0; log "slot $slot VIDEO DISABLED for the rest of the loop: a still failed after $CLIPS clip(s)"
  fi
  mins=$(( (10#${hm:0:2}) * 60 + 10#${hm:2:2} ))
  selected=0
  if [ "$mins" -le $((8 * 60)) ]; then selected=1
  elif [ $(( (mins - 8 * 60) % 30 )) -eq 0 ]; then selected=1; fi
  if [ "$VIDEO" = 1 ] && [ "$selected" = 1 ]; then
    left=$(( t + 600 - $(date +%s) ))
    if [ "$left" -lt 360 ]; then log "slot $slot video SKIP: only ${left} s left"
    elif [ "$(cma)" -lt "$CMA_MIN_KB" ]; then log "slot $slot video SKIP: CmaFree $(cma) kB < $CMA_MIN_KB"
    else
      SEL=$((SEL + 1))
      # oom_score_adj 1000: only this child can be OOM-killed; a killed card-wb (rc 137)
      # falls through to the held / auto gains below, never a skipped still
      wbj=$(/bin/sh -c 'echo 1000 > /proc/self/oom_score_adj; ulimit -v 1000000; exec "$0" "$@"' "$PY" "$RIG/scripts/s28_card_wb.py" \
            "s${slot}_lowgain.dng" --geometry "$GEOM" 2> "s${slot}_wb.err")
      wbrc=$?
      echo "$wbj" > "s${slot}_wb.json"
      R=$(echo "$wbj" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("red_gain",""))' 2>/dev/null)
      B=$(echo "$wbj" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("blue_gain",""))' 2>/dev/null)
      if [ "$wbrc" = 0 ] && [ -n "$R" ]; then LAST_R=$R; LAST_B=$B; src=card
      elif [ -n "$LAST_R" ]; then R=$LAST_R; B=$LAST_B; src=held
      else src=auto; fi
      log "slot $slot wb rc=$wbrc source=$src gains=$R,$B"
      if [ "$src" = auto ]; then clip "s${slot}_clip_card" --awb auto
      else clip "s${slot}_clip_card" --awb custom --awbgains "$R,$B"; fi
      if [ $((SEL % 2)) -eq 1 ]; then clip "s${slot}_clip_auto" --awb auto; fi
    fi
  fi
  t=$((t + 600))
done
done
log "loop done"
