"""Reef Check urchin study: video → stills rule (edge_ml/urchin/PREREG.md, E0–E5)."""

import csv
import json
import shutil
import subprocess
import sys

import cv2
import pytest
from edge_ml.urchin import extract_stills as ex


def test_select_keeps_sharpest_per_window():
    # Two 2 s windows of 4 candidates; distinct hashes so nothing is a duplicate.
    sharp = [1, 9, 3, 2, 5, 4, 8, 6]
    hashes = [0, 0, 0, 0, 0, 0, 0xFFFF_FFFF, 0]
    status = ex.select(sharp, hashes)
    assert [i for i, s in enumerate(status) if s == "kept"] == [1, 6]
    assert status.count("not_sharpest") == 6


def test_select_drops_blur_below_half_median():
    # Window bests: 10, 100, 100 → median 100, floor 50 → the first is blur.
    sharp = [10, 1, 1, 1, 100, 1, 1, 1, 100, 1, 1, 1]
    hashes = [0] * 4 + [0xFF] * 4 + [0xFF_FF00_0000] * 4
    status = ex.select(sharp, hashes)
    assert status[0] == "blur"
    assert status[4] == "kept" and status[8] == "kept"


def test_select_drops_near_duplicate_of_previous_kept():
    sharp = [5, 1, 1, 1, 5, 1, 1, 1, 5]
    hashes = [0, 0, 0, 0, 0b111111, 0, 0, 0, 0b1111111]  # 6 bits → dup; 7 bits → new
    status = ex.select(sharp, hashes)
    assert status[0] == "kept"
    assert status[4] == "duplicate"
    assert status[8] == "kept"  # compared with the last KEPT still (index 0), not index 4


def test_select_handles_empty_and_partial_window():
    assert ex.select([], []) == []
    assert ex.select([3.0, 4.0], [0, 0]) == ["not_sharpest", "kept"]


def test_still_name_uses_tenths_of_a_second():
    from pathlib import Path

    assert ex.still_name(Path("/x/GX010042.MP4"), 3) == "GX010042_t000015.jpg"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_inventory_and_extract_on_synthetic_video(tmp_path):
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    video = video_dir / "clip01.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "life=s=320x180:r=10:ratio=0.3:seed=7",
            "-t",
            "6",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    out = tmp_path / "run"
    assert ex.main(["inventory", str(video_dir), str(out)]) == 0
    inv = list(csv.DictReader((out / "inventory.csv").open()))
    assert len(inv) == 1 and inv[0]["error"] == ""
    assert (inv[0]["width"], inv[0]["height"]) == ("320", "180")
    assert len(inv[0]["sha256"]) == 64

    assert ex.main(["extract", str(video_dir), str(out), "--decoder", "software"]) == 0
    rows = list(csv.DictReader((out / "stills.csv").open()))
    assert len(rows) == 12  # 6 s at one candidate per 0.5 s
    kept = [r for r in rows if r["status"] == "kept"]
    assert 1 <= len(kept) <= 3  # at most one per 2 s window
    files = sorted((out / "stills" / "clip01").glob("*.jpg"))
    assert [f.name for f in files] == sorted(r["still"] for r in kept)
    for f in files:
        img = cv2.imread(str(f))
        assert img is not None and img.shape == (180, 320, 3)


def test_find_videos_skips_hidden_appledouble_files(tmp_path):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "clip.MP4").write_bytes(b"x")
    (tmp_path / "site" / "._clip.MP4").write_bytes(b"x")  # exFAT AppleDouble sidecar
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "other.mp4").write_bytes(b"x")
    assert [p.name for p in ex.find_videos(tmp_path)] == ["clip.MP4"]


def _clip(path, codec="libx264"):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "life=s=320x180:r=10:ratio=0.3:seed=7",
            "-t",
            "4",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            codec,
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or sys.platform != "darwin", reason="needs ffmpeg + VideoToolbox"
)
def test_videotoolbox_default_records_decoder_and_refuses_mixing(tmp_path):
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    _clip(video_dir / "c.mp4")
    (video_dir / "._c.mp4").write_bytes(b"\x00" * 4096)  # exFAT sidecar
    out = tmp_path / "out"
    assert ex.main(["extract", str(video_dir), str(out)]) == 0
    run = json.loads((out / "extract_run.json").read_text())
    assert run["decoder"] == "videotoolbox" and run["hidden_skipped"] == 1
    rows = list(csv.DictReader((out / "stills.csv").open()))
    assert {r["decoder"] for r in rows} == {"videotoolbox"}
    with pytest.raises(ValueError, match="refusing 'software'"):
        ex.main(["extract", str(video_dir), str(out), "--decoder", "software"])


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or sys.platform != "darwin", reason="needs ffmpeg + VideoToolbox"
)
def test_videotoolbox_fails_loudly_instead_of_falling_back(tmp_path):
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    _clip(video_dir / "c.mkv", codec="ffv1")  # no VideoToolbox decoder for FFV1
    assert ex.main(["extract", str(video_dir), str(tmp_path / "vt")]) == 1
    assert ex.main(["extract", str(video_dir), str(tmp_path / "sw"), "--decoder", "software"]) == 0


def test_vt_download_format_per_pix_fmt():
    assert ex.vt_download_format("yuvj420p") == "nv12"
    assert ex.vt_download_format("yuv420p10le") == "p010le"
    with pytest.raises(ValueError, match="--decoder software"):
        ex.vt_download_format("yuv422p10le")


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or sys.platform != "darwin", reason="needs ffmpeg + VideoToolbox"
)
def test_videotoolbox_extracts_10bit_hevc(tmp_path):
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    # Apple's own encoder (no GPL x265): 10-bit HEVC, like modern action cameras.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "life=s=320x180:r=10:ratio=0.3:seed=7",
            "-t",
            "4",
            "-pix_fmt",
            "p010le",
            "-c:v",
            "hevc_videotoolbox",
            "-profile:v",
            "main10",
            "-tag:v",
            "hvc1",
            str(video_dir / "h10.mp4"),
        ],
        check=True,
    )
    assert ex.probe(video_dir / "h10.mp4").pix_fmt == "yuv420p10le"
    out = tmp_path / "out"
    assert ex.main(["extract", str(video_dir), str(out)]) == 0
    run = json.loads((out / "extract_run.json").read_text())
    assert run["failed"] == [] and run["kept"] >= 1
