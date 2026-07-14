#!/usr/bin/env bash
# install_pi.sh — one-time Raspberry Pi environment setup for the test rig (Spec §16).
#
# Run this ON the Pi, from the repo root, as the 'pi' user:
#     cd ~/nereus-camera-test-rig && ./scripts/install_pi.sh
#
# It installs the system packages the rig needs (which require sudo — you will be
# prompted for a password), verifies the camera stack, and creates the project
# virtualenv. Idempotent: safe to re-run.
set -euo pipefail

echo "== Nereus rig — Pi environment setup =="

# --- 1. System packages (need sudo) ---------------------------------------
# git: deploy workflow. python3-venv/pip: project virtualenv + installs.
APT_PKGS=(git python3-venv python3-pip)
echo "-- installing system packages: ${APT_PKGS[*]}"
sudo apt-get update -qq
sudo apt-get install -y "${APT_PKGS[@]}"

# --- 2. Verify camera stack (Spec §9) -------------------------------------
echo "-- verifying camera command (rpicam-still preferred, libcamera-still fallback)"
if command -v rpicam-still >/dev/null 2>&1; then
  CAM_CMD=rpicam-still
elif command -v libcamera-still >/dev/null 2>&1; then
  CAM_CMD=libcamera-still
else
  echo "!! no rpicam-still or libcamera-still found — install rpicam-apps before continuing" >&2
  exit 1
fi
echo "   camera command: $(command -v "$CAM_CMD")"

echo "-- detected cameras:"
if ! "${CAM_CMD%-still}-hello" --list-cameras 2>&1 | sed 's/^/   /'; then
  echo "!! no camera detected — check the CSI ribbon and camera config" >&2
  exit 1
fi

# --- 3. Project virtualenv -------------------------------------------------
# Editable install; extras kept minimal here (Phase 1 = capture). Analysis/serial/
# web extras are added per phase (see pyproject.toml).
VENV=.venv
echo "-- creating virtualenv at $VENV and installing the package"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -e ".[dev]" -q

echo
echo "== Setup complete =="
echo "   camera: $CAM_CMD"
echo "   venv:   $VENV  (activate with: source $VENV/bin/activate)"
echo "   verify: $VENV/bin/python -m pytest -q"
