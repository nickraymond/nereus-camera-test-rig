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
from nereus_camera_test_rig.cameras.openmv_usb import _SerialIO  # noqa: E402

DEFAULT_BAUD = 115200
DEFAULT_HTTP_PORT = 8081


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

    def update(self, frame, seq, sharpness, fps):
        with self._lock:
            self.frame = frame
            self.seq = seq
            self.sharpness = sharpness
            self.peak = max(self.peak, sharpness)
            self.fps = fps

    def snapshot(self):
        with self._lock:
            return self.frame, self.seq, self.sharpness, self.peak, self.fps

    def reset_peak(self):
        with self._lock:
            self.peak = self.sharpness


def _reader_loop(io: _SerialIO, command_id: str, state: StreamState):
    """Read framed JPEG frames off the serial port until the stream ends or errors."""
    last_t = time.monotonic()
    fps = 0.0
    try:
        while state.running:
            header = cp.decode_message(io.read_line(timeout=10.0))
            status = header.get("status")
            if status == "frame":
                meta = header.get("frame") or {}
                size = int(meta.get("size_bytes", 0))
                data = io.read_exact(size, timeout=10.0)
                now = time.monotonic()
                dt = now - last_t
                last_t = now
                if dt > 0:
                    fps = 0.8 * fps + 0.2 * (1.0 / dt)  # smoothed
                state.update(data, int(meta.get("seq", 0)), int(meta.get("sharpness", size)), fps)
            elif status in ("completed", "failed"):
                break
    except Exception as exc:  # stream stalled / port closed — stop cleanly
        sys.stderr.write("focus stream reader stopped: %s\n" % exc)
    finally:
        state.running = False


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
</style></head><body>
<div id=hud>
  <div><div class=n id=sharp>0</div><div class=l>sharpness</div></div>
  <div><div class=n id=peak>0</div><div class=l>peak</div></div>
  <div><div class=n id=fps>0</div><div class=l>fps</div></div>
  <div><button onclick=fetch('/reset_peak',{method:'POST'})>reset peak</button></div>
</div>
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
 }catch(e){}
 setTimeout(tick,150);
}
tick();
</script></body></html>"""


def _make_handler(state: StreamState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def do_POST(self):
            if self.path == "/reset_peak":
                state.reset_peak()
                self.send_response(204)
                self.end_headers()
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
                body = json.dumps(
                    {"sharpness": sharp, "peak": peak, "fps": round(fps, 2), "seq": seq}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/stream.mjpeg":
                self._serve_mjpeg()
            else:
                self.send_error(404)

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


def run(serial_number=None, port=None, framesize="VGA", quality=70,
        http_host="0.0.0.0", http_port=DEFAULT_HTTP_PORT):
    import serial

    dev = port or find_port(serial_number)
    if not dev:
        raise RuntimeError("no OpenMV board found for serial_number=%r" % serial_number)

    ser = serial.Serial(dev, DEFAULT_BAUD, timeout=0.2)
    io = _SerialIO(ser, default_timeout=10.0)
    command_id = "focus-stream"
    settings = {"framesize": framesize, "jpeg_quality": quality}
    io.write_message(cp.make_request("start_stream", command_id, settings))

    state = StreamState()
    reader = threading.Thread(target=_reader_loop, args=(io, command_id, state), daemon=True)
    reader.start()

    httpd = ThreadingHTTPServer((http_host, http_port), _make_handler(state))
    url = "http://%s:%d/" % (_tailscale_host(), http_port)
    print("focus stream on %s (serial=%s, %s q%d)"
          % (url, serial_number or dev, framesize, quality))
    print("adjust the M12 lens until 'sharpness' peaks. Ctrl-C to stop.")
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
    parser.add_argument("--http-host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    args = parser.parse_args(argv)
    try:
        run(serial_number=args.serial_number, port=args.port, framesize=args.framesize,
            quality=args.quality, http_host=args.http_host, http_port=args.http_port)
    except RuntimeError as exc:
        print("focus stream failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
