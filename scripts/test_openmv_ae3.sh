#!/usr/bin/env bash
# test_openmv_ae3.sh — Phase 4 hardware smoke test (Spec §4 Phase 4 exit, §24, §25).
#
# Run ON the Pi from the repo root:  ./scripts/test_openmv_ae3.sh [SERIAL]
# Deploys the capture service to the AE3, then exercises the Phase 4 exit criteria:
# discover by USB identity, capture 3 stills in a row each retrieved with a matching
# checksum, and reject a bad command cleanly.
#
# Exit 0 = PASS. Non-zero = FAIL. SERIAL defaults to the bring-up board.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! venv missing at $PY — run scripts/install_pi.sh" >&2; exit 1; }

SERIAL="${1:-0829c14000000000}"

echo "== OpenMV AE3 smoke test (serial $SERIAL) =="

echo "-- discover"
"$PY" -m host_tools.discover_openmv || true

echo "-- deploy capture service"
"$PY" -m host_tools.deploy_openmv --board ae3 --serial "$SERIAL"

# Give the board a moment to re-enumerate after the post-deploy reset.
sleep 3

echo "-- run hardware smoke (device info, 3 captures + checksums, bad-command rejection)"
AE3_SERIAL="$SERIAL" "$PY" -m pytest tests/hardware/test_openmv_ae3.py -v -p no:cacheprovider

echo "PASS: AE3 capture service validated on hardware."
