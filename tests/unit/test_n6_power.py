"""INA3221 decode (scripts/ina3221_log.py) and the N6 power DOE's window statistics."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import ina3221_log as ina  # noqa: E402
import n6_power_sweep as sw  # noqa: E402


class FakeBus:
    """SMBus words come back little-endian; the chip registers are big-endian."""

    def __init__(self, regs: dict[int, int]):
        self.regs = regs

    def read_word_data(self, addr, reg):
        v = self.regs[reg]
        return ((v & 0xFF) << 8) | (v >> 8)


def test_decode_matches_the_nereus001_reads():
    # Raw chip values from 10/23: IDs, and CH1 = 5.128 V / 4.92 mV (the N6 idling).
    bus = FakeBus({0xFE: 0x5449, 0xFF: 0x3220, 0x00: 0x7127,
                   0x01: 123 << 3, 0x02: 641 << 3, 0x03: 0, 0x04: 0, 0x05: 0, 0x06: 0})
    assert ina.identify(bus, 0x40) == {"manuf": 0x5449, "die": 0x3220, "config": 0x7127}
    v, mv, ma = ina.read_channel(bus, 0x40, 1)
    assert round(v, 3) == 5.128 and round(mv, 2) == 4.92 and round(ma, 1) == 98.4
    assert ina.read_channel(bus, 0x40, 2) == (0.0, 0.0, 0.0)


def test_negative_shunt_is_signed():
    bus = FakeBus({0x01: (-10 << 3) & 0xFFFF, 0x02: 0})
    assert round(ina.read_channel(bus, 0x40, 1)[2], 1) == -8.0


def test_doe_has_the_twelve_cells_nick_asked_for():
    names = [sw.cell_name(c) for c in sw.cells()]
    assert len(names) == 12 and len(set(names)) == 12
    assert "HD_tags_30fps" in names and "VGA_notags_10fps" in names


def test_window_stats_counts_only_the_frame_window():
    rows = [{"mono_s": t, "bus_v": 5.0, "shunt_mv": 5.0, "ma": 100.0 + t, "mw": 5 * (100 + t)}
            for t in range(10)] + [{"mono_s": 5.5, "error": "OSError"}]
    s = sw.window_stats(rows, 2, 6)
    assert s["n_power"] == 5 and s["ma_mean"] == 104.0 and s["read_errors"] == 1
    assert sw.window_stats(rows, 100, 200) == {"n_power": 0}


def test_frame_stats(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("seq,host_rx_mono,bytes,tags,detect_ms\n0,10.0,1000,4,80\n1,10.1,1000,4,90\n"
                 "2,10.2,1000,3,100\n")
    s = sw.frame_stats(p, seconds=1.0)
    assert s["frames"] == 3 and s["fps_delivered"] == 10.0 and s["detect_ms_median"] == 90.0
    assert s["tags_mean"] == 3.67 and (s["t0"], s["t1"]) == (10.0, 10.2)


def test_sleep_phase_windows_skip_settling_and_the_reboot():
    import n6_sleep_power as sp
    marks = [{"t": 0, "name": "awake_sensor_on", "what": "start"},
             {"t": 20, "name": "sensor_sleep", "what": "start"},
             {"t": 40, "name": "sensor_shutdown", "what": "error OSError"},
             {"t": 40.1, "name": "lightsleep", "what": "start"},
             {"t": 60.2, "name": "lightsleep", "what": "woke"},
             {"t": 60.3, "name": "deepsleep", "what": "start"},
             {"t": 60.6, "name": "mpremote_exit", "what": "rc 1"},
             {"t": 90, "name": "service_idle_after", "what": "start"}]
    w = {n: (round(a, 1), round(b, 1)) for n, a, b in sp.phase_windows(marks, end=110)}
    assert w["awake_sensor_on"] == (2.0, 19.0) and w["sensor_sleep"] == (22.0, 39.0)
    assert w["deepsleep"] == (62.3, 79.3)          # capped at the hold: the reboot is excluded
    assert w["service_idle_after"] == (92.0, 109.0) and "sensor_shutdown" not in w
