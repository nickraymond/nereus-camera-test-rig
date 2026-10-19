"""Board framesize allowlists (openmv/*/board_config.py) as the shared board service reads them.

The N6's 960x600 is a custom size (pool 2 spec rev 6 fallback): ``sensor.set_framesize`` gets the
(w, h) tuple, which OpenMV v5.0.1 hands to the N6 ISP to scale from HD. The AE3 has no ISP scaler,
so its allowlist must not offer one. Runs the MicroPython module under CPython with stand-ins.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def service(monkeypatch):
    sensor = types.SimpleNamespace(QVGA=1, VGA=2, HD=3)
    monkeypatch.setitem(sys.modules, "sensor", sensor)
    monkeypatch.setitem(sys.modules, "command_protocol",
                        _load("command_protocol", "openmv/common/command_protocol.py"))
    return _load("capture_service_t", "openmv/common/capture_service.py")


def test_n6_offers_960x600_as_a_custom_size(service):
    n6 = _load("n6_board_config", "openmv/n6/board_config.py")
    assert service._resolve_framesize(n6, {"framesize": "960x600"}) == ((960, 600), "960x600")
    assert service._resolve_framesize(n6, {"framesize": "HD"}) == (3, "HD")   # unchanged
    assert service._resolve_framesize(n6, {}, default="VGA") == (2, "VGA")


def test_ae3_has_no_custom_size_and_rejects_960x600(service):
    ae3 = _load("ae3_board_config", "openmv/ae3/board_config.py")
    assert not any(isinstance(v, tuple) for v in ae3.FRAMESIZES.values())
    with pytest.raises(Exception, match="unsupported framesize"):
        service._resolve_framesize(ae3, {"framesize": "960x600"})
