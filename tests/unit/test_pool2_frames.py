"""edge_ml/pool2/frames.py on synthetic clip folders laid out as scripts/sync_video.py does."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from edge_ml.pool2 import frames


def _board_clip(tmp_path, n=4, subsampling=cv2.IMWRITE_JPEG_SAMPLING_FACTOR_420):
    clip = tmp_path / "clip001"
    clip.mkdir()
    rows, blob = [], b""
    for k in range(n):
        img = np.full((40, 64, 3), 20 * k, np.uint8)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90,
                                             cv2.IMWRITE_JPEG_SAMPLING_FACTOR, subsampling])
        blob += buf.tobytes()
        rows.append({"seq": k, "ticks_ms": 1000 + 100 * k, "host_rx_mono": 50.0 + 0.1 * k,
                     "bytes": len(buf), "width": 64, "height": 40})
    (clip / "openmv_ae3.mjpeg").write_bytes(blob)
    with (clip / "openmv_ae3_frames.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sync = {"cameras": {"openmv_ae3": {"fire_host_mono": 10.0,
                                       "board_sync": {"fire_rx_ms": 1000}}}}
    (clip / "video_sync.json").write_text(json.dumps(sync))
    return clip


def test_board_frames_carry_capture_time_and_decode_in_order(tmp_path):
    clip = _board_clip(tmp_path)
    fr = frames.camera_frames(clip, "openmv_ae3")
    assert [f["index"] for f in fr] == [0, 1, 2, 3]
    assert [round(f["t"], 3) for f in fr] == [10.0, 10.1, 10.2, 10.3]  # fire + ticks offset
    vals = [int(img.mean()) for _f, img in frames.iter_images(clip, "openmv_ae3", fr, gray=True)]
    assert vals == pytest.approx([0, 20, 40, 60], abs=2)
    assert frames.camera_frames(clip, "openmv_n6") == []  # not in this clip


def test_board_chroma_is_measured_from_every_frame(tmp_path):
    clip = _board_clip(tmp_path, subsampling=cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444)
    assert frames.board_chroma(clip, "openmv_ae3")["subsampling_measured"] == "444"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_imx_frames_decode_by_metadata_index(tmp_path):
    clip = tmp_path / "clip002"
    clip.mkdir()
    n = 6
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size=64x48:rate=10:duration={n / 10}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-bsf:v", "h264_mp4toannexb", "-f", "h264",
                    str(clip / "imx708.h264")], check=True)
    meta = [{"SensorTimestamp": int((20.0 + 0.1 * k) * 1e9), "ScalerCrop": "(0, 0)/4608x2592"}
            for k in range(n)]
    (clip / "imx708.meta.json").write_text(json.dumps(meta))
    (clip / "video_sync.json").write_text(json.dumps(
        {"cameras": {"imx708": {"fire_host_mono": 20.0, "scaler_crop": "(0, 0)/4608x2592"}}}))
    fr = frames.camera_frames(clip, "imx708")
    assert len(fr) == n and fr[3]["t"] == pytest.approx(20.3)
    got = list(frames.iter_images(clip, "imx708", [fr[1], fr[4]]))
    assert [f["index"] for f, _ in got] == [1, 4] and got[0][1].shape == (48, 64, 3)
    assert frames.imx_scaler_crop(clip) == "(0, 0)/4608x2592"
    meta.append(meta[-1])  # one record too many: the index -> time map would be wrong
    (clip / "imx708.meta.json").write_text(json.dumps(meta))
    with pytest.raises(RuntimeError, match="decoded 6 frames"):
        list(frames.iter_images(clip, "imx708", frames.camera_frames(clip, "imx708")))
