"""On-board AprilTags for pool 3 (docs/specs/pool3_edge_tags.md): the board hook's id filter, frame
subset and pacing (openmv/common/capture_service.py, run under CPython with stand-ins and a fake
clock), and the host's arm-line flags, frames.csv columns and per-board summary (sync_video.py).
No hardware: the bench proof (must do 4) is the rig owner's.
"""

from __future__ import annotations

import importlib.util
import json
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


class Clock:
    """MicroPython ``time`` stand-in: ticks in ms, advanced by the fakes below."""

    def __init__(self):
        self.now = 1000

    def ticks_ms(self):
        return self.now

    def ticks_add(self, a, b):
        return a + b

    def ticks_diff(self, a, b):
        return a - b

    def sleep_ms(self, ms):
        self.now += ms


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(monkeypatch, clock):
    monkeypatch.setitem(sys.modules, "sensor", types.SimpleNamespace(RGB565=1, VGA=2, HD=3))
    monkeypatch.setitem(sys.modules, "time", clock)
    monkeypatch.setitem(sys.modules, "image", types.SimpleNamespace(TAG25H9=4))
    monkeypatch.setitem(sys.modules, "gc", types.SimpleNamespace(collect=lambda: None))
    monkeypatch.setitem(sys.modules, "command_protocol",
                        _load("command_protocol", "openmv/common/command_protocol.py"))
    return _load("capture_service_tags_t", "openmv/common/capture_service.py")


def tag(i, corners=((10, 10), (20, 10), (20, 20), (10, 20))):
    return types.SimpleNamespace(id=i, corners=corners)


# -- id filter -----------------------------------------------------------------------------------

def test_filter_keeps_card_ids_and_reports_the_rest_as_rejected(svc):
    r = svc._tag_result([tag(0), tag(11), tag(3), tag(30)], [0, 1, 2, 3], 1.0, 42)
    assert r["ids"] == [0, 3] and r["rej"] == [11, 30] and r["ms"] == 42
    assert len(r["corners"]) == 2


def test_filter_dedupes_a_tag_found_twice_in_overlapping_boxes(svc):
    r = svc._tag_result([tag(1), tag(1), tag(30), tag(30)], [0, 1, 2, 3], 1.0, 5)
    assert r["ids"] == [1] and r["rej"] == [30] and len(r["corners"]) == 1


def test_corners_from_a_scaled_copy_come_back_in_full_frame_pixels(svc):
    r = svc._tag_result([tag(2, ((5, 5), (10, 5), (10, 10), (5, 10)))], [2], 0.5, 1)
    assert r["corners"] == [[[10, 10], [20, 10], [20, 20], [10, 20]]]


@pytest.mark.parametrize("bad", [
    {"every": 0, "ids": [0]}, {"every": 5, "ids": []}, {"every": 5},
    {"every": 5, "ids": [0], "scale": 0}, {"every": 5, "ids": [0], "scale": 1.5},
    {"every": 5, "ids": [0], "bands": 0}, {"every": 5, "ids": [0], "bands": 9},
    {"every": "x", "ids": [0]}, {"every": 5, "ids": ["a"]},
])
def test_bad_tag_settings_fail_loudly(svc, bad):
    with pytest.raises(svc.cp.ProtocolError):
        svc._tag_config({"tags": bad})


def test_tags_are_off_unless_asked(svc):
    assert svc._tag_config({}) is None and svc._tag_config({"tags": None}) is None
    assert svc._tag_config({"tags": {"every": 5, "ids": [0, 1, 2, 3]}}) == {
        "every": 5, "scale": 1.0, "bands": 1, "ids": [0, 1, 2, 3], "tiles": None, "pad": None,
        "reacq": 4, "budget": None, "lit": None}


# -- frame subset ----------------------------------------------------------------------------------

def test_search_runs_every_nth_frame_and_never_on_a_late_frame(svc):
    assert svc._tag_due(0, None, 5, 0)
    assert not svc._tag_due(4, 0, 5, 0) and svc._tag_due(5, 0, 5, 0)
    assert svc._tag_due(5, 0, 5, -26)                        # finished with slack
    assert not svc._tag_due(5, 0, 5, 1)                      # behind: deferred
    assert svc._tag_due(7, 0, 5, 0)                          # ...to the next on-time frame


def test_bands_cover_every_row_and_hold_any_tag_up_to_h_over_8(svc):
    w, h = 1280, 800
    for bands in range(1, 9):
        boxes = [svc._band(k, bands, w, h) for k in range(bands)]
        assert all(x == 0 and bw == w for x, _, bw, _ in boxes)
        for top in range(0, h - h // 8 + 1, 7):  # a tag spanning rows [top, top + h/8)
            assert any(y <= top and top + h // 8 <= y + bh for _, y, _, bh in boxes), (bands, top)
    assert svc._band(0, 1, w, h) == (0, 0, w, h)


def test_track_boxes_grow_each_tag_and_clip_to_the_frame(svc):
    boxes = svc._track_boxes([[[100, 100], [120, 100], [120, 120], [100, 120]],
                              [[0, 0], [10, 0], [10, 10], [0, 10]]], 640, 400)
    assert boxes[0] == (79, 79, 62, 62)
    assert boxes[1][:2] == (0, 0)


class FakeImage:
    """Frame stand-in: records the searches; ``hits`` = what find_apriltags returns per call."""

    def __init__(self, clock, hits=(), search_ms=0, w=1280, h=800, fail=None):
        self.clock, self.hits, self.search_ms, self.w, self.h = clock, list(hits), search_ms, w, h
        self.fail, self.rois, self.scaled = fail, [], None

    def width(self):
        return self.w

    def height(self):
        return self.h

    def to_grayscale(self, x_scale, y_scale, copy):
        assert copy is True
        self.scaled = (x_scale, y_scale)
        return self

    def find_apriltags(self, roi, families):
        assert families == 4
        if self.fail:
            raise self.fail
        self.rois.append(roi)
        # cost scales with the searched area: search_ms is a full-frame search
        self.clock.now += int(self.search_ms * roi[2] * roi[3] / (self.w * self.h))
        return self.hits.pop(0) if self.hits else []


def test_tracks_found_tags_then_falls_back_to_bands_when_lost(svc, clock):
    cfg = {"every": 1, "scale": 1.0, "bands": 2, "ids": [0, 1, 2, 3]}
    st = {"corners": [], "last": [], "band": 0}
    img = FakeImage(clock, hits=[[], [tag(0)], [], []], search_ms=30)
    r1 = svc._find_tags(img, cfg, 4, st)   # band 0: nothing
    r2 = svc._find_tags(img, cfg, 4, st)   # band 1: finds tag 0
    r3 = svc._find_tags(img, cfg, 4, st)   # track box around tag 0: lost
    r4 = svc._find_tags(img, cfg, 4, st)   # back to band 0
    assert [r["mode"] for r in (r1, r2, r3, r4)] == ["band0", "band1", "track", "band0"]
    assert r2["ids"] == [0] and r1["ms"] == 18   # half the rows + h/8 pad: 500/800 of 30 ms
    assert img.rois[2] == (0, 0, 31, 31)   # the 10-20 px tag grown by its size, clipped at 0


def test_scaled_search_uses_a_grayscale_copy_and_scaled_boxes(svc, clock):
    cfg = {"every": 1, "scale": 0.5, "bands": 1, "ids": [0]}
    img = FakeImage(clock)
    svc._find_tags(img, cfg, 4, {"corners": [], "last": [], "band": 0})
    assert img.scaled == (0.5, 0.5) and img.rois == [(0, 0, 640, 400)]


# -- the stream loop: off = unchanged, on = bounded, failure = hook off ----------------------------

class FakeUsb:
    """Host stand-in: decodes frame headers, sends the stop byte at ``stop_at`` ms."""

    def __init__(self, clock, cp, stop_at):
        self.clock, self.cp, self.stop_at, self.msgs = clock, cp, stop_at, []

    def any(self):
        return 1 if self.clock.now >= self.stop_at else 0

    def read(self, n):
        return b"\n"

    def write(self, data):
        if isinstance(data, (bytes, bytearray)) and data.startswith(b"{"):
            msg = json.loads(data)
            msg["_t"] = self.clock.now  # when the header went out (frame gaps)
            self.msgs.append(msg)


class FakeSensor:
    """Snapshot / JPEG cost in ms on the fake clock (pool 2 B0 medians: AE3 640x400 4:2:0 ~11 +
    63 ms; N6 HD ~0 + 26 ms, ~/NereusData/.../2026-10-17_pool2_prep/bench)."""

    def __init__(self, clock, snap_ms, jpeg_ms, image):
        self.clock, self.snap_ms, self.jpeg_ms, self.image = clock, snap_ms, jpeg_ms, image
        self.RGB565 = 1

    def reset(self):
        pass

    def set_pixformat(self, pf):
        pass

    def set_framesize(self, fs):
        pass

    def skip_frames(self, time):
        pass

    def snapshot(self):
        self.clock.now += self.snap_ms
        img = self.image
        sensor = self

        def compress(quality, subsampling=None):
            sensor.clock.now += sensor.jpeg_ms

        img.compress = compress
        img.bytearray = lambda: b"\xff\xd8" + b"\0" * 100 + b"\xff\xd9"
        return img


def run_stream(svc, clock, monkeypatch, image, settings, snap_ms=11, jpeg_ms=63, seconds=6):
    sensor = FakeSensor(clock, snap_ms, jpeg_ms, image)
    monkeypatch.setattr(svc, "sensor", sensor)
    usb = FakeUsb(clock, svc.cp, clock.now + seconds * 1000)
    board = types.SimpleNamespace(FRAMESIZES={"VGA": "VGA", "HD": "HD"},
                                  STREAM_DEFAULT_FRAMESIZE="VGA", STREAM_DEFAULT_QUALITY=70,
                                  STREAM_MAX_SECONDS=60)
    monkeypatch.setattr(svc.sensor, "VGA", 2, raising=False)
    monkeypatch.setattr(svc.sensor, "HD", 3, raising=False)
    svc.stream_frames(usb, "c1", board, {"framesize": "VGA", "fps": 10, **settings})
    frames = [{**m["frame"], "_t": m["_t"]} for m in usb.msgs if m["status"] == "frame"]
    done = [m for m in usb.msgs if m["status"] == "completed"][-1]["output"]
    return frames, done


def test_tags_off_stream_has_no_tag_fields_and_keeps_10_fps(svc, clock, monkeypatch):
    frames, done = run_stream(svc, clock, monkeypatch, FakeImage(clock), {})
    assert 59 <= len(frames) <= 61 and "tags" not in done
    assert not any("tags" in f for f in frames)


def test_tags_on_ae3_keeps_10_fps_and_the_gap_bar_at_owner_2s_search_cost(svc, clock,
                                                                          monkeypatch):
    # AE3 at the pool settings: 74 ms busy of each 100 ms; owner 2 measured a full-frame search
    # at 140-155 ms (bench_prep MANIFEST). With 3 bands each search is ~58 % of that; it is paid
    # back by the next frames' slack, so the clip keeps 10 fps (bar >= 9.5) and the longest frame
    # gap stays <= 2 periods (spec rev 1 N4). No tag found: every search is a band search.
    img = FakeImage(clock, search_ms=155)
    frames, done = run_stream(svc, clock, monkeypatch, img,
                              {"tags": {"every": 10, "bands": 3, "ids": [0, 1, 2, 3]}})
    searched = [f for f in frames if "tags" in f]
    assert 59 <= len(frames) <= 61
    assert len(searched) >= 5                                  # >= 1 detection frame per second
    assert done["tags"]["searches"] == len(searched) and done["tags"]["error"] is None
    assert all(b["seq"] - a["seq"] >= 10 for a, b in zip(searched, searched[1:]))
    gaps = [b["_t"] - a["_t"] for a, b in zip(frames, frames[1:])]
    assert max(gaps) <= 200                                    # 2 periods at 10 fps


def test_a_search_that_never_fits_is_never_run_more_than_the_schedule_allows(svc, clock,
                                                                             monkeypatch):
    # N6 asked 30 fps but each frame already takes 40 ms: every frame is late, so no search runs
    # and capture is exactly what it would be without the hook (detection never costs capture).
    frames, done = run_stream(svc, clock, monkeypatch, FakeImage(clock, search_ms=100),
                              {"fps": 30, "tags": {"every": 5, "ids": [0]}}, snap_ms=1, jpeg_ms=39)
    assert done["tags"]["searches"] == 1                        # frame 0 only (nothing is late yet)


def test_tracked_search_reports_card_ids_and_raw_false_ids(svc, clock, monkeypatch):
    img = FakeImage(clock, hits=[[tag(0), tag(1), tag(30)]] * 100, search_ms=120)
    frames, _ = run_stream(svc, clock, monkeypatch, img, {"tags": {"every": 10, "ids": [0, 1]}})
    searched = [f["tags"] for f in frames if "tags" in f]
    assert searched[0]["ids"] == [0, 1] and searched[0]["rej"] == [30]
    assert searched[1]["mode"] == "track"


def test_tags_without_an_fps_cap_fail_the_arm(svc, clock, monkeypatch):
    with pytest.raises(svc.cp.ProtocolError, match="fps"):
        run_stream(svc, clock, monkeypatch, FakeImage(clock),
                   {"fps": 0, "tags": {"every": 5, "ids": [0]}})


def test_memory_error_turns_the_hook_off_and_the_stream_goes_on(svc, clock, monkeypatch):
    frames, done = run_stream(svc, clock, monkeypatch, FakeImage(clock, fail=MemoryError("fb")),
                              {"tags": {"every": 5, "ids": [0]}})
    assert 59 <= len(frames) <= 61
    assert "MemoryError" in done["tags"]["error"] and done["tags"]["searches"] == 0
    assert not any("tags" in f for f in frames)


def test_firmware_without_tag25h9_fails_the_arm(svc, clock, monkeypatch):
    monkeypatch.setitem(sys.modules, "image", types.SimpleNamespace(TAG36H11=16))
    with pytest.raises(svc.cp.ProtocolError, match="TAG25H9"):
        run_stream(svc, clock, monkeypatch, FakeImage(clock), {"tags": {"every": 5, "ids": [0]}})


def test_next_due_catches_up_then_drops_debt_beyond_one_period(svc):
    assert svc._next_due(1000, 1050, 100) == (1100, -50)       # on time
    assert svc._next_due(1000, 1150, 100) == (1100, 50)        # 50 ms late: next frame at once
    assert svc._next_due(1000, 1250, 100) == (1250, 150)       # > 1 period behind: restart


# -- G2 (docs/specs/yard_g2.md): tiles, re-acquire, budget guard, lit stored frame ----------------

def g2(**kw):
    cfg = {"every": 1, "scale": 1.0, "bands": 1, "ids": [0, 1, 2, 3], "tiles": [2, 3], "pad": 100,
           "reacq": 4, "budget": 66, "lit": None}
    cfg.update(kw)
    return cfg


def g2_state():
    return {"corners": [], "last": [], "band": 0, "tile": 0, "tracks": 0, "tile_ms": None}


@pytest.mark.parametrize("rows,cols,w,h,pad", [(2, 3, 1280, 800, 100), (3, 4, 640, 400, 50),
                                               (1, 1, 1280, 800, 0), (8, 8, 1280, 800, 40)])
def test_tiles_cover_the_frame_and_hold_any_tag_up_to_pad_in_one_tile(svc, rows, cols, w, h, pad):
    boxes = [svc._tile(k, rows, cols, w, h, pad) for k in range(rows * cols)]
    assert all(x >= 0 and y >= 0 and x + bw <= w and y + bh <= h for x, y, bw, bh in boxes)
    side = max(pad, 1)
    for ty in range(0, h - side + 1, 37):
        for tx in range(0, w - side + 1, 37):
            assert any(x <= tx and y <= ty and tx + side <= x + bw and ty + side <= y + bh
                       for x, y, bw, bh in boxes), (tx, ty)


@pytest.mark.parametrize("bad", [
    {"tiles": [0, 3]}, {"tiles": [2, 9]}, {"tiles": [2]}, {"tiles": "2x3"}, {"pad": -1},
    {"reacq": 0}, {"budget_ms": 0}, {"lit": 5},
])
def test_bad_g2_settings_fail_loudly(svc, bad):
    with pytest.raises(svc.cp.ProtocolError):
        svc._tag_config({"tags": {"every": 5, "ids": [0], **bad}})


def test_g2_settings_parse(svc):
    cfg = svc._tag_config({"tags": {"every": 5, "ids": [0], "tiles": [2, 3], "pad": 80,
                                    "reacq": 3, "budget_ms": 60, "lit": "/flash/lit.pgm"}})
    assert (cfg["tiles"], cfg["pad"], cfg["reacq"], cfg["budget"], cfg["lit"]) == \
        ([2, 3], 80, 3, 60, "/flash/lit.pgm")


def test_acquire_visits_each_tile_in_turn_then_wraps(svc, clock):
    img = FakeImage(clock, search_ms=182)
    st, cfg = g2_state(), g2()
    modes = [svc._find_tags(img, cfg, 4, st)["mode"] for _ in range(7)]
    assert modes == ["tile0", "tile1", "tile2", "tile3", "tile4", "tile5", "tile0"]
    assert img.rois[:6] == [svc._tile(k, 2, 3, 1280, 800, 100) for k in range(6)]


def test_reacquire_adds_the_next_tile_every_mth_track_search_and_tracks_what_it_finds(svc, clock):
    other = ((900, 600), (930, 600), (930, 630), (900, 630))
    hits = [[tag(0)]] + [[tag(0)]] * 4 + [[tag(1, other)]]
    img = FakeImage(clock, hits=hits, search_ms=182)
    st, cfg = g2_state(), g2()
    recs = [svc._find_tags(img, cfg, 4, st) for _ in range(5)]
    assert [r["mode"] for r in recs] == ["tile0", "track", "track", "track", "track+tile1"]
    assert recs[-1]["ids"] == [0, 1]                 # the card's other tag, re-acquired (N4)
    assert len(svc._track_boxes(st["corners"], 1280, 800)) == 2  # both tracked from now on


def test_reacquire_waits_when_the_last_tile_would_not_fit_the_budget(svc, clock):
    img = FakeImage(clock, hits=[[tag(0)]] * 10, search_ms=182)
    st, cfg = g2_state(), g2(reacq=1, budget=40)
    svc._find_tags(img, cfg, 4, st)                  # tile0 (527 x 500 px) finds tag 0: 46 ms
    assert st["tile_ms"] == 46
    r = svc._find_tags(img, cfg, 4, st)              # track + 46 > 40: no tile this time
    assert r["mode"] == "track" and r["over"] == 0


def test_tile_says_whether_it_held_the_cards_last_known_position(svc, clock):
    # tag 0 found in tile0 (top-left), then lost: tile1..5 don't hold it, tile0 again does
    img = FakeImage(clock, hits=[[tag(0)], []], search_ms=182)
    st, cfg = g2_state(), g2()
    recs = [svc._find_tags(img, cfg, 4, st) for _ in range(8)]
    tiles = ["tile%d" % k for k in (1, 2, 3, 4, 5, 0)]
    assert [r["mode"] for r in recs] == ["tile0", "track"] + tiles
    assert recs[0]["held"] == 0                      # before the first detection nothing counts
    assert [r["held"] for r in recs[2:]] == [0, 0, 0, 0, 0, 1] and "held" not in recs[1]
    assert svc._holds((0, 0, 50, 50), [[[10, 10], [20, 10], [20, 20], [10, 20]]]) == 1
    assert svc._holds((15, 0, 50, 50), [[[10, 10], [20, 10], [20, 20], [10, 20]]]) == 0


def test_no_tiles_keeps_the_bands_behaviour_and_header(svc, clock):
    img = FakeImage(clock, hits=[[tag(0)]] * 10, search_ms=30)
    st = g2_state()
    cfg = g2(tiles=None, bands=2)
    recs = [svc._find_tags(img, cfg, 4, st) for _ in range(6)]
    assert [r["mode"] for r in recs] == ["band0"] + ["track"] * 5   # never track+tile
    assert all("over" not in r for r in recs) and st["tracks"] == 0


def test_n6_tiles_keep_the_gap_bar_at_the_spare_n6s_full_frame_cost(svc, clock, monkeypatch):
    # N6 HD 15 fps: ~26 ms JPEG of a 66 ms period. Full-frame search 182 ms on nereus001's spare N6
    # (pool3 spec :13; nereus002's own cost is must do 3's). 2 x 3 tiles + 100 px pad: ~67 ms.
    img = FakeImage(clock, search_ms=182, w=1280, h=800)
    frames, done = run_stream(svc, clock, monkeypatch, img,
                              {"framesize": "HD", "fps": 15,
                               "tags": {"every": 5, "ids": [0, 1, 2, 3], "tiles": [2, 3],
                                        "pad": 100}}, snap_ms=0, jpeg_ms=26)
    searched = [f["tags"] for f in frames if "tags" in f]
    assert len(frames) >= 0.95 * 15 * 6 and len(searched) >= 6
    assert done["tags"]["over"] == 0 and all(t["over"] == 0 for t in searched)
    gaps = [b["_t"] - a["_t"] for a, b in zip(frames, frames[1:])]
    assert max(gaps) <= 133                            # 2 periods at 15 fps


def test_over_budget_search_is_counted_and_skips_the_next_every_frames(svc, clock, monkeypatch):
    img = FakeImage(clock, search_ms=400)              # one 1 x 1 tile = the whole frame: 400 ms
    frames, done = run_stream(svc, clock, monkeypatch, img,
                              {"tags": {"every": 5, "ids": [0], "tiles": [1, 1]}}, seconds=10)
    searched = [f for f in frames if "tags" in f]
    assert len(frames) > 60 and len(searched) >= 3     # capture goes on
    assert all(f["tags"]["over"] == 1 for f in searched)
    assert done["tags"]["over"] == len(searched) == done["tags"]["searches"]
    assert all(b["seq"] - a["seq"] >= 10 for a, b in zip(searched, searched[1:]))  # 2 x every


def test_lit_image_is_searched_in_place_of_the_live_frame(svc, clock, monkeypatch):
    lit = FakeImage(clock, hits=[[tag(2)]] * 100, search_ms=100)
    live = FakeImage(clock)
    def load(path):
        if path != "/l":
            raise OSError(2, "ENOENT")
        return lit
    monkeypatch.setitem(sys.modules, "image", types.SimpleNamespace(TAG25H9=4, Image=load))
    frames, _ = run_stream(svc, clock, monkeypatch, live,
                           {"tags": {"every": 5, "ids": [2], "tiles": [2, 2], "lit": "/l"}})
    assert live.rois == [] and lit.rois                 # only the stored frame is searched
    assert [f["tags"]["ids"] for f in frames if "tags" in f][0] == [2]
    with pytest.raises(svc.cp.ProtocolError, match="lit image"):
        run_stream(svc, clock, monkeypatch, live,
                   {"tags": {"every": 5, "ids": [2], "tiles": [2, 2], "lit": "/missing"}})


# -- host: arm-line flags, frames.csv columns, per-board summary -----------------------------------

@pytest.fixture(scope="module")
def sv():
    return _load("sync_video_tags_t", "scripts/sync_video.py")


def test_tag_ids_come_from_the_card_copy_yaml(sv):
    assert sv.card_tag_ids("c1") == [0, 1, 2, 3]
    assert sv.card_tag_ids("c2") == [4, 5, 6, 7]
    with pytest.raises(ValueError, match="--tag-ids"):
        sv.card_tag_ids(None)


def test_board_settings_tags_off_by_default_and_per_board_on(sv):
    a = sv.build_parser().parse_args([])
    assert "tags" not in sv.board_settings(a, "openmv_n6")
    a = sv.build_parser().parse_args(["--n6-tags-every", "5", "--n6-tags-bands", "2",
                                      "--ae3-tags-every", "10", "--ae3-tags-scale", "0.5",
                                      "--tag-ids", "c1"])
    assert sv.board_settings(a, "openmv_n6")["tags"] == {"every": 5, "scale": 1.0, "bands": 2,
                                                         "ids": [0, 1, 2, 3]}
    assert sv.board_settings(a, "openmv_ae3")["tags"]["scale"] == 0.5


def test_tags_on_without_a_card_copy_fails_before_recording(sv):
    a = sv.build_parser().parse_args(["--board-tags-every", "5"])
    with pytest.raises(ValueError, match="--tag-ids"):
        sv.board_settings(a, "openmv_ae3")


def test_dive_program_phase_args_accept_the_tag_flags(sv):
    sv.check_extra_args(["--n6-tags-every", "5", "--ae3-tags-every", "10", "--tag-ids", "c1"])
    with pytest.raises(ValueError):
        sv.check_extra_args(["--tag-ids", "c9"])


def test_summary_counts_detection_frames_hits_and_raw_false_ids(sv):
    rows = [{**sv.tag_columns(None)} for _ in range(50)]
    rows += [sv.tag_columns({"mode": "band0", "ms": 40, "ids": [0, 1], "rej": [], "corners": []}),
             sv.tag_columns({"mode": "track", "ms": 20, "ids": [], "rej": [30], "corners": []}),
             sv.tag_columns({"mode": "track", "ms": 30, "ids": [2], "rej": [11, 30],
                             "corners": []})]
    s = sv.tag_summary(rows, 2.0)
    assert s["detection_frames"] == 3 and s["detections_per_s"] == 1.5
    assert s["hit_frames"] == 2 and s["hit_pct"] == 66.7
    assert s["false_ids"] == 3 and s["false_id_values"] == [11, 30]
    assert s["decode_ms_median"] == 30.0 and s["decode_ms_max"] == 40.0
    assert sv.tag_summary(rows[:50], 2.0)["hit_pct"] is None


def test_g2_flags_reach_the_board_settings_only_when_tiles_are_asked(sv):
    a = sv.build_parser().parse_args(["--n6-tags-every", "5", "--ae3-tags-every", "10",
                                      "--n6-tags-tiles", "2x3", "--n6-tags-pad", "100",
                                      "--board-tags-budget-ms", "60", "--ae3-tags-lit",
                                      "/flash/lit.pgm", "--tag-ids", "c1"])
    n6, ae3 = sv.board_settings(a, "openmv_n6")["tags"], sv.board_settings(a, "openmv_ae3")["tags"]
    assert n6["tiles"] == [2, 3] and n6["pad"] == 100 and n6["budget_ms"] == 60
    assert "reacq" not in n6                                 # unset: the board's default (4)
    assert "tiles" not in ae3 and "budget_ms" not in ae3     # no tiles: today's bands, unchanged
    assert ae3["lit"] == "/flash/lit.pgm"


def _g2_row(ticks, mode, ids=(), held=None, over=0):
    return {"ticks_ms": str(ticks), "tag_ms": "30", "tag_mode": mode,
            "tag_ids": json.dumps(list(ids)), "tag_rej": "[]", "tag_over": str(over),
            "tag_held": "" if held is None else str(held)}


def test_g2_summary_sweep_first_detection_and_r1_hit_pct(sv):
    rows = [{"ticks_ms": "1000", "tag_ms": ""}]
    rows += [_g2_row(1000 + 500 * k, "tile%d" % k, held=0) for k in range(4)]     # sweep 1: 1.5 s
    rows += [_g2_row(3000, "tile0", ids=[0], held=0, over=1),                     # first hit 2.0 s
             _g2_row(3500, "track", ids=[0]), _g2_row(4000, "track"),             # lost
             _g2_row(4500, "track+tile1", ids=[0, 1], held=0),                   # ends sweep
             _g2_row(5000, "tile2", held=0), _g2_row(7000, "tile3", held=0),     # sweep 2: 4.5 s
             _g2_row(9000, "tile0", held=1), _g2_row(9500, "tile1", held=0),
             _g2_row(10000, "tile2", held=0)]                                      # (3rd: open)
    g = sv.g2_summary(rows, 4)
    assert g["sweeps"] == 2 and g["sweep_s_max"] == 4.5 and g["sweep_ok"] is False
    assert g["first_detection_s"] == 2.0 and g["over_searches"] == 1
    # R1: 3 track frames + 1 held tile = 4 counted, 2 with a card id (the miss-free tile0 at 3000
    # held nothing yet: no frame counts before the first detection)
    assert g["r1_frames"] == 4 and g["r1_hit_pct"] == 50.0
    assert sv.g2_summary([{"ticks_ms": "0", "tag_ms": ""}], 6)["sweep_ok"] is None


def test_g2_sweep_ignores_the_re_acquire_probes_while_tracking(sv):
    # QE 011 rig #144 N1: 2x3 tiles, 3 searches/s, the card found in tile4 at 1.33 s, then
    # tracked for 10 s with a track+tileK probe every 4th search. Those probes visit all 6 tiles
    # in ~8 s; they are not an acquisition sweep and must not fail the 3 s bar.
    rows = [_g2_row(round(1000 * k / 3), "tile%d" % k, held=0) for k in range(4)]
    rows.append(_g2_row(1333, "tile4", ids=[0, 1, 2, 3], held=0))
    for i in range(1, 31):
        mode = "track+tile%d" % ((4 + i // 4) % 6) if i % 4 == 0 else "track"
        rows.append(_g2_row(1333 + round(1000 * i / 3), mode, ids=[0, 1, 2, 3]))
    g = sv.g2_summary(rows, 6)
    assert g["first_detection_s"] == 1.33
    assert g["sweeps"] == 0 and g["sweep_s_max"] is None and g["sweep_ok"] is None
    # The card is lost: a full acquire sweep at 3 searches/s now times 5 / 3 s, and passes.
    t0 = 1333 + 10000
    rows += [_g2_row(t0 + round(1000 * k / 3), "tile%d" % ((5 + k) % 6), held=int(k == 5))
             for k in range(6)]
    g = sv.g2_summary(rows, 6)
    assert g["sweeps"] == 1 and g["sweep_s_max"] == 1.67 and g["sweep_ok"] is True


def test_host_logs_the_board_tag_records_and_summarises_them(sv, tmp_path, monkeypatch):
    # The hook's frame headers end up in frames.csv (tag_* columns) and video_sync.json's
    # per-board "tags" block, next to max_gap_ms (the N4 bar's input).
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from openmv.common import command_protocol as cp

    from nereus_camera_test_rig.cameras.openmv_usb import _SerialIO

    class Bytes:
        def __init__(self, data):
            self.data = bytearray(data)

        def read(self, n):
            out = bytes(self.data[:n])
            del self.data[:n]
            return out

        def write(self, data):
            return len(data)

    jpeg = b"\xff\xd8" + b"\x01" * 200 + b"\xff\xd9"
    stream = b""
    for i in range(10):
        extra = None
        if i % 5 == 0:
            extra = {"tags": {"mode": "band0", "ms": 40 + i, "ids": [0, 1] if i else [],
                              "rej": [30], "corners": [[[1, 2], [3, 4], [5, 6], [7, 8]]]}}
        stream += cp.encode_message(cp.frame_response("cid", i, len(jpeg), len(jpeg), 640, 400,
                                                      ticks_ms=1000 + 100 * i, extra=extra)) + jpeg
    done = {"frames": 10, "sync": {"fire_rx_ms": 1000},
            "tags": {"searches": 2, "max_ms": 45, "error": None}}
    stream += cp.encode_message(cp.completed_response("cid", done))
    settings = {"tags": {"every": 5, "scale": 1.0, "bands": 1, "ids": [0, 1, 2, 3]}}
    bv = sv.BoardVideo("openmv_ae3", "SERIAL", tmp_path, settings, seconds=1.0)
    monkeypatch.setattr(bv.cam, "close", lambda: None)
    bv.io = _SerialIO(Bytes(stream), default_timeout=1)
    bv.rec["fire_host_mono"] = 50.0
    bv._read()
    import csv
    rows = list(csv.DictReader((tmp_path / "openmv_ae3_frames.csv").open()))
    assert list(rows[0])[-len(sv.TAG_KEYS):] == list(sv.TAG_KEYS)
    assert json.loads(rows[5]["tag_ids"]) == [0, 1] and rows[1]["tag_ms"] == ""
    rec = bv.finish()
    assert rec["tags"]["detection_frames"] == 2 and rec["tags"]["hit_pct"] == 50.0
    assert rec["tags"]["false_ids"] == 2 and rec["tags"]["board"]["searches"] == 2
