"""Unit tests for shared data models — Spec §5, §13."""

from __future__ import annotations

from nereus_camera_test_rig.models import (
    CameraIdentity,
    CaptureRequest,
    CaptureResult,
    DetectionResult,
    ExperimentRecord,
)


def test_capture_result_ok_property():
    cam = CameraIdentity(driver="imx708", platform="raspberry_pi", sensor="imx708")
    req = CaptureRequest(kind="image")
    ok = CaptureResult(camera=cam, request=req, status="completed", output_path="x.jpg")
    bad = CaptureResult(
        camera=cam,
        request=req,
        status="failed",
        error={"code": "capture_failed", "message": "boom"},
    )
    assert ok.ok is True
    assert bad.ok is False


def test_capture_result_to_dict_roundtrips_nested():
    cam = CameraIdentity(
        driver="openmv_usb", platform="openmv", board="n6", device_id="openmv-n6-001"
    )
    req = CaptureRequest(kind="image", settings={"framesize": "native"})
    res = CaptureResult(camera=cam, request=req, status="completed", width=1280, height=720)
    d = res.to_dict()
    assert d["camera"]["board"] == "n6"
    assert d["request"]["settings"]["framesize"] == "native"
    assert d["width"] == 1280


def test_detection_result_defaults_match_spec_shape():
    det = DetectionResult(
        status="pass",
        tags_detected=[0, 1, 2, 3],
        expected_tags=[0, 1, 2, 3],
        all_expected_tags_found=True,
        card_crop_created=True,
        crop_width=1600,
        crop_height=900,
    )
    d = det.to_dict()
    # Field names mirror the Spec §13 JSON contract.
    for key in (
        "status",
        "tags_detected",
        "expected_tags",
        "all_expected_tags_found",
        "card_crop_created",
        "crop_width",
        "crop_height",
    ):
        assert key in d


def test_experiment_record_timestamp_defaults_empty():
    # Timestamps are set deliberately by the capture layer, not at construction.
    rec = ExperimentRecord(experiment_id="exp_20260714T180000Z_reference_card_above_water")
    assert rec.timestamp == ""
    assert rec.captures == []
    assert rec.to_dict()["experiment_id"].startswith("exp_")


def test_experiment_record_collects_cameras_and_captures():
    cam = CameraIdentity(driver="imx708", platform="raspberry_pi")
    req = CaptureRequest(kind="image")
    rec = ExperimentRecord(
        experiment_id="exp_x",
        cameras=[cam],
        captures=[CaptureResult(camera=cam, request=req, status="completed")],
    )
    d = rec.to_dict()
    assert len(d["cameras"]) == 1
    assert d["captures"][0]["status"] == "completed"
