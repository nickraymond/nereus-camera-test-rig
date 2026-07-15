"""Unit tests for host_tools.discover_openmv — Spec §12.

Verifies identity-based discovery (VID + serial number), never a fixed ttyACM path.
pyserial's port enumeration is faked so the test runs on the host with no hardware.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from host_tools import discover_openmv  # noqa: E402


def _port(device, vid, pid, serial_number, product="OpenMV"):
    return SimpleNamespace(
        device=device, vid=vid, pid=pid, serial_number=serial_number,
        product=product, manufacturer="MicroPython",
    )


class _FakeListPorts:
    def __init__(self, ports):
        self._ports = ports

    def comports(self):
        return list(self._ports)


def _patch_ports(monkeypatch, ports):
    monkeypatch.setattr(discover_openmv, "_require_pyserial", lambda: _FakeListPorts(ports))


def test_discover_filters_by_openmv_vid(monkeypatch):
    ports = [
        _port("/dev/ttyACM0", 0x37C5, 0x1206, "005537493543"),   # N6
        _port("/dev/ttyUSB0", 0x10C4, 0xEA60, "SILABS123"),      # some FTDI/CP210x — ignore
    ]
    _patch_ports(monkeypatch, ports)
    boards = discover_openmv.discover()
    assert len(boards) == 1
    assert boards[0]["serial_number"] == "005537493543"
    assert boards[0]["port"] == "/dev/ttyACM0"
    assert boards[0]["vid"] == 0x37C5


def test_find_port_matches_by_serial_not_path(monkeypatch):
    ports = [
        _port("/dev/ttyACM0", 0x37C5, 0x1206, "AAAA"),
        _port("/dev/ttyACM1", 0x37C5, 0x1206, "BBBB"),
    ]
    _patch_ports(monkeypatch, ports)
    # Same serial must resolve to its port regardless of enumeration order / path.
    assert discover_openmv.find_port("BBBB") == "/dev/ttyACM1"
    assert discover_openmv.find_port("AAAA") == "/dev/ttyACM0"


def test_find_port_unknown_serial_returns_none(monkeypatch):
    _patch_ports(monkeypatch, [_port("/dev/ttyACM0", 0x37C5, 0x1206, "AAAA")])
    assert discover_openmv.find_port("ZZZZ") is None


def test_find_port_single_board_convenience(monkeypatch):
    _patch_ports(monkeypatch, [_port("/dev/ttyACM0", 0x37C5, 0x1206, "AAAA")])
    # No serial given + exactly one board -> return it.
    assert discover_openmv.find_port(None) == "/dev/ttyACM0"


def test_find_port_ambiguous_without_serial_returns_none(monkeypatch):
    ports = [
        _port("/dev/ttyACM0", 0x37C5, 0x1206, "AAAA"),
        _port("/dev/ttyACM1", 0x37C5, 0x1206, "BBBB"),
    ]
    _patch_ports(monkeypatch, ports)
    # Two boards + no serial -> refuse to guess.
    assert discover_openmv.find_port(None) is None
