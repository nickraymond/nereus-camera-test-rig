"""md19_check.py: rev 15 must do 19's CMA + capture bars from bench folders + the kernel log."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1].parent
_SPEC = importlib.util.spec_from_file_location("md19_check_t", ROOT / "scripts" / "md19_check.py")
md19 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(md19)

FIRES = (1000.0, 1100.0, 1200.0)  # clip fires on CLOCK_MONOTONIC, 30 s each


def _row(cam, **over):
    r = {"camera": cam, "ok": True, "error": None, "host_min_mem_avail_mb": 128,
         "host_min_cma_free_mb": 0}
    r.update({"fps_clip": 26.8, "max_gap_ms": 66.7, "frames_lost": 95} if cam == "imx708"
             else {"fps_clip": 10.03, "max_gap_ms": 100.0, "frames_lost": 0})
    r.update(over)
    return r


def _label(tmp_path, rows_per_clip=None, void=(False, False, False)):
    d = tmp_path / "md19_buf4"
    clips = []
    for k, fire in enumerate(FIRES):
        c = d / f"clip0{k + 1}"
        c.mkdir(parents=True)
        (c / "video_sync.json").write_text(json.dumps(
            {"seconds": 30.0, "fire_host_mono": {"imx708": fire, "openmv_n6": fire + 0.001,
                                                 "openmv_ae3": fire + 0.002}}))
        rows = (rows_per_clip or {}).get(k) or [_row(c_) for c_ in
                                                ("imx708", "openmv_n6", "openmv_ae3")]
        clips.append({"clip": c.name, "misses": [], "void": void[k], "rows": rows})
    (d / "setting.json").write_text(json.dumps(
        {"label": "md19_buf4", "build": "4441175", "sync_args": ["--imx-buffers", "4"],
         "clips": clips}))
    return d


BOOT = "[    0.000000] Booting Linux on physical CPU 0x0\n" \
       "[    0.000000] cma: Reserved 256 MiB at 0x000000000b000000\n"


def _check(d, journal_text, now=5000.0):
    return md19.check_label(d, md19.parse_journal(journal_text), now, debugfs=False)


def test_clean_setting_passes_and_says_driver_cma_is_unknown(tmp_path):
    res = _check(_label(tmp_path), BOOT + "[ 1010.123456] usb 1-1.2: reset high-speed USB device\n")
    assert res["verdict"] == "pass" and res["cma_bar"] and res["capture_bar"]
    assert res["driver_held_cma"].startswith("unknown")
    assert all(r["alloc_errors"] == [] for r in res["clips"])
    assert res["clips"][0]["min_mem_avail_mb"] == 128 and res["clips"][0]["mem_flag"] is True


def test_a_cma_allocation_error_in_a_clip_window_fails_the_cma_bar_only(tmp_path):
    j = BOOT + ("[ 1115.000001] cma: cma_alloc: linux,cma: alloc failed, req-size: 2025 pages, "
                "ret: -12\n[ 1500.000000] cma: cma_alloc: alloc failed (outside every window)\n")
    res = _check(_label(tmp_path), j)
    assert res["verdict"] == "cma_fails" and res["capture_bar"] and not res["cma_bar"]
    assert len(res["clips"][1]["alloc_errors"]) == 1 and res["clips"][0]["alloc_errors"] == []


def test_boot_lines_outside_the_windows_are_ignored(tmp_path):
    res = _check(_label(tmp_path), BOOT + "[    1.2] Out of memory test line at boot\n")
    assert res["verdict"] == "pass"


def test_a_lost_board_frame_fails_the_capture_bar(tmp_path):
    rows = {2: [_row("imx708"), _row("openmv_n6", frames_lost=2), _row("openmv_ae3")]}
    res = _check(_label(tmp_path, rows), BOOT)
    assert res["verdict"] == "capture_fails" and res["clips"][2]["capture_misses"] == \
        ["openmv_n6:lost 2"]


def test_imx708_is_judged_by_fps_and_gap_not_its_nominal_lost_count(tmp_path):
    rows = {0: [_row("imx708", fps_clip=25.9), _row("openmv_n6"), _row("openmv_ae3")]}
    res = _check(_label(tmp_path, rows), BOOT)
    assert res["clips"][0]["capture_misses"] == ["imx708:fps_clip 25.9"]
    assert _check(_label(tmp_path / "b"), BOOT)["clips"][0]["capture_misses"] == []  # lost 95 ok


def test_a_reboot_since_the_clips_makes_it_unknown(tmp_path):
    # this boot's journal starts at 0 but the clock is only 300 s in: the clips weren't this boot
    res = _check(_label(tmp_path), BOOT, now=300.0)
    assert res["verdict"] == "unknown" and not res["capture_bar"]


def test_a_void_clip_leaves_fewer_than_three(tmp_path):
    res = _check(_label(tmp_path, void=(False, True, False)), BOOT)
    assert res["verdict"] == "unknown" and "2 non-void clips < 3" in res["unknown"]


def test_main_exit_codes_and_md19_json(tmp_path, monkeypatch):
    d = _label(tmp_path)
    j = tmp_path / "kern.txt"
    j.write_text(BOOT + "[ 1215.5] cma: cma_alloc: alloc failed, ret: -12\n")
    monkeypatch.setattr(md19.time, "monotonic", lambda: 5000.0)  # clock past the clips
    assert md19.main([d.name, "--bench-root", str(tmp_path), "--journal", str(j)]) == md19.EXIT_CMA
    assert json.loads((d / "md19.json").read_text())["verdict"] == "cma_fails"
    assert md19.main(["nope", "--bench-root", str(tmp_path), "--journal", str(j)]) == \
        md19.EXIT_USAGE


@pytest.mark.parametrize("line,hit", [
    ("cma: cma_alloc: linux,cma: alloc failed, req-size: 8100 pages, ret: -12", True),
    ("rpicam-vid: dma-buf allocation failed", True),
    ("unicam fe801000.csi: Failed to allocate DMA buffer", True),
    ("usb 1-1.3: new high-speed USB device number 5 using dwc2", False),
    ("cma: Reserved 256 MiB at 0x000000000b000000", False),
])
def test_error_line_patterns(line, hit):
    lines = md19.kernel_lines([(10.0, line)], (0.0, 20.0))
    assert bool(lines["alloc_errors"]) is hit
