"""Per-pixel temporal median of a clip -> one-frame y4m (numpy).

The static camera's background: moving caustics, the diver and bubbles average out.

    python bg_median.py SRC.mp4 OUT.y4m [--step 3]

Every --step-th frame is used (3 -> 98 frames). Memory ~ frames x 1.4 MB at 720p
(step 3: ~135 MB; step 6: ~68 MB, the Pi setting); the median runs in column chunks so the
temporary copy stays small.
"""

import argparse
import subprocess

import numpy as np

W, H = 1280, 720
CHUNK = 1 << 16  # pixels per median call


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--step", type=int, default=3)
    a = ap.parse_args()
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            a.src,
            "-vf",
            f"select='not(mod(n\\,{a.step}))'",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    fs = W * H * 3 // 2
    frames = np.frombuffer(raw, np.uint8).reshape(-1, fs)
    med = np.empty(fs, np.uint8)
    for i in range(0, fs, CHUNK):
        med[i : i + CHUNK] = np.median(frames[:, i : i + CHUNK], axis=0).round().astype(np.uint8)
    with open(a.out, "wb") as f:
        f.write(f"YUV4MPEG2 W{W} H{H} F10:1 Ip A1:1 C420jpeg\n".encode())
        f.write(b"FRAME\n" + med.tobytes())


if __name__ == "__main__":
    main()
