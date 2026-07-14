"""Unit tests for config loading + validation — Spec §12."""

from __future__ import annotations

import textwrap

import pytest

from nereus_camera_test_rig import config


def _write(tmp_path, text):
    p = tmp_path / "rig.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_load_valid_rig_config(tmp_path):
    p = _write(
        tmp_path,
        """
        rig:
          id: nereus-camera-rig-001
          results_directory: ./results
        cameras:
          imx708:
            enabled: true
            driver: imx708
          openmv_n6:
            enabled: false
            driver: openmv_usb
            board: n6
        analysis:
          apriltag:
            enabled: true
            expected_tag_ids: [0, 1, 2, 3]
        web:
          host: 0.0.0.0
          port: 8080
        """,
    )
    cfg = config.load_rig_config(p)
    assert cfg["rig"]["id"] == "nereus-camera-rig-001"
    assert set(cfg["cameras"]) == {"imx708", "openmv_n6"}


def test_validate_accepts_minimal(minimal_rig_config):
    assert config.validate_rig_config(minimal_rig_config) is minimal_rig_config


def test_missing_file_raises(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load_rig_config(tmp_path / "nope.yaml")


def test_non_mapping_root_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(config.ConfigError, match="must be a mapping"):
        config.load_yaml(p)


def test_missing_required_section_raises():
    with pytest.raises(config.ConfigError, match="cameras"):
        config.validate_rig_config({"rig": {"id": "x"}})


def test_rig_without_id_raises():
    with pytest.raises(config.ConfigError, match="'id'"):
        config.validate_rig_config({"rig": {}, "cameras": {"imx708": {"driver": "imx708"}}})


def test_camera_without_driver_raises():
    with pytest.raises(config.ConfigError, match="driver"):
        config.validate_rig_config({"rig": {"id": "x"}, "cameras": {"imx708": {}}})


def test_empty_cameras_raises():
    with pytest.raises(config.ConfigError, match="non-empty"):
        config.validate_rig_config({"rig": {"id": "x"}, "cameras": {}})


def test_enabled_cameras_filters(minimal_rig_config):
    enabled = config.enabled_cameras(minimal_rig_config)
    assert set(enabled) == {"imx708"}  # openmv_n6 is disabled


def test_example_config_is_valid():
    # The shipped example must always be loadable + valid.
    cfg = config.load_rig_config("configs/rig.example.yaml")
    assert cfg["rig"]["id"]
    assert "imx708" in cfg["cameras"]
