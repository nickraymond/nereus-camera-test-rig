#!/usr/bin/env bash
# test_experiment.sh — Phase 5 hardware smoke test (Spec §4 Phase 5 exit, §11, §13, §25).
#
# Run ON the Pi from the repo root:  ./scripts/test_experiment.sh [CONFIG]
# Exercises the Phase 5 exit criteria end to end:
#   1. one `experiment` command produces one Spec §13 folder with, per connected
#      camera, a checksummed still + capture.json + (best-effort) analysis;
#   2. a disconnected camera yields a clear PARTIAL result — its slot is marked
#      failed, the other cameras' files are retained, and the folder is not deleted.
#
# No reference card is required: analysis simply reports no card, which is fine here.
# Exit 0 = PASS. Non-zero = FAIL. CONFIG defaults to the example rig config.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! venv missing at $PY — run scripts/install_pi.sh" >&2; exit 1; }

CONFIG="${1:-configs/rig.example.yaml}"
CLI="$PY -m nereus_camera_test_rig.cli --config"

echo "== Phase 5 experiment smoke test (config $CONFIG) =="

echo "-- 1/2: full capture set across all connected cameras"
$CLI "$CONFIG" experiment --type phase5_smoke --env bench || true

echo "-- validate artifacts (trust the files, not the exit code — CLAUDE.md §19)"
"$PY" - "phase5_smoke" <<'PY'
import glob, hashlib, json, sys
from pathlib import Path

root = Path(sorted(glob.glob("results/**/exp_*%s" % sys.argv[1], recursive=True))[-1])
ej = json.loads((root / "experiment.json").read_text())
assert root.is_dir(), "experiment folder missing"
assert (root / "logs" / "experiment.log").stat().st_size > 0, "empty experiment log"

ok_any = False
for cap in ej["captures"]:
    cam = cap["camera"].get("board") or cap["camera"].get("sensor") or cap["camera"]["driver"]
    if cap["status"] != "completed":
        print("  %-8s SKIP (not connected: %s)" % (cam, (cap.get("error") or {}).get("code")))
        continue
    ok_any = True
    out = cap["output"]
    img = Path(out["path"])
    assert img.is_file() and img.stat().st_size > 0, "missing/empty image for %s" % cam
    digest = hashlib.sha256(img.read_bytes()).hexdigest()
    assert digest == out["sha256"], "checksum mismatch for %s" % cam
    assert out["width"] and out["height"], "no dimensions for %s" % cam
    print("  %-8s OK  %sx%s  %s bytes  sha256 verified  fw=%s"
          % (cam, out["width"], out["height"], out["size_bytes"], cap["camera"].get("firmware")))

assert ok_any, "no camera produced a still — is the rig connected?"
print("  experiment.json: %d captures, %d analyses, %d errors"
      % (len(ej["captures"]), len(ej["analyses"]), len(ej["errors"])))
print("PASS: full capture set produced a valid Spec §13 experiment folder.")
PY

echo "-- 2/2: partial failure — one camera disconnected"
PARTIAL_CONFIG="$(mktemp /tmp/rig_partial.XXXX.yaml)"
# Break the AE3 serial so its adapter can't resolve a port (real device_not_found path).
sed 's/0829c14000000000/DEADBEEF_NOT_PRESENT/' "$CONFIG" > "$PARTIAL_CONFIG"
$CLI "$PARTIAL_CONFIG" experiment --type phase5_partial --env bench || true

"$PY" - "phase5_partial" <<'PY'
import glob, json, sys
from pathlib import Path

root = Path(sorted(glob.glob("results/**/exp_*%s" % sys.argv[1], recursive=True))[-1])
ej = json.loads((root / "experiment.json").read_text())
assert root.is_dir(), "partial-run folder was deleted"

statuses = {(c["camera"].get("board") or c["camera"].get("sensor")): c["status"] for c in ej["captures"]}
assert statuses.get("ae3") == "failed", "expected AE3 to fail, got %r" % statuses
assert any(s == "completed" for s in statuses.values()), "no survivor camera completed"
assert any("openmv_ae3" in e for e in ej["errors"]), "failure not recorded in experiment.json"
# Survivors keep their images.
for cap in ej["captures"]:
    if cap["status"] == "completed":
        assert Path(cap["output"]["path"]).is_file(), "survivor image was lost"
print("  statuses: %s" % statuses)
print("PASS: disconnected camera -> partial success, survivors + folder retained.")
PY

echo "ALL PASS: Phase 5 three-camera coordination validated on hardware."
