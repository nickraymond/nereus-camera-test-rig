"""One annotated N6 frame: on-board find_apriltags (FAMILY) drawn on the image (Nick, 10/23).

Run ``mpremote connect <port> run openmv/probes/n6_apriltag_snapshot_v5.py``, then copy
``:tags.jpg`` off and ``fs rm :tags.jpg`` + ``reset``. Prints ``#T {json}``: per tag id, corners,
centre and the detect ms. The frame is saved unannotated; the N6's own ids + corners are printed and drawn on the Mac
(draw_string and draw_line both raised TypeError on v5.0.1 with the v4 call style, 10/23).
"""
import json
import time

import image
import sensor


def val(a):
    return a() if callable(a) else a  # method on some firmware, property on others (QE N2)


SIZE = sensor.HD
FAMILY = "TAG25H9"  # card V3; needs the N6 build with IMLIB_ENABLE_APRILTAGS_TAG25H9 (openmv/firmware/0001); stock v5.0.1 has only TAG36H11
family = getattr(image, FAMILY)
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(SIZE)
sensor.skip_frames(time=2000)
img = sensor.snapshot()
t0 = time.ticks_ms()
tags = img.find_apriltags(families=family)
ms = time.ticks_diff(time.ticks_ms(), t0)
out = []
for t in tags:
    c = val(t.corners)
    out.append({"id": val(t.id), "cx": val(t.cx), "cy": val(t.cy), "corners": [list(p) for p in c]})
img.save("/flash/tags.jpg", quality=90)
print("#T", json.dumps({"w": img.width(), "h": img.height(), "detect_ms": ms, "tags": out}))
