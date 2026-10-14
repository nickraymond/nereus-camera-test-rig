"""MS5837 (Bar30) maths on the real nereus002 bench reading (2026-10-13 8:45 PM) and the pool
loop's fail-soft depth path. No hardware."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ms = _load("depth_ms5837", "scripts/depth_ms5837.py")
pool_capture = _load("pool_capture_depth", "scripts/pool_capture.py")

PROM = [0xC001, 0x6CAD, 0x5E31, 0x4005, 0x3B28, 0x6C00, 0x65E8]  # read on nereus002
D1, D2 = 3820977, 7100915


def test_prom_crc_matches_the_real_sensor():
    assert ms.crc4(PROM) == PROM[0] >> 12
    assert ms.crc4([PROM[0] ^ 0x0001] + PROM[1:]) != PROM[0] >> 12


def test_bar30_compensation_reproduces_the_bench_reading():
    p, t = ms.compensate(PROM, D1, D2, "30BA")
    assert p == pytest.approx(984.90, abs=0.01) and t == pytest.approx(20.72, abs=0.01)
    p02, _ = ms.compensate(PROM, D1, D2, "02BA")
    assert p02 < 100  # the wrong model is obviously wrong in air: how the Bar30 was identified


def test_depth_fresh_water():
    assert ms.depth_m(985.0 + 97.77, 985.0) == pytest.approx(1.0, abs=0.001)


def test_loop_depth_is_fail_soft(monkeypatch):
    monkeypatch.setattr(pool_capture, "DEPTH_PY", "/nonexistent/python3")
    d = pool_capture.read_depth(985.0)
    assert d["ok"] is False and "error" in d
    assert pool_capture.depth_text(d) == " · depth ERR"
    ok = {"ok": True, "pressure_mbar": 1082.8, "temp_c": 18.46, "depth_m": 1.0}
    assert pool_capture.depth_text(ok) == " · depth +1.00m 18.5C"
    assert pool_capture.depth_text({"ok": True, "pressure_mbar": 985.0, "temp_c": 20.0,
                                    "depth_m_run_start": 0.0}) == " · depth +0.00m 20.0C"


def test_read_depth_rejects_non_dict_and_bad_ok(monkeypatch, tmp_path):
    """A depth script that prints valid JSON of the wrong shape must still be fail-soft."""
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(pool_capture, "DEPTH_PY", sys.executable)
    monkeypatch.setattr(pool_capture, "ROOT", tmp_path)
    for body, why in (('print("[1, 2]")', "not a JSON object"),
                      ('print(\'{"ok": true, "pressure_mbar": "x"}\')', "numeric")):
        (tmp_path / "scripts" / "depth_ms5837.py").write_text(body + "\n")
        d = pool_capture.read_depth(None)
        assert d["ok"] is False and why in d["error"]


def test_depth_bus_reaches_the_script(monkeypatch, tmp_path):
    """--depth-bus N must reach depth_ms5837.py (Nick is moving the Bar30 off the UPS bus)."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "depth_ms5837.py").write_text(
        "import json, sys\nprint(json.dumps({'ok': True, 'pressure_mbar': 985.0, "
        "'temp_c': 20.0, 'argv': sys.argv[1:]}))\n")
    monkeypatch.setattr(pool_capture, "DEPTH_PY", sys.executable)
    monkeypatch.setattr(pool_capture, "ROOT", tmp_path)
    d = pool_capture.read_depth(None, bus=3)
    assert d["ok"] and d["argv"][d["argv"].index("--bus") + 1] == "3"


def test_led_codes_are_opt_in():
    """Nick: no custom LED flashing by default; --led-codes turns #101's patterns on."""
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "pool_capture.py"), "run",
                          "--help"], capture_output=True, text=True).stdout
    assert "--led-codes" in out and "off by default" in out and "--depth-bus" in out
