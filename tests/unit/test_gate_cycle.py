"""gate_cycle.py PASS/FAIL logic (PREREG §4) and board_probe's ladder summary. No hardware."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load("gate_cycle_t", "scripts/gate_cycle.py")
probe = _load("board_probe_t", "scripts/board_probe.py")


def _contact(ok=True, ticks_ms=30_000, recovered=None):
    first = {"ok": ok, "stage": "replied" if ok else "write_timeout", "reply_s": 0.3,
             "info": {"ticks_ms": ticks_ms, "boot_ms": {"main": 900, "imported": 1500,
                                                      "run": 1510}} if ok else {}}
    rec = {"first": first, "steps": [], "ok": ok}
    if recovered:
        rec["steps"] = [{"step": recovered, "retry": {"ok": True, "stage": "replied",
                                                       "info": {"ticks_ms": ticks_ms}}}]
        rec.update(ok=True, recovered_by=recovered)
    return rec


def _still(ok=True, why=None):
    return {"ok": ok, "bytes": 600_000, "seconds": 3.0, "mtime": 1000.0, **({"why": why} if why
                                                                           else {})}


def _rec(**kw):
    rec = {"cycle": 2, "started_by": "boot", "uptime_start_s": 25.0, "uptime_end_s": 120.0,
           "prev": {"clean": True, "by": "gate_timer"},
           "contact": {"openmv_ae3": _contact(), "openmv_n6": _contact()},
           "verify": {c: {"still": _still(), "raw": {"ok": True}} for c in gate.CAMERAS}}
    rec.update(kw)
    return rec


def test_all_good_is_a_pass_and_extends_the_streak():
    out = gate.evaluate(_rec(), {"streak": 4})
    assert out["result"] == "PASS" and out["streak"] == 5 and out["fail_reasons"] == []
    assert out["boards_power_cycled"] is True


def test_any_still_failure_fails_and_resets_the_streak():
    rec = _rec()
    rec["verify"]["openmv_ae3"]["still"] = _still(False, "timeout")
    out = gate.evaluate(rec, {"streak": 24})
    assert out["result"] == "FAIL" and out["streak"] == 0
    assert out["fail_reasons"] == ["openmv_ae3_still:timeout"]


def test_unclean_previous_shutdown_fails_but_not_on_the_first_cycle():
    assert "prev_shutdown_not_clean" in gate.evaluate(
        _rec(prev={"clean": False, "by": "unknown"}), {})["fail_reasons"]
    assert gate.evaluate(_rec(cycle=1, prev={"clean": False}), {})["result"] == "PASS"


def test_board_that_kept_power_fails_a_boot_cycle():
    rec = _rec(contact={"openmv_ae3": _contact(ticks_ms=9_000_000), "openmv_n6": _contact()})
    out = gate.evaluate(rec, {})
    assert out["boards_power_cycled"] is False and "boards_not_power_cycled" in out["fail_reasons"]


def test_ladder_recovery_in_boot_counts_as_pass_and_is_visible_in_the_csv():
    rec = _rec(contact={"openmv_ae3": _contact(ok=False, recovered="L3_usb_reset"),
                        "openmv_n6": _contact()})
    out = gate.evaluate(rec, {"streak": 1})
    assert out["result"] == "PASS"
    row = gate.csv_row(out)
    assert row["ae3_contact"] == "write_timeout->L3_usb_reset"
    assert set(row) == set(gate.CSV_FIELDS)


@pytest.mark.parametrize("rec,text", [
    ({"first": {"ok": True, "reply_s": 0.29}}, "ok 0.29s"),
    ({"first": {"ok": False, "stage": "write_timeout"}, "ok": True,
      "recovered_by": "L3_usb_reset",
      "steps": [{"step": "L1_dtr", "retry": {"stage": "write_timeout"}},
                {"step": "L3_usb_reset", "retry": {"stage": "replied"}}]},
     "write_timeout -> L1:write_timeout,L3:replied RECOVERED by L3_usb_reset"),
])
def test_probe_summary(rec, text):
    assert probe.summary(rec) == text


def test_video_clip_must_be_ok_synchronized_and_lossless():
    ok = {"ok": True, "skew_ms": 18.5, "fps": {"imx708": 27.4}, "lost": 0}
    assert gate.evaluate(_rec(video=ok), {})["result"] == "PASS"
    assert gate.evaluate(_rec(video={**ok, "skew_ms": 140.0}), {})["fail_reasons"] == [
        "video_skew:140.0"]
    assert gate.evaluate(_rec(video={**ok, "lost": 3}), {})["fail_reasons"] == ["video_lost:3"]
    assert gate.evaluate(_rec(video={"ok": False, "error": "timeout"}), {})["fail_reasons"] == [
        "video:timeout"]
    assert gate.csv_row(gate.evaluate(_rec(video=ok), {}))["video"] == "ok"
