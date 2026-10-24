"""One annotated N6 frame: on-board find_apriltags (FAMILY) drawn on the image (Nick, 10/23).

Run ``mpremote connect <port> run openmv/probes/n6_apriltag_snapshot_v5.py``, then copy
``:tags.jpg`` off and ``fs rm :tags.jpg`` + ``reset``. Prints ``#T {json}``: per tag id, corners,
centre and the detect ms. The N6 draws the tag outlines; ids and ms are printed (draw_string raised a TypeError on v5.0.1, 10/23).
"""
import json
import time

import image
import sensor

SIZE = sensor.HD
FAMILY = "TAG36H11"  # stock v5.0.1 on the N6 defines only TAG36H11 (dir(image), 10/23); no tag25h9
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
    c = t.corners()
    for i in range(4):
        a, b = c[i], c[(i + 1) % 4]
        img.draw_line(a[0], a[1], b[0], b[1], color=(0, 255, 0), thickness=4)
    out.append({"id": t.id(), "cx": t.cx(), "cy": t.cy(), "corners": [list(p) for p in c]})
img.save("/flash/tags.jpg", quality=90)
print("#T", json.dumps({"w": img.width(), "h": img.height(), "detect_ms": ms, "tags": out}))
