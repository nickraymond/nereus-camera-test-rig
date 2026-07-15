"""Unit tests for multi-camera experiment coordination — Spec §11, §13 (Phase 5).

No hardware: fake ``CameraDevice`` drivers are registered under the real driver
names. A "disconnected" board is simulated by an adapter that returns a failed
``CaptureResult`` with code ``device_not_found`` — exactly what the real
``OpenMvUsbCamera`` returns when its serial can't be resolved to a port. The
reference-card analysis runs for real against the V2 fixture (card present) and
against a blank frame (no card — the expected state during mechanical bring-up).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("cv2")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from nereus_camera_test_rig.cameras import registry  # noqa: E402
from nereus_camera_test_rig.cameras.base import CameraDevice  # noqa: E402
from nereus_camera_test_rig.capture.coordinator import run_experiment  # noqa: E402
from nereus_camera_test_rig.models import CameraIdentity, CaptureResult  # noqa: E402
from nereus_camera_test_rig.storage.checksums import sha256_bytes, sha256_file  # noqa: E402

WHEN = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)
FIXTURE = Path("tests/fixtures/reference_card/Nereus_Reef_Reference_Card_V2.png")
N6_SERIAL = "N6SERIAL"
AE3_SERIAL = "AE3SERIAL"


def _jpeg_of(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return buf.tobytes()


def _card_jpeg() -> bytes:
    img = cv2.imread(str(FIXTURE))
    assert img is not None, f"missing fixture {FIXTURE}"
    return _jpeg_of(img)


def _blank_jpeg() -> bytes:
    return _jpeg_of(np.full((720, 1280, 3), 127, np.uint8))


class FakeCamera(CameraDevice):
    """A CameraDevice that writes canned bytes, or fails, without any hardware."""

    driver = "fake"

    def __init__(self, *, source_bytes=None, fail_code=None, **kwargs):
        self._source = source_bytes
        self._fail_code = fail_code

    def get_device_info(self):
        return {}

    def configure(self, settings):
        pass

    def capture_image(self, destination, request):
        identity = CameraIdentity(driver="fake", platform="test")
        if self._fail_code:
            return CaptureResult(
                camera=identity, request=request, status="failed",
                error={"code": self._fail_code, "message": "simulated disconnect"},
            )
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._source)
        return CaptureResult(
            camera=identity, request=request, status="completed",
            output_path=str(dest), size_bytes=len(self._source),
            sha256=sha256_bytes(self._source), image_format="jpeg",
        )

    def capture_video(self, destination, request):
        identity = CameraIdentity(driver="fake", platform="test")
        return CaptureResult(
            camera=identity, request=request, status="failed",
            error={"code": "not_supported", "message": "fake"},
        )

    def health_check(self):
        return {"healthy": True}


def _register(imx_bytes, n6_bytes, *, ae3_disconnected):
    """Register fake drivers under the real driver names (build_camera won't clobber)."""
    registry.clear()
    registry.register("imx708", lambda settings=None, **kw: FakeCamera(source_bytes=imx_bytes))

    def openmv_factory(*, serial_number=None, board=None, settings=None):
        if serial_number == AE3_SERIAL and ae3_disconnected:
            return FakeCamera(fail_code="device_not_found")
        return FakeCamera(source_bytes=n6_bytes if serial_number == N6_SERIAL else imx_bytes)

    registry.register("openmv_usb", openmv_factory)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _config():
    return {
        "rig": {"id": "test-rig", "results_directory": "unused"},
        "cameras": {
            "imx708": {"enabled": True, "driver": "imx708"},
            "openmv_n6": {"enabled": True, "driver": "openmv_usb", "board": "n6",
                          "serial_number": N6_SERIAL},
            "openmv_ae3": {"enabled": True, "driver": "openmv_usb", "board": "ae3",
                           "serial_number": AE3_SERIAL},
        },
        "analysis": {"apriltag": {"enabled": True, "expected_tag_ids": [0, 1, 2, 3]}},
    }


def test_partial_failure_retains_others(tmp_path):
    """A disconnected AE3 must not delete or block the IMX708 + N6 (Spec §11, §12)."""
    card = _card_jpeg()
    _register(card, card, ae3_disconnected=True)

    out = run_experiment(_config(), "reference_card", environment_label="bench",
                         results_root=tmp_path, when=WHEN)

    assert out.status == "partial"
    by_name = {c.camera_name: c for c in out.camera_outcomes}

    # The two connected cameras produced valid, checksum-matching stills + analysis.
    for name in ("imx708", "openmv_n6"):
        c = by_name[name]
        assert c.ok
        img = Path(c.image_path)
        assert img.is_file() and img.stat().st_size > 0
        cap = json.loads(Path(c.metadata_path).read_text())
        assert cap["status"] == "completed"
        assert cap["output"]["sha256"] == sha256_file(img)  # checksum is trustworthy
        # Reference card present -> analysis passes and writes a nonempty crop (§13).
        assert c.analysis is not None and c.analysis.status == "pass"
        assert c.analysis.all_expected_tags_found
        crop = Path(c.analysis_dir) / "card_crop.jpg"
        assert crop.is_file() and crop.stat().st_size > 0

    # The disconnected board is marked failed, but its slot + the folder survive.
    ae3 = by_name["openmv_ae3"]
    assert not ae3.ok
    assert ae3.result.error["code"] == "device_not_found"
    assert ae3.image_path is None
    assert not (out.paths.analysis_root / "openmv_ae3" / "detection.json").exists()

    # Experiment record + logs are intact and name the failure (Spec §13, §16).
    assert out.paths.root.is_dir()
    record = json.loads(out.paths.record_path.read_text())
    assert any("openmv_ae3" in e for e in record["errors"])
    assert out.paths.log_path.is_file() and out.paths.log_path.stat().st_size > 0


def test_analysis_runs_without_card_is_not_a_failure(tmp_path):
    """No reference card in frame: capture succeeds, analysis reports fail, run is completed."""
    blank = _blank_jpeg()
    _register(blank, blank, ae3_disconnected=False)

    out = run_experiment(_config(), "reference_card", results_root=tmp_path, when=WHEN)

    # Every camera captured -> the *run* is completed even though no card was found.
    assert out.status == "completed"
    for c in out.camera_outcomes:
        assert c.ok
        assert c.analysis is not None
        assert c.analysis.status == "fail"
        assert c.analysis.tags_detected == []
        assert c.analysis.card_crop_created is False
        # detection.json is still written (analysis ran); no crop image is produced.
        det = Path(c.analysis_dir) / "detection.json"
        assert det.is_file()
        assert not (Path(c.analysis_dir) / "card_crop.jpg").exists()


def test_camera_subset_and_no_analysis(tmp_path):
    """--cameras subset is honored and --no-analysis skips the analysis pass."""
    blank = _blank_jpeg()
    _register(blank, blank, ae3_disconnected=False)

    out = run_experiment(_config(), "reference_card", results_root=tmp_path, when=WHEN,
                         camera_names=["imx708"], analysis=False)

    assert [c.camera_name for c in out.camera_outcomes] == ["imx708"]
    assert out.status == "completed"
    assert out.camera_outcomes[0].analysis is None
    assert not out.paths.analysis_root.exists()
