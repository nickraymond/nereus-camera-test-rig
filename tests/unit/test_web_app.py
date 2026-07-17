"""Unit tests for the Phase 6 web app — Spec §14.

No hardware and no live server: fake ``CameraDevice`` drivers are registered
under the real driver names (same pattern as ``test_coordinator.py``), real
experiment folders are produced by the real coordinator into ``tmp_path``, and
the pages are exercised through Flask's test client. The reference-card
analysis runs for real against the V2 fixture so the comparison view, color
check, and cut sheet are tested against genuine §13 artifacts.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("flask")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from nereus_camera_test_rig.cameras import registry  # noqa: E402
from nereus_camera_test_rig.cameras.base import CameraDevice  # noqa: E402
from nereus_camera_test_rig.capture.coordinator import run_experiment  # noqa: E402
from nereus_camera_test_rig.models import CameraIdentity, CaptureResult  # noqa: E402
from nereus_camera_test_rig.storage.checksums import sha256_bytes  # noqa: E402
from nereus_camera_test_rig.web import results_reader  # noqa: E402
from nereus_camera_test_rig.web.app import create_app  # noqa: E402
from nereus_camera_test_rig.web.color_check import check_card_crop  # noqa: E402

WHEN = datetime(2026, 7, 16, 9, 41, 7, tzinfo=timezone.utc)
FIXTURE = Path("tests/fixtures/reference_card/Nereus_Reef_Reference_Card_V2.png")
N6_SERIAL = "N6SERIAL"
AE3_SERIAL = "AE3SERIAL"


def _jpeg_of(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return buf.tobytes()


def _card_scene() -> np.ndarray:
    img = cv2.imread(str(FIXTURE))
    assert img is not None, f"missing fixture {FIXTURE}"
    return cv2.copyMakeBorder(img, 200, 200, 300, 300, cv2.BORDER_CONSTANT, value=(120, 96, 60))


def _warm_variant(scene: np.ndarray) -> np.ndarray:
    warm = scene.astype(np.float32)
    warm[:, :, 2] *= 1.12  # BGR: boost red for a warm cast
    warm[:, :, 0] *= 0.92
    return np.clip(warm, 0, 255).astype(np.uint8)


class FakeCamera(CameraDevice):
    driver = "fake"

    def __init__(self, *, source_bytes=None, fail_code=None):
        self._source = source_bytes
        self._fail_code = fail_code

    def get_device_info(self):
        return {}

    def configure(self, settings):
        pass

    def health_check(self):
        return {"healthy": self._fail_code is None, "driver": "fake"}

    def capture_image(self, destination, request):
        identity = CameraIdentity(driver="fake", platform="test", firmware="9.9.9")
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
            output_path=str(dest), width=1880, height=1370, image_format="jpeg",
            size_bytes=len(self._source), sha256=sha256_bytes(self._source),
            duration_seconds=1.5,
        )

    def capture_video(self, destination, request):
        identity = CameraIdentity(driver="fake", platform="test")
        return CaptureResult(
            camera=identity, request=request, status="failed",
            error={"code": "not_supported", "message": "fake"},
        )


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _register(*, ae3_disconnected=False):
    scene = _card_scene()
    imx_bytes = _jpeg_of(scene)
    ae3_bytes = _jpeg_of(_warm_variant(scene))
    registry.register("imx708", lambda settings=None, **kw: FakeCamera(source_bytes=imx_bytes))

    def openmv_factory(*, serial_number=None, board=None, settings=None):
        if serial_number == AE3_SERIAL:
            if ae3_disconnected:
                return FakeCamera(fail_code="device_not_found")
            return FakeCamera(source_bytes=ae3_bytes)
        return FakeCamera(source_bytes=imx_bytes)

    registry.register("openmv_usb", openmv_factory)


def _config(results_root: Path) -> dict:
    return {
        "rig": {"id": "test-rig", "results_directory": str(results_root)},
        "cameras": {
            "imx708": {"enabled": True, "driver": "imx708"},
            "openmv_n6": {"enabled": True, "driver": "openmv_usb", "board": "n6",
                          "serial_number": N6_SERIAL},
            "openmv_ae3": {"enabled": True, "driver": "openmv_usb", "board": "ae3",
                           "serial_number": AE3_SERIAL},
        },
        "analysis": {"apriltag": {"enabled": True, "expected_tag_ids": [0, 1, 2, 3]}},
        "web": {"host": "127.0.0.1", "port": 8080},
    }


@pytest.fixture
def rig(tmp_path):
    """Two real experiment folders (one completed, one partial) + a test client."""
    _register(ae3_disconnected=False)
    config = _config(tmp_path)
    full = run_experiment(config, "reference_card", environment_label="bench",
                          results_root=tmp_path, when=WHEN)

    registry.clear()
    _register(ae3_disconnected=True)
    partial = run_experiment(config, "reference_card", environment_label="bench",
                             results_root=tmp_path, when=WHEN - timedelta(hours=5))

    app = create_app(config)
    app.config["TESTING"] = True
    return {
        "client": app.test_client(),
        "root": tmp_path,
        "full": full,
        "partial": partial,
    }


def _detail_url(outcome) -> str:
    return f"/experiments/{outcome.paths.root.parent.name}/{outcome.paths.experiment_id}"


# ---------------------------------------------------------------- results_reader

def test_list_experiments_newest_first(rig):
    views = results_reader.list_experiments(rig["root"])
    assert [v.experiment_id for v in views] == [
        rig["full"].paths.experiment_id,
        rig["partial"].paths.experiment_id,
    ]
    assert views[0].status == "completed"
    assert views[1].status == "partial"
    assert views[0].cameras_ok == 3
    assert views[1].cameras_ok == 2


def test_reader_resolves_artifacts(rig):
    view = results_reader.load_experiment(
        rig["root"], rig["full"].paths.root.parent.name, rig["full"].paths.experiment_id
    )
    for cam in view.cameras:
        assert cam.ok
        assert cam.image_rel and (view.root / cam.image_rel).is_file()
        assert cam.annotated_rel and cam.crop_rel
        assert cam.detection["status"] == "pass"
        assert cam.output["sha256"]


def test_reader_rejects_traversal(rig):
    assert results_reader.load_experiment(rig["root"], "..", "x") is None
    assert results_reader.load_experiment(rig["root"], "2026-07-16", "../../etc") is None


# ---------------------------------------------------------------- pages

def test_index_lists_runs_with_status(rig):
    page = rig["client"].get("/experiments").get_data(as_text=True)
    assert rig["full"].paths.experiment_id in page
    assert rig["partial"].paths.experiment_id in page
    assert "completed" in page and "partial" in page


def test_detail_page_compares_cameras(rig):
    page = rig["client"].get(_detail_url(rig["full"])).get_data(as_text=True)
    for cam in ("IMX708", "OpenMV N6", "OpenMV AE3"):
        assert cam in page
    assert "annotated.jpg" in page and "card_crop.jpg" in page
    assert "Sharpness (Laplacian var)" in page
    assert 'class="swatch"' in page  # color check rendered
    assert "Download ZIP" in page


def test_detail_page_renders_partial_failure(rig):
    page = rig["client"].get(_detail_url(rig["partial"])).get_data(as_text=True)
    assert "device_not_found" in page
    assert "no capture" in page
    # Survivors still render their artifacts (Spec §11).
    assert "card_crop.jpg" in page


def test_status_page_renders(rig):
    page = rig["client"].get("/").get_data(as_text=True)
    assert "cameras healthy" in page
    assert "experiments stored" in page


# ---------------------------------------------------------------- color check

def test_color_check_flags_warm_cast(rig):
    view = results_reader.load_experiment(
        rig["root"], rig["full"].paths.root.parent.name, rig["full"].paths.experiment_id
    )
    by_name = {c.name: c for c in view.cameras}
    neutral = check_card_crop(by_name["imx708"].crop_abs)
    warm = check_card_crop(by_name["openmv_ae3"].crop_abs)
    assert neutral.grey_mean_delta_e < 3.0  # unmodified card ≈ design values
    assert abs(neutral.grey_cast_r_minus_b) < 3.0
    assert warm.grey_cast_r_minus_b > 8.0  # deliberately warmed variant
    assert warm.grey_mean_delta_e > neutral.grey_mean_delta_e
    assert len(neutral.patches) == 17


# ---------------------------------------------------------------- files & downloads

def test_file_route_serves_and_blocks_traversal(rig):
    url = _detail_url(rig["full"])
    ok = rig["client"].get(f"{url}/file/experiment.json")
    assert ok.status_code == 200 and b"experiment_id" in ok.data
    for bad in ("../../../etc/passwd", "..%2F..%2Fpyproject.toml"):
        assert rig["client"].get(f"{url}/file/{bad}").status_code == 404


def test_zip_contains_full_folder(rig):
    url = _detail_url(rig["full"])
    resp = rig["client"].get(f"{url}/download.zip")
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    exp_id = rig["full"].paths.experiment_id
    assert f"{exp_id}/experiment.json" in names
    assert any("captures/imx708/" in n and n.endswith(".jpg") for n in names)
    assert any(n.endswith("analysis/imx708/card_crop.jpg") for n in names)
    assert any(n.endswith("logs/experiment.log") for n in names)


def test_cutsheet_png_and_pdf(rig):
    url = _detail_url(rig["full"])
    png = rig["client"].get(f"{url}/cutsheet.png")
    assert png.status_code == 200 and png.data[:8] == b"\x89PNG\r\n\x1a\n"
    pdf = rig["client"].get(f"{url}/cutsheet.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"


# ---------------------------------------------------------------- new experiment

def test_new_experiment_post_runs_and_redirects(rig, tmp_path):
    resp = rig["client"].post(
        "/experiments/new",
        data={
            "experiment_type": "web_trigger",
            "environment": "bench",
            "notes": "from test",
            "cameras": ["imx708"],
            "analysis": "on",
        },
    )
    assert resp.status_code == 302
    assert "/experiments/" in resp.headers["Location"]
    # The §13 folder exists with the §13 artifacts.
    views = results_reader.list_experiments(rig["root"])
    triggered = [v for v in views if "web_trigger" in v.experiment_id]
    assert len(triggered) == 1
    assert triggered[0].cameras[0].ok
    assert (triggered[0].root / "experiment.json").is_file()


def test_unknown_experiment_404s(rig):
    assert rig["client"].get("/experiments/2026-01-01/exp_nope").status_code == 404
