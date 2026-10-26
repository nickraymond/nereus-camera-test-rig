"""N6 mode list (SPEC_n6_eval must do 1): what the sensor really delivers, one captured frame each.

Run with ``mpremote connect <port> run openmv/probes/n6_modes_probe_v5.py`` (the port is the
board's /dev/serial/by-id path), then ``mpremote connect <port> reset``: mpremote leaves the rig
service stopped. Writes nothing to /flash. Prints one ``#M {json}`` line per entry; the host
keeps the whole console as the evidence.

Entries (each = set it, snapshot one frame, report what came back, or the error):
  - identity: firmware / MicroPython strings, sensor id;
  - every named framesize the ``sensor`` module defines (a name the module lacks is listed as
    absent, not tried), at RGB565;
  - custom (w, h) framesizes (n6/board_config.py: v5.0.1 hands a tuple to the N6 ISP scaler);
  - pixformats at HD;
  - windowing at HD (a centred 1280x720 and 640x400 window).
Binning vs cropping is not decided here: a smaller named size that keeps HD's field of view is
binned or scaled, a window crops. The frame's mean luma is printed so a dark or blank frame is
visible. Uses the legacy ``sensor`` API, the one the rig's stream uses (capture_service.py).
"""
import gc
import json
import time

import sensor

NAMES = ("QQQVGA", "QQVGA", "QVGA", "VGA", "WVGA", "SVGA", "XGA", "HD", "WXGA", "SXGA",
         "UXGA", "FHD", "B320X320", "B128X128")
CUSTOM = ((1280, 720), (1024, 640), (960, 600), (800, 500), (640, 360))
PIXFORMATS = ("RGB565", "GRAYSCALE", "BAYER", "YUV422", "JPEG")


def emit(kind, **kw):
    kw["kind"] = kind
    print("#M", json.dumps(kw))


def frame(tag):
    """Snapshot one frame after a short settle; report size, format and mean luma."""
    t0 = time.ticks_ms()
    sensor.skip_frames(time=300)
    img = sensor.snapshot()
    out = {"w": img.width(), "h": img.height(), "ms": time.ticks_diff(time.ticks_ms(), t0)}
    try:
        out["bytes"] = img.size()
    except Exception as e:
        out["bytes"] = "ERR " + repr(e)
    try:
        out["l_mean"] = img.get_statistics().l_mean()
    except Exception as e:
        out["l_mean"] = "ERR " + repr(e)
    try:
        out["exposure_us"] = sensor.get_exposure_us()
        out["gain_db"] = sensor.get_gain_db()
    except Exception as e:
        out["3a"] = "ERR " + repr(e)
    del img
    gc.collect()
    return out


def attempt(kind, label, setup):
    try:
        sensor.reset()
        setup()
        emit(kind, label=label, ok=True, **frame(label))
    except Exception as e:
        emit(kind, label=label, ok=False, error=repr(e))


def main():
    ident = {}
    for key, fn in (("uname", lambda: str(__import__("os").uname())),
                    ("implementation", lambda: str(__import__("sys").implementation)),
                    ("omv_version", lambda: __import__("omv").version_string()),
                    ("omv_board", lambda: __import__("omv").board_type()),
                    ("sensor_id", lambda: hex(sensor.get_id()))):
        try:
            sensor.reset()
            ident[key] = fn()
        except Exception as e:
            ident[key] = "ERR " + repr(e)
    emit("identity", **ident)

    for name in NAMES:
        fs = getattr(sensor, name, None)
        if fs is None:
            emit("framesize", label=name, ok=None, absent=True)
            continue
        attempt("framesize", name, lambda fs=fs: (sensor.set_pixformat(sensor.RGB565),
                                                  sensor.set_framesize(fs)))
    for wh in CUSTOM:
        attempt("custom", "%dx%d" % wh, lambda wh=wh: (sensor.set_pixformat(sensor.RGB565),
                                                       sensor.set_framesize(wh)))
    for pf in PIXFORMATS:
        const = getattr(sensor, pf, None)
        if const is None:
            emit("pixformat", label=pf, ok=None, absent=True)
            continue
        attempt("pixformat", pf, lambda c=const: (sensor.set_pixformat(c),
                                                  sensor.set_framesize(sensor.HD)))
    for win in ((0, 40, 1280, 720), (320, 200, 640, 400)):
        attempt("window", "HD win %dx%d+%d+%d" % (win[2], win[3], win[0], win[1]),
                lambda win=win: (sensor.set_pixformat(sensor.RGB565),
                                 sensor.set_framesize(sensor.HD), sensor.set_windowing(win)))
    emit("done")


main()
