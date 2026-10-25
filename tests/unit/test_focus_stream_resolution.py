"""host_tools.focus_stream: resolution shown on the page and switchable from it (Nick, 10/24).

A switch must stop the board's stream cleanly (one stop byte, wait for its completed line) and
restart it at the new framesize; the page reads width x height from the frames themselves.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from host_tools import focus_stream as fs


class FakeIO:
    """Board side of one or more stream segments: frames until a stop byte, then completed."""

    def __init__(self, state, sizes):
        self.state, self.sizes, self.requests, self.stops = state, sizes, [], 0
        self._t = self
        self.queue = []

    # _SerialIO surface used by the reader
    def write_message(self, msg):
        self.requests.append(msg["settings"]["framesize"])
        w, h = self.sizes[msg["settings"]["framesize"]]
        self.queue = [{"status": "frame", "frame": {"seq": i, "size_bytes": 3, "sharpness": 100 + i,
                                                       "width": w, "height": h}} for i in range(50)]
        if len(self.requests) >= self.stop_after:
            self.state.running = False

    def write(self, b):  # raw stop byte
        assert b == b"\n"
        self.stops += 1
        self.queue = [self.queue[0], {"status": "completed"}]  # one frame in flight, then done

    _buf = bytearray()
    stop_after = 3  # segments before the fake ends the test run

    def read_line(self, timeout=None):
        if not self.queue:
            self.state.running = False
            return json.dumps({"status": "completed"}).encode()
        m = self.queue.pop(0)
        if isinstance(m, Exception):
            raise m
        return m if isinstance(m, bytes) else json.dumps(m).encode()

    def read_exact(self, n, timeout=None):
        return b"\xff" * n


def test_switch_stops_the_stream_and_restarts_at_the_new_size():
    st = fs.StreamState()
    io = FakeIO(st, {"HD": (1280, 800), "VGA": (640, 400)})
    orig = io.read_exact
    switched = {"done": False}

    def read_exact(n, timeout=None):  # ask for VGA once the HD stream is running
        if not switched["done"] and st.width == 1280:
            assert st.request_framesize("VGA") and not st.request_framesize("QVGA")  # one at a time
            switched["done"] = True
        return orig(n, timeout)
    io.read_exact = read_exact
    fs._reader_loop(io, "c", fs.stream_settings("HD", 70, 60), st)
    assert io.requests[:2] == ["HD", "VGA"] and io.stops == 1
    info = st.info()
    assert (info["width"], info["height"], info["framesize"], info["switching"]) == (640, 400, "VGA", False)


def _serve(state, sizes):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fs._make_handler(state, sizes))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_address[1]


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_framesize_validates_and_reports():
    st = fs.StreamState()
    st.update(b"x", 1, 5, 10.0, 1280, 800)
    st.framesize = "HD"
    httpd, base = _serve(st, ("VGA", "HD"))
    try:
        d = json.loads(urllib.request.urlopen(base + "/focus.json", timeout=5).read())
        assert (d["width"], d["height"], d["framesize"], d["framesizes"], d["switching"]) == \
            (1280, 800, "HD", ["VGA", "HD"], False)
        assert _post(base + "/framesize", {"framesize": "FHD"})[0] == 400
        assert _post(base + "/framesize", {"framesize": "VGA"}) == (200, {"ok": True, "framesize": "VGA"})
        assert _post(base + "/framesize", {"framesize": "HD"})[0] == 409   # switch already pending
        assert json.loads(urllib.request.urlopen(base + "/focus.json", timeout=5).read())["switching"]
    finally:
        httpd.shutdown()


def test_imx708_preview_offers_no_switch():
    st = fs.StreamState()
    httpd, base = _serve(st, ())
    try:
        assert _post(base + "/framesize", {"framesize": "VGA"})[0] == 400
    finally:
        httpd.shutdown()


def test_sensor_label_from_the_live_id():
    assert fs.sensor_label(0x7936)["sensor"] == "PAG7936" and fs.sensor_label(0x7936)["sensor_class"] == "1 MP"
    ps = fs.sensor_label(0x5520)
    assert (ps["sensor"], ps["sensor_class"], ps["sensor_native"], ps["sensor_id"]) == ("PS5520", "5 MP", "2592x1944", "0x5520")
    assert fs.sensor_label(0x1234)["sensor"] == "unknown" and fs.sensor_label(None)["sensor_class"] is None


def test_reader_picks_up_the_sensor_id_from_the_first_frame():
    st = fs.StreamState()
    io = FakeIO(st, {"HD": (1280, 800)})
    orig = io.write_message

    def wm(msg):
        orig(msg)
        io.queue[0]["frame"]["sensor_id"] = 0x5520
    io.write_message = wm
    fs._reader_loop(io, "c", fs.stream_settings("HD", 70, 60), st)
    assert st.info()["sensor"] == "PS5520" and st.info()["sensor_class"] == "5 MP"


def test_refused_size_falls_back_to_the_last_working_one():
    st = fs.StreamState()

    class RefusingIO(FakeIO):
        def write_message(self, msg):
            super().write_message(msg)
            if msg["settings"]["framesize"] == "2592x1944":   # PAG7936: "Sensor control failed"
                self.queue = [{"status": "failed", "error": {"message": "Sensor control failed"}}]

    io = RefusingIO(st, {"HD": (1280, 800), "2592x1944": (2592, 1944)})
    orig = io.read_exact
    asked = {"done": False}

    def read_exact(n, timeout=None):
        if not asked["done"]:
            assert st.request_framesize("2592x1944")
            asked["done"] = True
        return orig(n, timeout)
    io.read_exact = read_exact
    fs._reader_loop(io, "c", fs.stream_settings("HD", 70, 60), st)
    assert io.requests[:3] == ["HD", "2592x1944", "HD"]
    info = st.info()
    assert info["framesize"] == "HD" and "Sensor control failed" in info["error"]


def test_default_sizes_follow_the_n6_allowlist():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "n6cfg", Path(__file__).resolve().parents[2] / "openmv/n6/board_config.py")
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    assert list(fs.N6_FRAMESIZES) == list(cfg.FRAMESIZES)


def test_reader_resyncs_past_garbage_and_survives_a_dead_segment():
    """Builder 2, 10/24: at 1920x1080 lost bytes killed the reader ("'utf-8' codec can't decode
    byte 0xa8") and every later switch hung. Now garbage is skipped and an error restarts."""
    st = fs.StreamState()
    io = FakeIO(st, {"1920x1080": (1920, 1080), "HD": (1280, 800)})
    orig = io.write_message

    def wm(msg):
        orig(msg)
        if len(io.requests) == 1:  # first segment: garbage, a header behind JPEG bytes, then a stall
            fr = io.queue[0]
            io.queue = [b"\xa8\xff\x00 not json", b"\xff\xd8junk" + json.dumps(fr).encode(),
                        TimeoutError("stall")]
            st.request_framesize("HD")   # a switch asked while the segment is about to die
    io.write_message = wm
    fs._reader_loop(io, "c", fs.stream_settings("1920x1080", 70, 60), st)
    info = st.info()
    assert info["resyncs"] == 2 and info["reader_errors"] == 1 and "stall" in info["last_reader_error"]
    assert io.requests[:2] == ["1920x1080", "HD"]        # the switch still happened after the error
    assert info["framesize"] == "HD" and info["switching"] is False


def test_short_frame_is_dropped_and_its_tail_goes_back_to_the_buffer():
    data, rest = fs._split_short_jpeg(b"\xff\xd8ab\xff\xd9{next")
    assert (data, rest) == (b"\xff\xd8ab\xff\xd9", b"{next")
    assert fs._split_short_jpeg(b"\xff\xd8abc\xff\xd9") == (b"\xff\xd8abc\xff\xd9", b"")


def test_reader_gives_up_after_repeated_dead_segments_and_clears_pending():
    st = fs.StreamState()
    io = FakeIO(st, {"HD": (1280, 800)})
    io.stop_after = 100
    orig = io.write_message

    def wm(msg):
        orig(msg)
        io.queue = [TimeoutError("dead")]
        if len(io.requests) == 1:
            st.request_framesize("HD")
    io.write_message = wm
    fs.time.sleep, real_sleep = (lambda s: None), fs.time.sleep
    try:
        fs._reader_loop(io, "c", fs.stream_settings("HD", 70, 60), st)
    finally:
        fs.time.sleep = real_sleep
    assert len(io.requests) == fs.MAX_FAILED_SEGMENTS and st.running is False and st.pending is None
def test_page_has_zoom_controls():
    import host_tools.focus_stream as fsm
    page = fsm._PAGE
    for needle in ("id=vp", "data-z=fit", "data-z=1", "data-z=8", "pixelated", "addEventListener('wheel'"):
        assert needle in page
