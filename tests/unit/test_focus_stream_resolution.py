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
        if len(self.requests) >= 3:
            self.state.running = False

    def write(self, b):  # raw stop byte
        assert b == b"\n"
        self.stops += 1
        self.queue = [self.queue[0], {"status": "completed"}]  # one frame in flight, then done

    def read_message(self, timeout=None):
        if not self.queue:
            self.state.running = False
            return {"status": "completed"}
        return self.queue.pop(0)

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


def test_page_has_zoom_controls():
    import host_tools.focus_stream as fsm
    page = fsm._PAGE
    for needle in ("id=vp", "data-z=fit", "data-z=1", "data-z=8", "pixelated", "addEventListener('wheel'"):
        assert needle in page
