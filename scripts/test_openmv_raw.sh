#!/usr/bin/env bash
# test_openmv_raw.sh — Phase 8 S3 hardware smoke test for OpenMV capture_raw (OQ-21).
#
# Run ON the Pi from the repo root:  ./scripts/test_openmv_raw.sh
# Assumes the current capture service is deployed on both boards (test_openmv_n6.sh /
# test_openmv_ae3.sh deploy it). Captures a metered and a locked-exposure Bayer RAW on
# each board (reset_board before each: one camera session per boot on the AE3), runs the
# linear JPEG XL round trip with the rig's V1 card on each metered frame, then the
# card-metered locked-exposure recipe (scripts/capture_raw_openmv.py) on each board.
#
# Stop Nick's workbench recipe first:  curl -X POST localhost:8088/api/stop
# Exit 0 = PASS. Frames + jxl-check output land in results/raw_smoke/<UTC time>/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! venv missing at $PY — run scripts/install_pi.sh" >&2; exit 1; }

OUT="$ROOT/results/raw_smoke/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
echo "== OpenMV capture_raw smoke test -> $OUT =="

RAW_OUT="$OUT" "$PY" -m pytest tests/hardware/test_openmv_raw.py -v -p no:cacheprovider

for board in n6 ae3; do
    echo "-- jxl-check $board (card configs/cards/nereus_v1.yaml)"
    "$PY" -m host_tools.color jxl-check "$OUT/${board}_metered.bayer" \
        --card configs/cards/nereus_v1.yaml --crop card --out "$OUT/jxl"
done

for board in n6 ae3; do
    echo "-- card-metered locked-exposure recipe $board"
    "$PY" scripts/capture_raw_openmv.py --board "$board" --out "$OUT/recipe"
done

echo "PASS: capture_raw + card-metered recipe validated on N6 + AE3; see $OUT"
