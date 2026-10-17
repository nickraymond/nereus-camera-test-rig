# Results table (desk emulation; see RESULTS.md)

## moving camera (Reef Check, 3 windows)

| encoder | 100 KB worst / mean VMAF | 500 KB | 1 MB | knee (bracketed) | MJPEG floor (smallest setting) |
|---|---|---|---|---|---|
| IMX708 · H.264 Pi hardware (desk proxy) · 30 fps | 3.7 / 11.8 | 42.2 / 52.3 | 64.8 / 70.9 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| IMX708 · same, no B-frames like the Pi hardware (sensitivity, post-hoc) · 30 fps | 1.8 / 11.6 | 43.0 / 53.1 | 66.4 / 72.0 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| IMX708 · AV1 on the Pi (software) · 30 fps | 34.9 / 41.8 | 70.6 / 76.2 | 81.4 / 85.0 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| N6 · MJPEG on-board hardware JPEG · 15 fps | can't reach (floor 1275 KB) | can't reach (floor 1275 KB) | can't reach (floor 1275 KB) | n/a (missing sizes) | 1275–1868 KB, worst VMAF 64.9 |
| N6 · H.264 on-board (needs custom firmware; desk proxy) · 15 fps | 2.0 / 11.7 | 51.4 / 61.5 | 73.2 / 78.4 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| N6 → Pi · AV1 on the Pi (software) · 15 fps | 39.0 / 47.1 | 74.6 / 81.1 | 85.2 / 89.4 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |
| AE3 · MJPEG on-board software JPEG · 10 fps | can't reach (floor 769 KB) | can't reach (floor 769 KB) | 48.1 / 61.0 | n/a (missing sizes) | 769–801 KB, worst VMAF 20.6 |
| AE3 → Pi · AV1 on the Pi (software) · 10 fps | 41.9 / 50.9 | 76.7 / 84.0 | 86.8 / 91.6 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |

## diver (pool round 3, 2 clips)

| encoder | 100 KB worst / mean VMAF | 500 KB | 1 MB | knee (bracketed) | MJPEG floor (smallest setting) |
|---|---|---|---|---|---|
| AE3 · MJPEG on-board software JPEG · 10 fps | can't reach (floor 812 KB) | can't reach (floor 812 KB) | 50.7 / 50.9 | n/a (missing sizes) | — |
| AE3 → Pi · AV1 on the Pi (software) · 10 fps | 74.1 / 74.7 | 91.3 / 91.5 | 95.1 / 95.2 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |

## pool, our camera (6 clips)

| encoder | 100 KB worst / mean VMAF | 500 KB | 1 MB | knee (bracketed) | MJPEG floor (smallest setting) |
|---|---|---|---|---|---|
| AE3 · MJPEG on-board software JPEG · 10 fps | can't reach (floor 806 KB) | can't reach (floor 806 KB) | 49.4 / 50.1 | n/a (missing sizes) | — |
| AE3 → Pi · AV1 on the Pi (software) · 10 fps | 74.1 / 84.0 | 91.3 / 93.2 | 95.1 / 95.3 | 500 KB–1 MB or above (bracket; still rising at 1 MB) | — |

