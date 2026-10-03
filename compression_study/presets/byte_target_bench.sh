#!/bin/bash
# byte_target_bench.sh — Step C bench ON nereus002: byte_target.py on the 3 sweep frames ×
# 6 card-centred ROIs × caps 180 / 195 at effort 5, plus 2000x1124 at e7 / 195 (Step A's PASS).
# Each run in its own process with ulimit -v 700 MiB and nice 10 (cjxl keeps the production
# 250 MiB guard inside). One JSON line per run on stdout. Run from ~/s28_presets/BM_Devel_Pi.
set -u
S=${1:-$HOME/s28_presets/sweep_20261003T203525Z}
PY=${PY:-python3}
roi() { case "$1" in 800x450) echo 1944,1078,800,450;; 1200x676) echo 1744,966,1200,676;;
  1600x900) echo 1544,854,1600,900;; 2000x1124) echo 1344,742,2000,1124;;
  2304x1296) echo 1192,656,2304,1296;; 3072x1728) echo 808,440,3072,1728;; esac; }
run() {  # frame roi cap effort
  ( ulimit -v 716800; nice -n 10 $PY byte_target.py --dng "$S/stop_+0_r$1.dng" \
      --metadata "$S/stop_+0_r$1.json" --crop "$(roi "$2")" --cap "$3" --effort "$4" ) \
    | sed "s/^{/{\"frame\": $1, \"roi\": \"$2\", /"
}
for f in 0 1 2; do
  for r in 800x450 1200x676 1600x900 2000x1124 2304x1296 3072x1728; do
    for c in 180 195; do run "$f" "$r" "$c" 5; done
  done
  run "$f" 2000x1124 195 7
done
free -m | sed -n 2p >&2
