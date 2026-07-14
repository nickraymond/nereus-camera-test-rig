"""Unit tests for capture.naming — Spec §12, §13."""

from __future__ import annotations

from datetime import datetime

import pytest

from nereus_camera_test_rig.capture import naming


def test_utc_timestamp_format(fixed_utc):
    assert naming.utc_timestamp(fixed_utc) == "20260714T180000Z"


def test_utc_timestamp_rejects_naive_datetime():
    with pytest.raises(ValueError):
        naming.utc_timestamp(datetime(2026, 7, 14, 18, 0, 0))


def test_utc_timestamp_converts_to_utc():
    from datetime import timedelta, timezone

    # 20:00 at +02:00 == 18:00 UTC
    aware = datetime(2026, 7, 14, 20, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert naming.utc_timestamp(aware) == "20260714T180000Z"


def test_slugify():
    assert naming.slugify("Reference Card / Above Water!") == "reference_card_above_water"
    assert naming.slugify("  Low-Light  ") == "low_light"


def test_experiment_id(fixed_utc):
    assert (
        naming.experiment_id("reference_card_above_water", fixed_utc)
        == "exp_20260714T180000Z_reference_card_above_water"
    )


def test_experiment_id_without_type(fixed_utc):
    assert naming.experiment_id("", fixed_utc) == "exp_20260714T180000Z"


def test_date_folder(fixed_utc):
    assert naming.date_folder(fixed_utc) == "2026-07-14"


def test_capture_filename(fixed_utc):
    assert (
        naming.capture_filename("imx708", "image", ".jpg", fixed_utc)
        == "imx708_image_20260714T180000Z.jpg"
    )
