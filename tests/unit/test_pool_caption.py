"""Unit tests for ``host_tools/pool_caption.py`` (caption bars appended below pool renders).

Synthetic run folders only: run_manifest.json + sets.jsonl (depth present / missing / failed,
torn last line), Mac-anchored vs Pi-clock times, PDT vs PST, long file names, pixel identity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from host_tools import pool_caption as pc

SET_A = "2026-10-13/exp_20261013T222549Z_pool"
SET_B = "2026-10-13/exp_20261013T222637Z_pool"
SET_C = "2026-10-13/exp_20261013T222724Z_pool"  # its sets.jsonl line is the torn one


def _reading(depth_m=None, run0=None, temp=20.0, ok=True) -> dict:
    if not ok:
        return {"ok": False, "error": "OSError: [Errno 121] Remote I/O error"}
    d = {"ok": True, "pressure_mbar": 1100.0, "temp_c": temp}
    if depth_m is not None:
        d["depth_m"] = depth_m
    if run0 is not None:
        d["depth_m_run_start"] = run0
    return d


def _line(experiment: str, pi_utc: str, depth=None) -> dict:
    line = {"set": 1, "experiment": experiment, "clock": {"pi_utc": pi_utc, "mono_s": 31.8}}
    if depth is not None:
        line["depth"] = depth
    return line


def _run(tmp_path: Path, lines: list, manifest: dict | None = None, torn: str = "") -> Path:
    run = tmp_path / "pool003_20261013T222547Z"
    for rel in (SET_A, SET_B, SET_C):
        (run / rel).mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (run / "run_manifest.json").write_text(json.dumps(manifest))
    text = "".join(json.dumps(x) + "\n" for x in lines) + torn
    (run / "sets.jsonl").write_text(text)
    return run


PI_START = "2026-10-13T22:25:48+00:00"


# --- run metadata ---------------------------------------------------------------------------

def test_depth_present_mean_of_ok_readings_and_torn_last_line_skipped(tmp_path):
    lines = [_line(SET_A, "2026-10-13T22:25:48+00:00",
                   {"start": _reading(1.20, 1.10, 20.0), "end": _reading(1.30, 1.20, 20.4)}),
             _line(SET_B, "2026-10-13T22:26:37+00:00",
                   {"start": _reading(0.80, temp=19.0), "end": _reading(ok=False)})]
    warnings = []
    run = _run(tmp_path, lines, {"start_clock": {"pi_utc": PI_START}},
               torn='{"set": 3, "experiment": "2026-10-13/exp_2026')
    meta = pc.load_run(run, warnings.append)
    assert meta.bad_lines == 1 and set(meta.sets) == {SET_A, SET_B}
    assert any("skipped 1 line" in w for w in warnings)

    a = pc.set_caption(meta, run / SET_A)
    assert (a.depth, a.water) == ("depth 1.25 m", "water 20.2 °C")  # depth_m preferred
    b = pc.set_caption(meta, run / SET_B)
    assert (b.depth, b.water) == ("depth 0.80 m", "water 19.0 °C")  # failed end reading ignored
    # the torn line's set: no line -> time from the folder name, depth n/a, and a warning
    c = pc.set_caption(meta, run / SET_C, warnings.append)
    assert (c.depth, c.water) == ("depth n/a", "water n/a")
    assert c.time == "2026-10-13 3:27:24 PM PDT (Pi clock, unanchored via folder name)"
    assert any("no sets.jsonl line" in w for w in warnings)
    assert meta.summary()["sets_with_depth"] == 2


@pytest.mark.parametrize("depth, want", [
    (None, ("depth n/a", "water n/a")),                                    # older run: no depth
    ({}, ("depth n/a", "water n/a")),
    ({"start": _reading(ok=False), "end": _reading(ok=False)}, ("depth n/a", "water n/a")),
    ({"start": _reading(run0=0.0, temp=18.0), "end": _reading(run0=0.5, temp=18.2)},
     ("depth 0.25 m vs run start", "water 18.1 °C")),
    ({"start": _reading(depth_m=-0.001), "end": _reading(depth_m=0.0)},
     ("depth 0.00 m", "water 20.0 °C")),                                   # never "-0.00"
    ({"start": {"ok": True, "pressure_mbar": 1000.0}}, ("depth n/a", "water n/a")),
])
def test_depth_fields(depth, want):
    line = _line(SET_A, PI_START, depth) if depth is not None else _line(SET_A, PI_START)
    assert pc.depth_fields(line) == want


def test_no_depth_at_all_like_pool002(tmp_path):
    run = _run(tmp_path, [_line(SET_A, "2026-10-13T22:25:48+00:00")],
               {"mac_time_at_start": None, "start_clock": {"pi_utc": PI_START}})
    cap = pc.set_caption(pc.load_run(run), run / SET_A)
    assert cap == pc.SetCaption("2026-10-13 3:25:48 PM PDT (Pi clock, unanchored)",
                                "depth n/a", "water n/a")


# --- time ---------------------------------------------------------------------------------------

def test_mac_anchored_vs_pi_clock(tmp_path):
    lines = [_line(SET_A, "2026-10-13T22:30:00+00:00")]
    pi_only = _run(tmp_path / "a", lines, {"mac_time_at_start": None,
                                           "start_clock": {"pi_utc": PI_START}})
    meta = pc.load_run(pi_only)
    assert meta.offset is None and meta.basis == "Pi clock, unanchored"
    assert pc.set_caption(meta, pi_only / SET_A).time == \
        "2026-10-13 3:30:00 PM PDT (Pi clock, unanchored)"

    # Mac clock 97 s ahead of the Pi clock at start (date -Iseconds format, with offset)
    anchored = _run(tmp_path / "b", lines, {"mac_time_at_start": "2026-10-13T15:27:25-07:00",
                                            "start_clock": {"pi_utc": PI_START}})
    meta = pc.load_run(anchored)
    assert meta.offset.total_seconds() == 97 and meta.summary()["mac_minus_pi_s"] == 97
    assert pc.set_caption(meta, anchored / SET_A).time == \
        "2026-10-13 3:31:37 PM PDT (Mac-anchored)"

    # a Mac time without a UTC offset is ambiguous: not used
    naive = _run(tmp_path / "c", lines, {"mac_time_at_start": "2026-10-13T15:27:25",
                                         "start_clock": {"pi_utc": PI_START}})
    meta = pc.load_run(naive)
    assert meta.offset is None and "not both ISO with a UTC offset" in meta.notes[0]


def test_time_fallbacks_experiment_json_then_folder_name(tmp_path):
    run = _run(tmp_path, [_line(SET_B, "garbage")],
               {"mac_time_at_start": "2026-10-13T22:26:48Z",
                "start_clock": {"pi_utc": PI_START}})                   # +60 s anchor
    (run / SET_A / "experiment.json").write_text(json.dumps({"timestamp": "20261013T222549Z"}))
    meta = pc.load_run(run)
    assert pc.set_caption(meta, run / SET_A).time == \
        "2026-10-13 3:26:49 PM PDT (Mac-anchored via experiment.json)"
    assert pc.set_caption(meta, run / SET_B).time == \
        "2026-10-13 3:27:37 PM PDT (Mac-anchored via folder name)"
    odd = run / "2026-10-13" / "not_a_set"
    odd.mkdir()
    assert pc.set_caption(meta, odd).time == "time n/a"


def test_no_manifest_no_sets_jsonl(tmp_path):
    run = tmp_path / "run"
    (run / SET_A).mkdir(parents=True)
    warnings = []
    meta = pc.load_run(run, warnings.append)
    assert meta.offset is None and any("no sets.jsonl" in w for w in warnings)
    assert pc.set_caption(meta, run / SET_A).depth == "depth n/a"


@pytest.mark.parametrize("utc, want", [
    ("2026-10-14T00:55:29+00:00", "2026-10-13 5:55:29 PM PDT"),
    ("2026-11-01T08:30:00Z", "2026-11-01 1:30:00 AM PDT"),   # before fall-back (2 AM PDT)
    ("2026-11-01T09:30:00Z", "2026-11-01 1:30:00 AM PST"),   # after: same wall time, PST
    ("2026-12-01T20:00:00Z", "2026-12-01 12:00:00 PM PST"),
    ("2026-03-08T09:59:59Z", "2026-03-08 1:59:59 AM PST"),   # spring forward at 2 AM PST
    ("2026-03-08T10:00:00Z", "2026-03-08 3:00:00 AM PDT"),
    ("20261013T070000Z", "2026-10-13 12:00:00 AM PDT"),       # compact experiment.json form
])
def test_local_time_pdt_pst(utc, want):
    assert pc.local_time_text(pc.parse_utc(utc)) == want


def test_parse_utc_rejects_naive_and_junk():
    assert pc.parse_utc("2026-10-13T22:25:48") is None
    assert pc.parse_utc("") is None and pc.parse_utc(None) is None and pc.parse_utc("x") is None
    assert pc.parse_utc("2026-10-13T15:25:48-07:00") == datetime(2026, 10, 13, 22, 25, 48,
                                                                 tzinfo=timezone.utc)


# --- the bar ------------------------------------------------------------------------------------

INFO = pc.SetCaption("2026-10-13 3:25:49 PM PDT (Mac-anchored)", "depth 1.23 m", "water 20.1 °C")


def _noise(h: int, w: int, ch: int = 3) -> np.ndarray:
    rng = np.random.default_rng(7)
    shape = (h, w, ch) if ch > 1 else (h, w)
    return rng.integers(0, 256, size=shape, dtype=np.uint8)


@pytest.mark.parametrize("w, h", [(4608, 2592), (1280, 800), (1600, 900), (512, 512)])
def test_bar_appended_below_pixels_identical(w, h):
    img = _noise(h, w)
    out = pc.add_caption(img, INFO, "imx708", "imx708_raw_20261013T222549Z.dng")
    bar = out[h:]
    assert out.shape[1] == w and bar.shape[0] > 0
    assert out[:h].tobytes() == img.tobytes()          # no image pixel covered or changed
    assert bar.max() == 255 and np.count_nonzero(bar) < bar.size // 2  # white text on black
    assert bar.shape[0] < 0.2 * h                        # "small" bar


def test_gray_input_and_16bit_refused():
    gray = _noise(300, 640, ch=1)
    out = pc.add_caption(gray, INFO, "openmv_n6", "openmv_n6_raw_20261013T222549Z.bayer")
    assert out.ndim == 2 and out[:300].tobytes() == gray.tobytes()
    with pytest.raises(ValueError, match="8-bit"):
        pc.add_caption(np.zeros((10, 10, 3), np.uint16), INFO, "imx708", "x")


@pytest.mark.parametrize("w", [4608, 1280])
def test_typical_caption_is_two_lines_and_phone_readable(w):
    fs, _pad, lines = pc.layout(pc.caption_fields(
        INFO, "imx708", w, 900, "imx708_raw_20261013T222549Z.dng"), w)
    assert len(lines) == 2
    assert lines[0].startswith("2026-10-13 3:25:49 PM PDT") and lines[1].startswith("IMX708 · ")
    assert f"{w}x900 px" in lines[1]
    assert w / 36 - 1 <= fs <= w / 32                    # font scales with width
    assert fs * 400 / w >= 10.5                          # px when shown 400 px wide on a phone


def test_very_long_file_name_is_not_clipped():
    w = 1600
    name = "imx708_raw_" + "verylongname_" * 20 + "20261013T222549Z.dng"
    fields = pc.caption_fields(INFO, "imx708", w, 900, name)
    fs, pad, lines = pc.layout(fields, w)
    fnt = pc.font(fs)
    for ln in lines:
        assert pc.text_width(ln, fnt) <= w - 2 * pad, ln   # every line fits the bar
    joined = "\n".join(lines)
    assert pc.ELLIPSIS in joined and "imx708_raw_verylong" in joined
    assert joined.endswith("20261013T222549Z.dng")         # middle-ellipsized: tail kept
    # pixels: nothing drawn in the right margin, i.e. no glyph runs off the bar
    out = pc.add_caption(_noise(900, w), INFO, "imx708", name)
    bar = out[900:]
    assert not bar[:, w - pad + 1:].any()


def test_long_but_fitting_name_wraps_whole():
    name = "production crop x1504 y846 of imx708_raw_20261013T222549Z.jpg"
    _fs, _pad, lines = pc.layout(pc.caption_fields(INFO, "imx708", 1600, 900, name), 1600)
    assert pc.ELLIPSIS not in "".join(lines)
    assert "imx708_raw_20261013T222549Z.jpg" in "".join(lines)


def test_every_caption_character_has_a_glyph():
    """A character missing from the font draws as a box (seen with U+00D7 on 2026-10-13)."""
    fnt = pc.font(60)
    notdef = fnt.getmask("\u4e00")  # CJK: not in the built-in Latin font
    text = "".join(pc.caption_fields(INFO, "openmv_ae3", 1280, 800, "a_b-0123456789.bayer")[0]
                   + pc.caption_fields(INFO, "imx708", 1, 1, "AMPSTxyz:/()")[1]) + pc.ELLIPSIS
    for ch in sorted(set(text) - {" "}):
        m = fnt.getmask(ch)
        assert (m.size, bytes(m)) != (notdef.size, bytes(notdef)), f"no glyph for {ch!r}"


def test_middle_ellipsize_keeps_head_and_tail():
    def fits(s):
        return len(s) <= 11

    assert pc.middle_ellipsize("abcdefghijklmnop.dng", fits) == "abcde…p.dng"
    assert pc.middle_ellipsize("abc", lambda s: False) == pc.ELLIPSIS


def test_ups_rtc_anchor_after_a_power_off(tmp_path):
    """Boot-started run in the pool: no Mac time, Pi clock wrong; the UPS RTC (97 s behind real
    time, measured at arm with NTP up) gives the real capture time."""
    real = datetime(2026, 10, 14, 18, 5, 0, tzinfo=timezone.utc)          # 11:05:00 AM PDT
    rtc = int(real.timestamp()) - 97                                       # RTC lags real by 97 s
    line = _line(SET_A, "2026-10-14T04:00:00+00:00")                       # stale Pi clock
    line["clock"]["ups_rtc_epoch"] = rtc
    arm = {"pi_epoch": 1791929324, "ups_rtc_epoch": 1791929227, "ntp": "yes"}  # pi - rtc = +97
    meta = pc.load_run(_run(tmp_path, [line], {"arm_clock": arm}))
    assert meta.basis == "UPS-RTC-anchored" and meta.rtc_offset_s == 97
    assert pc.set_caption(meta, meta.run / SET_A).time == \
        "2026-10-14 11:05:00 AM PDT (UPS-RTC-anchored)"


@pytest.mark.parametrize("arm", [
    {"pi_epoch": 1791929324, "ups_rtc_epoch": 1791929227, "ntp": "no"},     # no NTP at arm
    {"pi_epoch": 1791929324, "ups_rtc_epoch": None, "ntp": "yes"},          # RTC unread
    "garbage",
])
def test_unusable_anchor_falls_back_to_unanchored_pi_clock(tmp_path, arm):
    line = _line(SET_A, PI_START)
    line["clock"]["ups_rtc_epoch"] = 1791929227
    meta = pc.load_run(_run(tmp_path, [line], {"arm_clock": arm}))
    assert meta.basis == "Pi clock, unanchored"
    assert pc.set_caption(meta, meta.run / SET_A).time.endswith("(Pi clock, unanchored)")


def test_mac_anchor_still_wins_over_rtc(tmp_path):
    line = _line(SET_A, PI_START)
    line["clock"]["ups_rtc_epoch"] = 1791929227
    man = {"mac_time_at_start": "2026-10-13T15:25:48-07:00", "start_clock": {"pi_utc": PI_START},
           "arm_clock": {"pi_epoch": 1791929324, "ups_rtc_epoch": 1791929227, "ntp": "yes"}}
    meta = pc.load_run(_run(tmp_path, [line], man))
    assert pc.set_caption(meta, meta.run / SET_A).time.endswith("(Mac-anchored)")
