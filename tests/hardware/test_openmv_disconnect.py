"""Hardware fault injection: the N6 reboots mid-capture — the experiment must carry on (§11).

Overnight HIL soak on ``nereus002`` (2026-09-29): in 9 of 134 cycles the N6 rebooted during
its capture, pyserial's "device reports readiness to read but returned no data" escaped the
adapter and the whole experiment aborted (no ``experiment.json``, the AE3 never captured).

Here a second handle on the N6's port sends the board a real ``reset_board`` just before the
adapter's capture, so the board reboots while the adapter waits for its reply — a real USB
port loss, no fakes. Expected: the N6 slot fails with a structured error, the AE3 still
captures, ``experiment.json`` is written, status ``partial``. Run on the rig with both boards
(stop Nick's workbench recipe first):
``PYTHONPATH=$PWD/src:$PWD .venv/bin/python -m pytest tests/hardware/test_openmv_disconnect.py``
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

serial = pytest.importorskip("serial", reason="pyserial not installed")

from host_tools.discover_openmv import find_port  # noqa: E402
from openmv.common import command_protocol as cp  # noqa: E402

from nereus_camera_test_rig import config as config_mod  # noqa: E402
from nereus_camera_test_rig.cameras import openmv_usb  # noqa: E402
from nereus_camera_test_rig.capture.coordinator import run_experiment  # noqa: E402

N6_SERIAL = "020023000450433547373200"


def _inject_reset(port: str) -> None:
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=5) as s:
        s.write(cp.encode_message(cp.make_request("reset_board", "fault-injection")))


def test_n6_reboot_mid_capture_keeps_the_experiment(monkeypatch):
    if not find_port(N6_SERIAL):
        pytest.skip("N6 not connected")
    original = openmv_usb.OpenMvUsbCamera.capture_image
    injected = []

    def capture_with_fault(self, destination, request):
        if self._serial_number == N6_SERIAL and not injected:
            injected.append(True)
            _inject_reset(self._port or find_port(N6_SERIAL))
        return original(self, destination, request)

    monkeypatch.setattr(openmv_usb.OpenMvUsbCamera, "capture_image", capture_with_fault)
    cfg = config_mod.load_rig_config(REPO / "configs" / "rig.example.yaml")
    outcome = run_experiment(cfg, "fault_n6_reboot", environment_label="hardware-test",
                             camera_names=["openmv_n6", "openmv_ae3"], analysis=False,
                             results_root=REPO / "results" / "hw_tests")

    assert injected, "fault was not injected"
    record = json.loads((outcome.paths.root / "experiment.json").read_text())
    by_board = {c["camera"]["board"]: c for c in record["captures"]}
    assert outcome.status == "partial", record["errors"]
    assert by_board["n6"]["status"] == "failed"
    assert by_board["n6"]["error"]["code"] in ("device_disconnected", "bad_transfer",
                                               "timeout"), by_board["n6"]["error"]
    assert by_board["ae3"]["status"] == "completed"
