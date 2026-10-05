"""Hardware test for OpenMV ``capture_raw`` on the N6 and the AE3 — Phase 8 S3 (OQ-21).

Runs on the Pi against real boards with the capture service deployed (see
``scripts/test_openmv_raw.sh``). Excluded from the default suite; skips a board that is not
connected. Every capture follows a ``reset_board``: required on the AE3 (one camera session
per boot on OpenMV v5, PR #70), harmless on the N6, and it gives both boards fresh 3A state.

Proves: the Bayer frame arrives with a matching SHA-256 and the exact W×H×bits size, the
sidecar carries what the reader needs (CFA, bits, black / white level, read-back exposure /
gain), ``read_openmv_bayer`` turns it into a ``RawFrame``, and a requested exposure / gain is
the one the sensor reports back (the lock check). Frames are kept under ``RAW_OUT`` when set,
for ``jxl-check`` in the smoke script.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("serial", reason="pyserial not installed (install the 'serial' extra)")

from host_tools.discover_openmv import find_port  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera  # noqa: E402
from nereus_camera_test_rig.color.raw_io import read_openmv_bayer  # noqa: E402
from nereus_camera_test_rig.models import CaptureRequest  # noqa: E402

BOARDS = {
    "n6": os.environ.get("N6_SERIAL", "020023000450433547373200"),
    "ae3": os.environ.get("AE3_SERIAL", "0829c14000000000"),
}
# Lock-check target: well inside the range the probe reached (80 us .. metered ~6 ms), and
# the measured minimum analogue gain (0 dB reads back 3.152157, OQ-21).
LOCK_EXPOSURE_US = 2000
MIN_GAIN_DB = 3.152157


def _out_dir(tmp_path):
    out = os.environ.get("RAW_OUT")
    return Path(out) if out else tmp_path


def _capture(board, dest, settings):
    serial = BOARDS[board]
    if not find_port(serial):
        pytest.skip("%s (serial %s) not connected" % (board, serial))
    cam = OpenMvUsbCamera(serial_number=serial, board=board)
    try:
        cam.reset_board()
        return cam.capture_raw(str(dest), CaptureRequest(kind="image", settings=settings))
    finally:
        cam.close()


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_capture_raw_metered(board, tmp_path):
    dest = _out_dir(tmp_path) / ("%s_metered.bayer" % board)
    result = _capture(board, dest, {"warmup_ms": 2000})
    assert result.ok, result.error
    assert result.image_format == "bayer"
    assert dest.stat().st_size == 1280 * 800  # 8-bit HD
    side = json.loads(dest.with_suffix(".json").read_text())
    assert side["sha256"] == result.sha256
    assert (side["cfa"], side["bits"], side["black_level"], side["white_level"]) == (
        "BGGR", 8, 0, 255)
    assert side["camera"]["board"] == board and side["camera"]["firmware"]
    assert side["exposure_us"] > 0 and side["gain_db"] is not None
    frame = read_openmv_bayer(dest)
    assert frame.mosaic.shape == (800, 1280) and frame.cfa == "BGGR"
    # A real scene, not a blank or saturated buffer.
    assert 5 < float(frame.mosaic.mean()) < 250


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_capture_raw_locked_exposure(board, tmp_path):
    dest = _out_dir(tmp_path) / ("%s_locked.bayer" % board)
    result = _capture(board, dest, {"warmup_ms": 500, "exposure_us": LOCK_EXPOSURE_US,
                                    "gain_db": MIN_GAIN_DB})
    assert result.ok, result.error
    meta = result.sensor_metadata
    assert meta["requested"] == {"exposure_us": LOCK_EXPOSURE_US, "gain_db": MIN_GAIN_DB}
    # The sensor quantizes exposure to its line time; allow 5 %.
    assert abs(meta["exposure_us"] - LOCK_EXPOSURE_US) <= 0.05 * LOCK_EXPOSURE_US, meta
    assert meta["gain_db"] == pytest.approx(MIN_GAIN_DB, abs=0.05), meta


@pytest.mark.parametrize("board", sorted(BOARDS))
def test_capture_raw_long_exposure(board, tmp_path):
    """Past the default frame time (N6 8.2 ms, AE3 16.6 ms at HD Bayer) the board lengthens
    the frame through the PAG7936 registers (OQ-51) and the exposure reads back exactly."""
    dest = _out_dir(tmp_path) / ("%s_long.bayer" % board)
    result = _capture(board, dest, {"warmup_ms": 300, "exposure_us": 50000,
                                    "gain_db": MIN_GAIN_DB})
    assert result.ok, result.error
    meta = result.sensor_metadata
    assert abs(meta["exposure_us"] - 50000) <= 0.05 * 50000, meta
    assert meta["frame_time_us"] == 55000, meta
