"""Host tool: live N6 focus stream in a browser — Spec §2 (video where practical), §14.

Serves the OpenMV N6's live camera as an MJPEG stream plus a large **sharpness readout**,
so you can adjust the M12 lens by hand and watch the number peak at best focus. Built for
the headless-on-Pi setup: run it on the Pi, open the printed URL in any browser over
Tailscale.

The board streams framed JPEG frames (``start_stream``); a background thread reads them off
the serial port and keeps the latest frame + sharpness. HTTP handlers serve:

    /               an HTML page: live video + big sharpness number + peak tracker
    /stream.mjpeg   multipart/x-mixed-replace MJPEG of the latest frames
    /focus.json     {"sharpness", "peak", "fps", "seq"} for the on-page readout

Sharpness is the JPEG byte count at fixed quality — a relative focus proxy that rises as a
fixed scene comes into focus. While streaming, this tool owns the serial port exclusively;
stop it (Ctrl-C) before running a capture.

Usage (on the Pi)::

    python -m host_tools.focus_stream --serial 005537493543
    python -m host_tools.focus_stream --serial 005537493543 --framesize VGA --http-port 8081
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openmv.common import command_protocol as cp  # noqa: E402

from host_tools.discover_openmv import find_port  # noqa: E402
from nereus_camera_test_rig.cameras.openmv_usb import WRITE_TIMEOUT, _SerialIO  # noqa: E402

DEFAULT_BAUD = 115200
DEFAULT_HTTP_PORT = 8081


# The N6 stream allowlist (openmv/n6/board_config.py FRAMESIZES): the last 4 need the PS5520.
N6_FRAMESIZES = ("QVGA", "VGA", "960x600", "HD", "1280x960", "1920x1080", "2560x1440", "2592x1944")

# Sensor id (OpenMV common/omv_csi.h) -> name, class, native size (drivers/sensors/*.c, v5.0.1).
SENSORS = {0x7936: ("PAG7936", "1 MP", "1280x800"), 0x5520: ("PS5520", "5 MP", "2592x1944")}


def sensor_label(sensor_id):
    """What the page shows for the sensor id the board reported (None = not reported)."""
    if sensor_id is None:
        return {"sensor": "unknown (board service doesn't report it)", "sensor_class": None,
                "sensor_native": None, "sensor_id": None}
    name, cls, native = SENSORS.get(int(sensor_id), ("unknown", None, None))
    return {"sensor": name, "sensor_class": cls, "sensor_native": native,
            "sensor_id": "0x%04x" % int(sensor_id)}


class StreamState:
    """Latest frame + focus metrics, shared between the serial reader and HTTP handlers."""

    def __init__(self):
        self._lock = threading.Lock()
        self.frame = b""
        self.seq = -1
        self.sharpness = 0
        self.peak = 0
        self.fps = 0.0
        self.running = True
        # Resolution shown on the page and switchable from it (Nick, 10/24): the board stream
        # is stopped cleanly, then restarted at the new framesize; fps follows the framesize.
        self.width = 0
        self.height = 0
        self.framesize = None
        self.pending = None  # framesize asked from the page, applied at the next segment
        self.sensor_id = None
        self.error = None  # last switch the board refused (e.g. a PS5520 size on the PAG7936)
        self.resyncs = 0          # garbage lines skipped (lost bytes)
        self.short_frames = 0     # frames dropped because bytes were lost inside them
        self.reader_errors = 0    # segments ended on an error and restarted
        self.last_reader_error = None

    def update(self, frame, seq, sharpness, fps, width=None, height=None):
        with self._lock:
            self.frame = frame
            self.seq = seq
            self.sharpness = sharpness
            self.peak = max(self.peak, sharpness)
            self.fps = fps
            if width and height:
                self.width, self.height = int(width), int(height)

    def request_framesize(self, framesize):
        """Ask the reader to switch; False while a switch is already in flight."""
        with self._lock:
            if self.pending is not None:
                return False
            self.pending = framesize
            return True

    def apply_pending(self):
        """Reader side, between segments: the framesize to restart with (None = keep)."""
        with self._lock:
            fs, self.pending = self.pending, None
            if fs is not None:
                self.framesize = fs
                self.peak = 0  # sharpness scales with the frame: a new size starts a new peak
                self.fps = 0.0
            return fs

    def info(self):
        with self._lock:
            return {"width": self.width, "height": self.height, "framesize": self.framesize,
                    "switching": self.pending is not None, "error": self.error,
                    "resyncs": self.resyncs, "short_frames": self.short_frames,
                    "reader_errors": self.reader_errors, "last_reader_error": self.last_reader_error,
                    "streaming": self.running,
                    **sensor_label(self.sensor_id)}

    def snapshot(self):
        with self._lock:
            return self.frame, self.seq, self.sharpness, self.peak, self.fps

    def reset_peak(self):
        with self._lock:
            self.peak = self.sharpness


def _decode_tail(line):
    """The protocol message at the END of ``line``: lost frame bytes can leave JPEG bytes in front
    of the next header. Same algorithm as scripts/sync_video.py ``decode_tail``."""
    end = len(line)
    start = max(0, end - 512)  # a header line is ~200 B; never scan a whole JPEG
    i = line.rfind(b"{", start, end)
    while i >= 0:
        try:
            msg = cp.decode_message(line[i:])
            if isinstance(msg, dict) and "status" in msg:
                return msg
        except (ValueError, UnicodeDecodeError, cp.ProtocolError):
            pass
        i = line.rfind(b"{", start, i)
    return None


def _split_short_jpeg(data):
    """A payload whose JPEG ends (FF D9) before its declared size lost bytes in transit: return
    (that frame, the bytes after it, which belong to the next message). As sync_video."""
    eoi = data.find(b"\xff\xd9")
    if 0 <= eoi < len(data) - 2:
        return data[:eoi + 2], data[eoi + 2:]
    return data, b""


def _next_message(io, state, timeout=10.0):
    """The next protocol message, resyncing past garbage instead of dying on it (Builder 2, 10/24:
    at 1920x1080 the N6 dropped bytes, and the old reader died on "'utf-8' codec can't decode")."""
    deadline = time.monotonic() + timeout
    while True:
        line = io.read_line(timeout=max(0.1, deadline - time.monotonic()))
        msg = _decode_tail(line)
        if msg is not None:
            if not line.lstrip().startswith(b"{"):
                state.resyncs += 1
            return msg
        if line.strip():
            state.resyncs += 1
        if time.monotonic() > deadline:
            raise TimeoutError("no stream header for %.0f s" % timeout)


MAX_FAILED_SEGMENTS = 5  # restarts in a row without a single frame before the reader gives up


def _reader_loop(io: _SerialIO, command_id: str, settings: dict, state: StreamState):
    """Read framed JPEG frames off the serial port, re-requesting the stream as needed.

    The board caps each stream segment at a safety timeout (so a dead host can't stream
    forever). To keep the browser feed continuous across that cap, the reader re-sends
    ``start_stream`` whenever a segment ends — invisible to the viewer beyond a brief hold
    on the last frame. Lost bytes are resynced (``_next_message``, ``_split_short_jpeg``); any
    other error ends only that segment: the board gets a stop byte and the stream restarts,
    so a resolution switch can never stay stuck. Stops for good once ``state.running`` is
    cleared (host shutdown) or after MAX_FAILED_SEGMENTS restarts in a row with no frame.
    """
    last_t = time.monotonic()
    fps = 0.0
    failed_in_a_row = 0
    try:
        while state.running:
            new_fs = state.apply_pending()
            if new_fs is not None:
                last_good = settings.get("framesize")
                settings = {**settings, "framesize": new_fs}
                state.error = None
                fps = 0.0
            else:
                last_good = settings.get("framesize")
                if state.framesize is None:
                    state.framesize = last_good
            stop_sent = False
            frames_this_segment = 0
            try:
                io.write_message(cp.make_request("start_stream", command_id, settings))
                while state.running:
                    header = _next_message(io, state)
                    status = header.get("status")
                    if status == "frame":
                        meta = header.get("frame") or {}
                        size = int(meta.get("size_bytes", 0))
                        data, rest = _split_short_jpeg(io.read_exact(size, timeout=10.0))
                        if rest:  # bytes were lost: this frame ended early, the next one began
                            io._buf[0:0] = rest
                            state.short_frames += 1
                            continue
                        frames_this_segment += 1
                        failed_in_a_row = 0
                        now = time.monotonic()
                        dt = now - last_t
                        last_t = now
                        if dt > 0:
                            fps = 0.8 * fps + 0.2 * (1.0 / dt)  # smoothed
                        state.update(
                            data, int(meta.get("seq", 0)), int(meta.get("sharpness", size)), fps,
                            meta.get("width"), meta.get("height"),
                        )
                        if meta.get("sensor_id") is not None:
                            state.sensor_id = meta["sensor_id"]
                        if state.pending is not None and not stop_sent:
                            # Any inbound byte ends the board's stream; a bare newline is ignored
                            # by its line loop afterwards (same stop as sync_video, QE #115).
                            io._t.write(b"\n")
                            stop_sent = True
                    elif status == "failed" and frames_this_segment == 0 \
                            and settings.get("framesize") != last_good:
                        # The board refused the new size before streaming a frame: go back to the
                        # last working size instead of re-asking for a size that cannot work.
                        err = (header.get("error") or {}).get("message") or "refused"
                        state.error = "%s: %s" % (settings.get("framesize"), err)
                        settings = {**settings, "framesize": last_good}
                        state.framesize = last_good
                        break
                    elif status in ("completed", "failed"):
                        break  # segment ended; outer loop re-requests if still running
            except Exception as exc:  # lost sync, timeout: end this segment, restart the stream
                state.reader_errors += 1
                state.last_reader_error = repr(exc)
                sys.stderr.write("focus stream segment ended on error (restarting): %r\n" % exc)
                if frames_this_segment == 0:
                    failed_in_a_row += 1
                    if failed_in_a_row >= MAX_FAILED_SEGMENTS:
                        raise
                try:
                    io._t.write(b"\n")  # make sure the board's stream is stopped
                except Exception:
                    pass
                time.sleep(1.0)
                try:
                    io._buf.clear()  # drop the rest of the broken segment
                except Exception:
                    pass
    except Exception as exc:  # the board stopped answering at all: stop cleanly
        sys.stderr.write("focus stream reader stopped: %s\n" % exc)
    finally:
        state.running = False
        state.pending = None  # never leave the page stuck at "switching"


_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>N6 focus</title><style>
body{margin:0;background:#111;color:#eee;font-family:system-ui,sans-serif;text-align:center}
#v{max-width:100%;height:auto;background:#000;display:block;margin:0 auto}
#hud{padding:10px;display:flex;gap:24px;justify-content:center;align-items:baseline;flex-wrap:wrap}
.n{font-size:40px;font-weight:700;font-variant-numeric:tabular-nums}
.l{font-size:12px;color:#9aa;text-transform:uppercase;letter-spacing:.08em}
#bar{height:10px;background:#333;border-radius:5px;overflow:hidden;max-width:640px;margin:6px auto}
#fill{height:100%;width:0;background:linear-gradient(90deg,#f55,#fd0,#5f5)}
button{background:#333;color:#eee;border:1px solid #555;border-radius:6px;
 padding:6px 12px;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
button.on{background:#2a6;border-color:#5c9}
#sizes{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;padding:0 10px 8px}
</style></head><body>
<div id=hud>
  <div><div class=n id=sharp>0</div><div class=l>sharpness</div></div>
  <div><div class=n id=peak>0</div><div class=l>peak</div></div>
  <div><div class=n id=fps>0</div><div class=l>fps</div></div>
  <div><div class=n id=res>&ndash;</div><div class=l id=resl>resolution</div></div>
  <div><div class=n id=sen>&ndash;</div><div class=l id=senl>sensor</div></div>
  <div><button onclick=fetch('/reset_peak',{method:'POST'})>reset peak</button></div>
</div>
<div id=sizes></div>
<div id=err style="color:#f88;font-size:13px;min-height:1em"></div>
<div id=bar><div id=fill></div></div>
<img id=v src=/stream.mjpeg>
<script>
async function tick(){
 try{let r=await fetch('/focus.json');let d=await r.json();
  document.getElementById('sharp').textContent=d.sharpness;
  document.getElementById('peak').textContent=d.peak;
  document.getElementById('fps').textContent=d.fps.toFixed(1);
  let pct=d.peak?Math.round(100*d.sharpness/d.peak):0;
  document.getElementById('fill').style.width=pct+'%';
  document.getElementById('res').textContent=d.width?d.width+'\u00d7'+d.height:'\u2013';
  document.getElementById('err').textContent=d.error?'board refused '+d.error:'';
  document.getElementById('resl').textContent=d.switching?'switching\u2026':(d.framesize?'resolution ('+d.framesize+')':'resolution');
  document.getElementById('sen').textContent=d.sensor_class?d.sensor+' \u00b7 '+d.sensor_class:d.sensor;
  document.getElementById('senl').textContent=d.sensor_native?'sensor (native '+d.sensor_native+', id '+d.sensor_id+')':'sensor';
  let box=document.getElementById('sizes');
  if(box.dataset.k!==d.framesizes.join()+d.framesize+d.switching){
   box.dataset.k=d.framesizes.join()+d.framesize+d.switching; box.innerHTML='';
   for(const fs of d.framesizes){let b=document.createElement('button');b.textContent=fs;
    b.className=fs===d.framesize?'on':''; b.disabled=d.switching||fs===d.framesize;
    b.onclick=()=>fetch('/framesize',{method:'POST',body:JSON.stringify({framesize:fs})});
    box.appendChild(b);}
  }
 }catch(e){}
 setTimeout(tick,150);
}
tick();
</script></body></html>"""


def _make_handler(state: StreamState, framesizes=N6_FRAMESIZES):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def do_POST(self):
            if self.path == "/reset_peak":
                state.reset_peak()
                self.send_response(204)
                self.end_headers()
            elif self.path == "/framesize":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    fs = json.loads(self.rfile.read(n) or b"{}").get("framesize")
                except ValueError:
                    fs = None
                if fs not in framesizes:
                    self._json(400, {"ok": False, "err": "framesize must be one of %s" % list(framesizes)})
                elif not state.request_framesize(fs):
                    self._json(409, {"ok": False, "err": "a resolution switch is already in progress"})
                else:
                    self._json(200, {"ok": True, "framesize": fs})
            else:
                self.send_error(404)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                body = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/focus.json":
                _frame, seq, sharp, peak, fps = state.snapshot()
                self._json(200, {"sharpness": sharp, "peak": peak, "fps": round(fps, 2),
                                 "seq": seq, **state.info(), "framesizes": list(framesizes)})
            elif self.path == "/stream.mjpeg":
                self._serve_mjpeg()
            else:
                self.send_error(404)

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_mjpeg(self):
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last_sent = -1
            try:
                while state.running:
                    frame, seq, _s, _p, _f = state.snapshot()
                    if frame and seq != last_sent:
                        last_sent = seq
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            ("Content-Length: %d\r\n\r\n" % len(frame)).encode()
                        )
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def _tailscale_host():
    try:
        return socket.gethostname()
    except Exception:
        return "<pi-host>"


def _raise_keyboard_interrupt(signum, frame):
    """SIGTERM handler: raise KeyboardInterrupt so run()'s clean shutdown path runs."""
    raise KeyboardInterrupt


def stream_settings(framesize="VGA", quality=70, max_seconds=3600, fps=0.0) -> dict:
    """The ``start_stream`` settings. max_seconds caps each board-side segment (the reader
    re-requests to stay live); ``fps`` is a board-side cap, sent only when non-zero (boards on
    older code ignore unknown keys)."""
    settings = {"framesize": framesize, "jpeg_quality": quality, "max_seconds": max_seconds}
    if fps:
        settings["fps"] = fps
    return settings


def _imx_reader_loop(state: StreamState, width: int, height: int, fps: float, lens: float):
    """IMX708 full-frame preview: rpicam-vid MJPEG on stdout, split at the JPEG end marker.

    ``--mode 2304:1296:10:P`` forces the full-FOV 2x2-binned sensor mode, so the preview shows
    the whole frame a still sees (ScalerCrop (0,0)/4608x2592; a small output size would
    otherwise pick the cropped 1536x864 mode). For posing a calibration board on the full
    frame (Friday 2026-10-16) and placing bench lamps/clocks."""
    import subprocess

    cmd = ["rpicam-vid", "-n", "-t", "0", "--codec", "mjpeg", "--mode", "2304:1296:10:P",
           "--width", str(width), "--height", str(height), "--framerate", str(fps or 10),
           "--autofocus-mode", "manual", "--lens-position", str(lens), "-o", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buf, seq, t_last = b"", 0, time.monotonic()
    try:
        while state.running and proc.poll() is None:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                continue
            buf += chunk
            while True:
                start = buf.find(b"\xff\xd8")
                end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                if start < 0 or end < 0:
                    break
                frame, buf = buf[start:end + 2], buf[end + 2:]
                now = time.monotonic()
                seq += 1
                state.update(frame, seq, len(frame), 1.0 / max(now - t_last, 1e-3), width, height)
                t_last = now
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def run_imx708(width=1152, height=648, fps=10.0, lens=0.8, http_host="0.0.0.0",
               http_port=8083):
    state = StreamState()
    reader = threading.Thread(target=_imx_reader_loop, args=(state, width, height, fps, lens),
                              daemon=True)
    reader.start()
    httpd = ThreadingHTTPServer((http_host, http_port), _make_handler(state, ()))
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    print("IMX708 full-frame preview on http://%s:%d/ (%dx%d, lens %.2f). Ctrl-C to stop."
          % (_tailscale_host(), http_port, width, height, lens))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping preview...")
    finally:
        state.running = False
        reader.join(timeout=6)
        httpd.server_close()


def run(serial_number=None, port=None, framesize="VGA", quality=70, max_seconds=3600,
        http_host="0.0.0.0", http_port=DEFAULT_HTTP_PORT, fps=0.0,
        framesizes=N6_FRAMESIZES):
    import serial

    dev = port or find_port(serial_number)
    if not dev:
        raise RuntimeError("no OpenMV board found for serial_number=%r" % serial_number)

    ser = serial.Serial(dev, DEFAULT_BAUD, timeout=0.2, write_timeout=WRITE_TIMEOUT)
    io = _SerialIO(ser, default_timeout=10.0)
    command_id = "focus-stream"
    settings = stream_settings(framesize, quality, max_seconds, fps)

    state = StreamState()
    reader = threading.Thread(
        target=_reader_loop, args=(io, command_id, settings, state), daemon=True
    )
    reader.start()

    httpd = ThreadingHTTPServer((http_host, http_port), _make_handler(state, tuple(framesizes)))

    # Treat SIGTERM like Ctrl-C so `kill <pid>` also shuts down cleanly. Without this, a
    # SIGTERM mid-stream skips the finally block below — the board never gets its stop byte
    # and can wedge its USB stack (observed 2026-07-14). Only works in the main thread.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    url = "http://%s:%d/" % (_tailscale_host(), http_port)
    print("focus stream on %s (serial=%s, %s q%d)"
          % (url, serial_number or dev, framesize, quality))
    print("adjust the M12 lens until 'sharpness' peaks. Ctrl-C (or SIGTERM) to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping stream...")
    finally:
        state.running = False
        try:
            ser.write(b"\n")  # stop signal to the board
            ser.flush()
        except Exception:
            pass
        time.sleep(0.3)
        httpd.server_close()
        ser.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live N6 focus stream in a browser.")
    parser.add_argument("--serial", dest="serial_number", default=None,
                        help="USB serial of the N6 (from discover_openmv)")
    parser.add_argument("--port", default=None, help="explicit device path (overrides --serial)")
    parser.add_argument("--framesize", default="VGA", help="QVGA | VGA | HD (default VGA)")
    parser.add_argument("--quality", type=int, default=70, help="JPEG quality (default 70)")
    parser.add_argument("--framesizes", default=",".join(N6_FRAMESIZES),
                        help="resolutions the page offers (default = openmv/n6/board_config.py "
                             "FRAMESIZES; the 4 large ones are PS5520-only: a size the sensor "
                             "refuses falls back to the last working one, shown on the page)")
    parser.add_argument("--max-seconds", type=int, default=3600,
                        help="per-segment board stream cap; reader re-requests to stay live")
    parser.add_argument("--http-host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--fps", type=float, default=0.0,
                        help="board-side frame-rate cap (0 = unlimited; e.g. 10 for the N6 to "
                             "match the AE3 and draw less current)")
    parser.add_argument("--imx708", action="store_true",
                        help="preview the IMX708 full frame instead of a board (default port "
                             "8083 when --http-port is not given)")
    parser.add_argument("--lens-position", type=float, default=0.8,
                        help="IMX708 manual focus for --imx708 (production 0.8)")
    args = parser.parse_args(argv)
    if args.imx708:
        port = args.http_port if args.http_port != DEFAULT_HTTP_PORT else 8083
        run_imx708(fps=args.fps or 10.0, lens=args.lens_position, http_host=args.http_host,
                   http_port=port)
        return 0
    try:
        run(serial_number=args.serial_number, port=args.port, framesize=args.framesize,
            quality=args.quality, max_seconds=args.max_seconds,
            http_host=args.http_host, http_port=args.http_port, fps=args.fps,
            framesizes=[f for f in args.framesizes.split(",") if f])
    except RuntimeError as exc:
        print("focus stream failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
