#!/bin/bash
# Sprint28 B3a encode ladder on nereus002 (EM call (a), 2026-10-08): every sweep DNG through the
# #134 pipeline (rc_raw_jxl.py --layout rgb, default search, 1600x900 crop), one at a time.
# A sampler polls /proc every 0.1 s for cjxl's VmPeak / VmHWM and the system CmaFree.
#   scripts/s28_b3a_ladder.sh <sweep_dir> <b3a_dir> <out_dir>
set -u
SWEEP=$1; B3A=$2; OUT=$3
PY=$HOME/nereus-camera-test-rig/.venv/bin/python
mkdir -p "$OUT"
echo "frame,rc,wall_s" > "$OUT/runs.csv"
for dng in "$SWEEP"/s*_*.dng; do
  stem=$(basename "$dng" .dng)
  d="$OUT/$stem"; mkdir -p "$d"
  ( while true; do
      for p in $(pgrep -x cjxl); do
        awk -v p="$p" '/^VmPeak|^VmHWM/{printf "%s %s %s ", p, $1, $2} END{print ""}' /proc/$p/status 2>/dev/null
      done
      grep CmaFree /proc/meminfo
      sleep 0.1
    done ) > "$d/proc_samples.txt" &
  S=$!
  t0=$(date +%s%N)
  ( cd "$B3A" && "$PY" rc_raw_jxl.py --dng "$dng" --metadata "${dng%.dng}.json" --layout rgb \
      --out "$d" > "$d/stdout.txt" 2>&1 )
  rc=$?
  t1=$(date +%s%N)
  kill $S 2>/dev/null; wait $S 2>/dev/null
  echo "$stem,$rc,$(( (t1 - t0) / 1000000 ))e-3" >> "$OUT/runs.csv"
  echo "$stem rc=$rc $(tail -1 "$d/stdout.txt")"
done
echo "ladder done"
