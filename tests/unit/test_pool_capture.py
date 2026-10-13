"""Unit tests for the pool capture loop helpers (scripts/pool_capture.py) and the Mac pull's
verification (host_tools/pool_pull.py). No hardware: synthetic set folders only."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from host_tools import pool_pull

_SPEC = importlib.util.spec_from_file_location(
    "pool_capture", Path(__file__).resolve().parents[2] / "scripts" / "pool_capture.py")
pool_capture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pool_capture)


def _capture(board_or_sensor: str, path: str, size: int, sha: str, meta: dict) -> dict:
    cam = {"sensor": "imx708"} if board_or_sensor == "imx708" else {"board": board_or_sensor,
                                                                     "firmware": "5.0.1"}
    return {"camera": cam, "status": "completed", "output_path": path, "size_bytes": size,
            "sha256": sha, "duration_seconds": 3.21, "sensor_metadata": meta}


def _make_set(root: Path, fail_ae3_raw: bool = False) -> Path:
    exp = root / "2026-10-14" / "exp_20261014T170000Z_pool"
    files = {"imx708/imx708_image_x.jpg": b"j" * 100, "imx708/imx708_raw_x.dng": b"d" * 200,
             "imx708/imx708_raw_x.jpg": b"k" * 50, "openmv_n6/openmv_n6_image_x.jpg": b"n" * 30,
             "openmv_n6/openmv_n6_raw_x.bayer": b"b" * 64, "openmv_ae3/openmv_ae3_image_x.jpg":
             b"a" * 20, "openmv_ae3/openmv_ae3_raw_x.bayer": b"c" * 64}
    for rel, data in files.items():
        p = exp / "captures" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    sha = {rel: pool_pull._sha256(exp / "captures" / rel) for rel in files}
    pi = "/home/pi/x/captures/"
    rec = {
        "captures": [
            _capture("imx708", pi + "imx708/imx708_image_x.jpg", 100,
                     sha["imx708/imx708_image_x.jpg"], {"ExposureTime": 1000}),
            _capture("n6", pi + "openmv_n6/openmv_n6_image_x.jpg", 30,
                     sha["openmv_n6/openmv_n6_image_x.jpg"], {"exposure_us": 8000}),
            _capture("ae3", pi + "openmv_ae3/openmv_ae3_image_x.jpg", 20,
                     sha["openmv_ae3/openmv_ae3_image_x.jpg"], {"exposure_us": 9000})],
        "raw_captures": [
            _capture("imx708", pi + "imx708/imx708_raw_x.dng", 200,
                     sha["imx708/imx708_raw_x.dng"],
                     {"LensPosition": 0.8, "ColourGains": [1.5, 2.0], "jpeg_path":
                      pi + "imx708/imx708_raw_x.jpg", "junk": 1}),
            _capture("n6", pi + "openmv_n6/openmv_n6_raw_x.bayer", 64,
                     sha["openmv_n6/openmv_n6_raw_x.bayer"], {"cfa": "BGGR", "gain_db": 6.0}),
            _capture("ae3", pi + "openmv_ae3/openmv_ae3_raw_x.bayer", 64,
                     sha["openmv_ae3/openmv_ae3_raw_x.bayer"], {"cfa": "BGGR"})],
    }
    if fail_ae3_raw:
        rec["raw_captures"][2] = {"camera": {"board": "ae3"}, "status": "failed",
                                  "error": {"code": "timeout", "message": "x"}}
    (exp / "experiment.json").write_text(json.dumps(rec))
    return exp


def test_summarize_set_records_files_and_applied_settings(tmp_path):
    exp = _make_set(tmp_path)
    s = pool_capture.summarize_set(exp)
    assert set(s) == {"imx708", "openmv_n6", "openmv_ae3"}
    raw = s["imx708"]["raw"]
    assert raw["file"] == "imx708_raw_x.dng" and raw["jpeg"] == "imx708_raw_x.jpg"
    assert raw["applied"] == {"LensPosition": 0.8, "ColourGains": [1.5, 2.0]}  # junk dropped
    assert s["openmv_n6"]["raw"]["firmware"] == "5.0.1"


def test_sha256sums_cover_every_file_and_pull_verifies(tmp_path):
    exp = _make_set(tmp_path)
    total = pool_capture.write_sha256sums(exp)
    lines = (exp / "SHA256SUMS").read_text().splitlines()
    assert len(lines) == 8 and total > 0  # 7 captures + experiment.json
    assert any(line.endswith("  captures/imx708/imx708_raw_x.jpg") for line in lines)
    ok = pool_pull.verify_set(exp)
    assert ok["verified"] == 8 and not ok["bad"] and not ok["missing"]
    (exp / "captures/openmv_n6/openmv_n6_raw_x.bayer").write_bytes(b"corrupt")
    (exp / "captures/imx708/imx708_raw_x.jpg").unlink()
    bad = pool_pull.verify_set(exp)
    assert "captures/openmv_n6/openmv_n6_raw_x.bayer" in bad["bad"]
    assert "captures/imx708/imx708_raw_x.jpg" in bad["missing"]


def test_set_without_sha256sums_is_unverified_but_record_checked(tmp_path):
    exp = _make_set(tmp_path)
    r = pool_pull.verify_set(exp)
    assert r["unverified"] and not r["bad"] and not r["missing"]


def _line(cams: dict) -> dict:
    return {"set": 7, "run_s": 600.0, "clock": {"uptime_s": 900.0}, "set_seconds": 41.2,
            "cameras": cams, "cma_free_mb": 90,
            "pi_after": {"cpu_temp_c": 51.0, "throttled": "0x0", "mem_available_mb": 210,
                         "disk_free_gb": 55.1},
            "power": {"vin_mv": 4950, "vbat_mv": 3380, "w_out": 3.9, "on_battery": False}}


def test_health_text_ok_and_partial(tmp_path):
    good = pool_capture.summarize_set(_make_set(tmp_path / "a"))
    text = pool_capture.health_text(_line(good))
    assert text.startswith("[set 007 | run+ 10m | boot+15m] OK 3/3")
    assert "4.95Vin" in text and "cma 90MB" in text
    part = pool_capture.summarize_set(_make_set(tmp_path / "b", fail_ae3_raw=True))
    text = pool_capture.health_text(_line(part))
    assert "PARTIAL 2/3" in text and "ae3 FAIL(still ok/raw timeout)" in text


def test_storage_check_plans_sets_and_respects_max_sets(tmp_path):
    args = argparse.Namespace(hours=1.0, plan_set_s=40.0, interval_s=0.0, reserve_gb=2.0,
                              max_sets=0)
    s = pool_capture.storage_check(tmp_path, args)
    assert s["planned_sets"] == 91
    assert s["need_gb"] == round(91 * pool_capture.SET_MB_PLAN / 1024 + 2.0, 2)
    args.max_sets = 10
    assert pool_capture.storage_check(tmp_path, args)["planned_sets"] == 10
    args.reserve_gb = 1e9
    assert pool_capture.storage_check(tmp_path, args)["ok"] is False


def test_backoff_benches_a_failing_board_but_never_the_imx708():
    fails = {"imx708": 9, "openmv_n6": 0, "openmv_ae3": 2}
    cams, benched = pool_capture.cameras_for_set(7, fails, retry_every=5)
    assert cams == ["imx708", "openmv_n6"] and benched == ["openmv_ae3"]
    cams, benched = pool_capture.cameras_for_set(10, fails, retry_every=5)  # retry set
    assert cams == ["imx708", "openmv_n6", "openmv_ae3"] and benched == []
    fails["openmv_ae3"] = 1
    assert pool_capture.cameras_for_set(7, fails, 5)[1] == []


def test_health_text_marks_benched(tmp_path):
    line = _line(pool_capture.summarize_set(_make_set(tmp_path)))
    line["benched"] = ["openmv_ae3"]
    del line["cameras"]["openmv_ae3"]
    assert "ae3 benched" in pool_capture.health_text(line)


def test_power_cycle_only_for_a_persistently_failing_board_after_warmup():
    fails = {"imx708": 7, "openmv_n6": 1, "openmv_ae3": 3}
    assert pool_capture.should_power_cycle(fails, 3, uptime_s=900) == ["openmv_ae3"]
    assert pool_capture.should_power_cycle(fails, 3, uptime_s=300) == []  # boot-loop guard
    assert pool_capture.should_power_cycle(fails, 0, uptime_s=900) == []  # disabled
    assert pool_capture.should_power_cycle({"imx708": 50}, 3, uptime_s=900) == []  # never IMX


def test_light_pull_defers_dngs_and_render_picks_them(tmp_path):
    run = tmp_path / "pool009_x"
    exp = _make_set(run)
    pool_capture.write_sha256sums(exp)
    (exp / "captures/imx708/imx708_raw_x.dng").unlink()  # as after --no-dng
    r = pool_pull.verify_set(exp, deferred=(".dng",))
    assert r["deferred"] == 1 and not r["missing"] and not r["bad"]
    assert pool_pull.verify_set(exp)["missing"] == ["captures/imx708/imx708_raw_x.dng",
                                                    "imx708_raw_x.dng"]
    render = tmp_path / "pool009_x_render"
    render.mkdir()
    (render / "render_manifest.json").write_text(json.dumps({"selected": {
        "imx708": [f"pool009_x/{exp.name}/imx708_raw_x.jpg"], "openmv_n6": []}}))
    assert pool_pull.dng_list_for_render(render, tmp_path) == {
        "pool009_x": [f"2026-10-14/{exp.name}/captures/imx708/imx708_raw_x.dng"]}
