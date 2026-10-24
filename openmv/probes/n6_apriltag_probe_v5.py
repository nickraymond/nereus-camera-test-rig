"""On-N6 AprilTags (SPEC_n6_eval must do 4): does ``find_apriltags`` run on v5.0.1, and how fast.

Run with ``mpremote connect <port> run openmv/probes/n6_apriltag_probe_v5.py`` with the tag25h9
board ~1 m in front of the camera in air, then ``mpremote connect <port> reset``. Writes nothing
to /flash. Prints ``#A {json}`` lines.

1. Which tag25h9 family constant the ``image`` module defines (names tried, never assumed).
2. For each Phase 1 framesize x pixformat (GRAYSCALE, RGB565): N_FRAMES snapshots, each timed
   through ``find_apriltags``: decodes per frame, tag ids, ms per frame (median, max), and any
   error (e.g. MemoryError at HD) instead of a crash.
Pass (must do 4): >= 1 decode at 1 m. A missing function or family = F2 (Pi-side detection only).
"""
import gc
import json
import time

import image
import sensor

N_FRAMES = 10
FAMILY_NAMES = ("TAG25H9", "APRILTAG_25H9", "AprilTag25h9")
SIZES = ("HD", "VGA", (960, 600))
PIXFORMATS = ("GRAYSCALE", "RGB565")


def emit(kind, **kw):
    kw["kind"] = kind
    print("#A", json.dumps(kw))


def main():
    found = {n: getattr(image, n) for n in FAMILY_NAMES if hasattr(image, n)}
    has_fn = hasattr(image.Image, "find_apriltags") if hasattr(image, "Image") else None
    emit("api", families=list(found), find_apriltags_on_image_class=has_fn)
    if not found:
        emit("done", verdict="F2: no tag25h9 family constant")
        return
    fam_name = FAMILY_NAMES[[n in found for n in FAMILY_NAMES].index(True)]
    family = found[fam_name]
    for size in SIZES:
        for pf in PIXFORMATS:
            label = (size if isinstance(size, str) else "%dx%d" % size) + " " + pf
            try:
                sensor.reset()
                sensor.set_pixformat(getattr(sensor, pf))
                sensor.set_framesize(getattr(sensor, size) if isinstance(size, str) else size)
                sensor.skip_frames(time=1500)
                ms, decodes, ids = [], [], []
                for _ in range(N_FRAMES):
                    img = sensor.snapshot()
                    t0 = time.ticks_ms()
                    tags = img.find_apriltags(families=family)
                    ms.append(time.ticks_diff(time.ticks_ms(), t0))
                    decodes.append(len(tags))
                    for t in tags:
                        if t.id() not in ids:
                            ids.append(t.id())
                    w, h = img.width(), img.height()
                    del img, tags
                    gc.collect()
                ms.sort()
                emit("run", label=label, family=fam_name, w=w, h=h, frames=N_FRAMES,
                     decodes=decodes, ids=sorted(ids), ms_median=ms[len(ms) // 2],
                     ms_max=ms[-1], ok=max(decodes) >= 1)
            except Exception as e:
                emit("run", label=label, family=fam_name, ok=False, error=repr(e))
                gc.collect()
    emit("done")


main()
