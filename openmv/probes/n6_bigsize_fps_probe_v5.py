"""N6 + PS5520 max frame rate at the large sizes (TEST_CARD_PS5520_FPS, Nick 10/24).

Run with ``mpremote connect <port> run openmv/probes/n6_bigsize_fps_probe_v5.py``, then
``mpremote connect <port> reset`` (mpremote leaves the rig service stopped). Writes nothing to
/flash. Prints one ``#F {json}`` line per row.

Per size (PS5520: 1280x720 reference, 1280x960, 1920x1080, 2560x1440, 2592x1944; PAG7936: its 4 allowlist sizes):
  - RGB565 (the sensor reads its full 2592x1944 array, the N6 ISP crops to aspect + scales):
    capture-only fps over N_CAP snapshots, then capture + ``compress(quality=70)`` fps and JPEG bytes
    over N_JPEG frames (the stream's per-frame work, without USB);
  - BAYER (no ISP scaling: the sensor's own register mode for that size): capture-only fps.
A failure (MemoryError, "Sensor control failed", ...) is reported as the row's result and the run goes
on. Exposure and the framerate setting are printed, since a long exposure caps the frame rate.
"""
import gc
import json
import time

import sensor

N_CAP = 30
N_JPEG = 15
SIZES = (("1280x720", "HD"), ("1280x960", "SXGAM"), ("1920x1080", "FHD"),
         ("2560x1440", "QHD"), ("2592x1944", "WQXGA2"))
# PAG7936 (0x7936, 1280x800 array): its own allowlist sizes (openmv/n6/board_config.py), 10/24 4.5 mm lens run.
SIZES_BY_SENSOR = {0x7936: (("320x200", "QVGA"), ("640x400", "VGA"), ("960x600", (960, 600)), ("1280x800", "HD"))}


def emit(**kw):
    print("#F", json.dumps(kw))


def setup(pf, name):
    sensor.reset()
    sensor.set_pixformat(getattr(sensor, pf))
    sensor.set_framesize(name if isinstance(name, tuple) else getattr(sensor, name))
    sensor.skip_frames(time=1500)


def info():
    out = {}
    for key, fn in (("exposure_us", sensor.get_exposure_us), ("framerate", sensor.get_framerate)):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = "ERR " + repr(e)
    return out


def run(label, name, pf, jpeg):
    row = {"size": label, "pixformat": pf}
    try:
        setup(pf, name)
        img = sensor.snapshot()
        row.update(w=img.width(), h=img.height(), bytes_raw=img.size(), **info())
        del img
        gc.collect()
        t0 = time.ticks_ms()
        for _ in range(N_CAP):
            sensor.snapshot()
        ms = time.ticks_diff(time.ticks_ms(), t0)
        row.update(cap_frames=N_CAP, cap_ms=ms, cap_fps=round(N_CAP * 1000 / ms, 2))
        if jpeg:
            sizes = []
            t0 = time.ticks_ms()
            for _ in range(N_JPEG):
                img = sensor.snapshot()
                img.compress(quality=70)
                sizes.append(img.size())
            ms = time.ticks_diff(time.ticks_ms(), t0)
            row.update(jpeg_frames=N_JPEG, jpeg_ms=ms, jpeg_fps=round(N_JPEG * 1000 / ms, 2),
                       jpeg_bytes_mean=sum(sizes) // len(sizes))
            del img
        row["ok"] = True
    except Exception as e:
        row.update(ok=False, error=repr(e))
    gc.collect()
    emit(**row)


def main():
    sensor.reset()
    sid = sensor.get_id()
    emit(kind="identity", sensor_id=hex(sid), mem_free=gc.mem_free())
    for label, name in SIZES_BY_SENSOR.get(sid, SIZES):
        if not isinstance(name, tuple) and getattr(sensor, name, None) is None:
            emit(size=label, ok=None, absent=name)
            continue
        run(label, name, "RGB565", True)
        run(label, name, "BAYER", False)
    emit(kind="done")


main()
