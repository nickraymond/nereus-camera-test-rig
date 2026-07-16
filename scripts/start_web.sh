#!/usr/bin/env bash
# start_web.sh — launch the Phase 6 result-review web app (Spec §14).
#
# Run from the repo root (Pi or Mac):  ./scripts/start_web.sh [CONFIG]
# CONFIG defaults to configs/rig.example.yaml. Binds to web.host/web.port from
# the config (default 0.0.0.0:8080) so the app is reachable over Tailscale,
# e.g. http://nereus000:8080 from any device on the tailnet.
#
# Requires the 'web' extra:  .venv/bin/pip install -e ".[web]"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! venv missing at $PY — run scripts/install_pi.sh (Pi) or 'python3 -m venv .venv' (Mac)" >&2; exit 1; }

"$PY" -c "import flask" 2>/dev/null || {
  echo "!! Flask not installed — run: $ROOT/.venv/bin/pip install -e \".[web]\"" >&2
  exit 1
}

CONFIG="${1:-configs/rig.example.yaml}"
exec "$PY" -m nereus_camera_test_rig.web.app --config "$CONFIG"
