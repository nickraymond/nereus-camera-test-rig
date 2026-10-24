"""On-N6 AprilTags (SPEC_n6_eval must do 4): does ``find_apriltags`` run on v5.0.1, and how fast.

Run with ``mpremote connect <port> run openmv/probes/n6_apriltag_probe_v5.py`` with the tag25h9
board ~1 m in front of the camera in air, then ``mpremote connect <port> reset``. Writes nothing
to /flash. Prints ``#A {json}`` lines.

1. Which tag25h9 family constant the ``image`` module defines (names tried, never assumed).
2. For each Phase 1 framesize x pixformat (GRAYSCALE, RGB565): N_FRAMES snapshots, each timed
   through ``find_apriltags``: decodes per frame, tag ids, ms per frame (median, max), and any
   error (e.g. MemoryError at HD) instead of a crash.
3. ROI (bench 1, 10/24): at HD GRAYSCALE, the tags found on a full frame give (a) one box around all
   of them and (b) one small box per tag (the "track near the last position" case); each is timed
   with ``find_apriltags(roi=...)`` over N_FRAMES. The snapshot ms is reported for every run.
Pass (must do 4): >= 1 decode at 1 m. A missing function or family = F2 (Pi-side detection only).
"""
import gc
import json
import time

import image
import sensor


def val(a):
    return a() if callable(a) else a  # method on some firmware, property on others (QE N2)


N_FRAMES = 10
FAMILY_NAMES = ("TAG25H9", "APRILTAG_25H9", "AprilTag25h9")
SIZES = ("HD", (960, 600), "VGA", "QVGA")
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
                ms, decodes, ids, snap = [], [], [], []
                for _ in range(N_FRAMES):
                    ts = time.ticks_ms()
                    img = sensor.snapshot()
                    t0 = time.ticks_ms()
                    snap.append(time.ticks_diff(t0, ts))
                    tags = img.find_apriltags(families=family)
                    ms.append(time.ticks_diff(time.ticks_ms(), t0))
                    decodes.append(len(tags))
                    for t in tags:
                        if val(t.id) not in ids:
                            ids.append(val(t.id))
                    w, h = img.width(), img.height()
                    del img, tags
                    gc.collect()
                ms.sort()
                snap.sort()
                emit("run", label=label, family=fam_name, w=w, h=h, frames=N_FRAMES,
                     decodes=decodes, ids=sorted(ids), ms_median=ms[len(ms) // 2],
                     ms_max=ms[-1], snap_ms_median=snap[len(snap) // 2], ok=max(decodes) >= 1)
            except Exception as e:
                emit("run", label=label, family=fam_name, ok=False, error=repr(e))
                gc.collect()
    roi_runs(family, fam_name)
    emit("done")


def _box(pts, margin, w, h):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0 = max(0, min(xs) - margin), max(0, min(ys) - margin)
    x1, y1 = min(w, max(xs) + margin), min(h, max(ys) + margin)
    return (x0, y0, x1 - x0, y1 - y0)


def roi_runs(family, fam_name, margin=40):
    try:
        sensor.reset()
        sensor.set_pixformat(sensor.GRAYSCALE)
        sensor.set_framesize(sensor.HD)
        sensor.skip_frames(time=1500)
        img = sensor.snapshot()
        w, h = img.width(), img.height()
        tags = img.find_apriltags(families=family)
        corners = [list(val(t.corners)) for t in tags]
        del img, tags
        gc.collect()
        if not corners:
            emit("roi", ok=False, error="no tags on the full frame")
            return
        cases = [("all_tags_box", [_box([p for c in corners for p in c], margin, w, h)]),
                 ("per_tag_boxes", [_box(c, margin, w, h) for c in corners])]
        for name, rois in cases:
            ms, decodes = [], []
            for _ in range(N_FRAMES):
                img = sensor.snapshot()
                t0 = time.ticks_ms()
                n = 0
                for r in rois:
                    n += len(img.find_apriltags(roi=r, families=family))
                ms.append(time.ticks_diff(time.ticks_ms(), t0))
                decodes.append(n)
                del img
                gc.collect()
            ms.sort()
            emit("roi", label="HD GRAYSCALE " + name, family=fam_name, rois=rois, frames=N_FRAMES,
                 decodes=decodes, ms_median=ms[len(ms) // 2], ms_max=ms[-1],
                 roi_pixels=sum(r[2] * r[3] for r in rois), ok=min(decodes) == len(corners))
    except Exception as e:
        emit("roi", ok=False, error=repr(e))


main()
