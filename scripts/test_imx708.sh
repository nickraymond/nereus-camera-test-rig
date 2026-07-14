#!/usr/bin/env bash
# test_imx708.sh — Phase 1 hardware smoke test (Spec §4 Phase 1 exit, §19).
#
# Run ON the Pi from the repo root:  ./scripts/test_imx708.sh
# Captures a still via the Imx708 adapter and asserts the artifact is real:
# exists, plausible size, correct dimensions, metadata sidecar written.
#
# Exit 0 = PASS (a validated image + metadata exist). Non-zero = FAIL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! venv missing at $PY — run scripts/install_pi.sh" >&2; exit 1; }

OUT="${1:-$ROOT/results/smoke_imx708}"
MIN_BYTES="${MIN_BYTES:-50000}"   # a 4608x2592 JPEG is ~1 MB; 50 KB is a safe floor

echo "== IMX708 still smoke test =="
echo "-- capturing to $OUT"
"$PY" -m nereus_camera_test_rig.cli --config configs/rig.example.yaml \
  capture --camera imx708 --kind image --out "$OUT"

# The adapter already validates (dimensions/size) and exits non-zero on failure;
# re-assert here on the artifacts themselves so the script proves the outcome.
IMG="$(ls -t "$OUT"/imx708_image_*.jpg 2>/dev/null | head -1 || true)"
[ -n "$IMG" ] || { echo "FAIL: no image produced in $OUT" >&2; exit 1; }

BYTES="$(stat -c%s "$IMG" 2>/dev/null || stat -f%z "$IMG")"
echo "-- image: $IMG ($BYTES bytes)"
[ "$BYTES" -ge "$MIN_BYTES" ] || { echo "FAIL: image too small ($BYTES < $MIN_BYTES)" >&2; exit 1; }

META="${IMG%.jpg}.json"
[ -f "$META" ] || { echo "FAIL: metadata sidecar missing ($META)" >&2; exit 1; }
echo "-- metadata: $META"

echo "PASS: validated still + metadata produced."
