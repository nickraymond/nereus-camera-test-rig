"""video_clip_table.py: the bars' inputs from the raw clip files (pool 2, QE 005 R2 + nits)."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("video_clip_table_t",
                                               ROOT / "scripts" / "video_clip_table.py")
vct = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vct)


def _sof_jpeg(w, h, y_hv=0x22, ncomp=3):
    """A JPEG header with one SOF0 segment (enough for jpeg_sof), then an EOI."""
    comps = bytes([1, y_hv, 0]) + (bytes([2, 0x11, 1, 3, 0x11, 1]) if ncomp == 3 else b"")
    body = bytes([8]) + h.to_bytes(2, "big") + w.to_bytes(2, "big") + bytes([ncomp]) + comps
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + bytes(9)
    return b"\xff\xd8" + app0 + b"\xff\xc0" + (len(body) + 2).to_bytes(2, "big") + body + \
        b"\xff\xd9"


def test_jpeg_sof_reads_size_and_chroma_format():
    assert vct.jpeg_sof(_sof_jpeg(960, 600, 0x22)) == {"width": 960, "height": 600,
                                                       "subsampling": "420"}
    assert vct.jpeg_sof(_sof_jpeg(640, 400, 0x11))["subsampling"] == "444"
    assert vct.jpeg_sof(_sof_jpeg(640, 400, 0x21))["subsampling"] == "422"
    assert vct.jpeg_sof(_sof_jpeg(640, 400, 0x11, ncomp=1))["subsampling"] == "gray"
    assert vct.jpeg_sof(b"not a jpeg") is None


def test_jpeg_sof_on_real_encoder_output():
    Image = pytest.importorskip("PIL.Image")
    for sub, name in ((0, "444"), (1, "422"), (2, "420")):
        buf = io.BytesIO()
        Image.new("RGB", (64, 40), (200, 30, 90)).save(buf, "JPEG", quality=70, subsampling=sub)
        assert vct.jpeg_sof(buf.getvalue()) == {"width": 64, "height": 40, "subsampling": name}


def test_throttle_void_is_any_sample_not_0x0_spec_rev9():
    assert vct.throttle_void(["0x0", "0x0"]) is False
    assert vct.throttle_void(["0x50000", "0x50000"]) is True    # QE 006 R2: sticky bits count
    assert vct.throttle_void(["0x0", "0x80008", "0x80000"]) is True
    assert vct.throttle_void(["0x0", "0x80000"]) is True        # happened between samples
    assert vct.throttle_void([]) is None and vct.throttle_void(["?"]) is None


def _clip(tmp_path, n_frames=201, seconds=30.0, sub=0x11):
    """A synthetic AE3-only clip: n frames at 10 fps from the fire, real JPEG headers."""
    clip = tmp_path / "clip01"
    clip.mkdir()
    frame = _sof_jpeg(640, 400, sub)
    rows = [{"seq": k, "ticks_ms": 5000 + 100 * k, "host_rx_mono": 0, "bytes": len(frame),
             "width": 640, "height": 400} for k in range(n_frames)]
    with (clip / "openmv_ae3_frames.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (clip / "openmv_ae3.mjpeg").write_bytes(frame * n_frames)
    (clip / "host_stats.csv").write_text("mono_s,cpu_c,load1,mem_avail_mb,cma_free_mb,throttled\n"
                                         "1,50.0,1.0,200,40,0x0\n2,,1.0,190,38,0x0\n")
    rec = {"ok": True, "frames": n_frames, "fps": 10.0, "frames_lost": 0, "bytes": 1,
           "fire_host_mono": 50.0, "first_frame_host_mono": 50.0,
           "board_sync": {"fire_rx_ms": 5000}, "settings": {"fps": 10, "jpeg_quality": 70},
           "dims": "640x400"}
    (clip / "video_sync.json").write_text(json.dumps(
        {"utc": "x", "seconds": seconds, "args": {}, "start_offsets_ms": {"openmv_ae3": 0.0},
         "cameras": {"openmv_ae3": rec}}))
    return clip


def test_fps_clip_and_frame_checks_from_the_raw_files(tmp_path):
    row = vct.clip_rows(_clip(tmp_path, n_frames=201))[0]
    # QE 005 R2: 10.0 fps while streaming, but only 20 s of a 30 s clip
    assert row["fps"] == 10.0 and row["fps_clip"] == 6.7 and row["last_frame_s"] == 20.0
    assert row["resolution_measured"] == "640x400" and row["subsampling_measured"] == "444"
    assert row["subsampling"] == "auto" and row["bad_frames"] == 0
    assert row["framesize_asked"] is None   # the synthetic clip asked no framesize
    assert row["host_throttle_void"] is False and row["host_max_cpu_c"] == 50.0


def test_a_cut_frame_is_counted_bad(tmp_path):
    clip = _clip(tmp_path, n_frames=5, sub=0x22)
    data = (clip / "openmv_ae3.mjpeg").read_bytes()
    (clip / "openmv_ae3.mjpeg").write_bytes(data[:-3])   # the last frame lost its EOI
    row = vct.clip_rows(clip)[0]
    assert row["bad_frames"] == 1 and row["subsampling_measured"] == "420"


def test_an_error_only_record_does_not_crash_the_table(tmp_path):
    clip = tmp_path / "clip01"
    clip.mkdir()
    (clip / "video_sync.json").write_text(json.dumps({"seconds": 30.0, "cameras": {
        "imx708": {"camera": "imx708", "ok": False, "error": "arm: x"},
        "openmv_n6": {"camera": "openmv_n6", "ok": False, "error": "arm: y"}}}))
    rows = vct.clip_rows(clip)
    assert [r["fps_clip"] for r in rows] == [None, None]
    assert rows[0]["host_throttle_void"] is None


def _script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_t", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chroma_pair_picks_by_capture_time_and_refuses_unequal_footprints(tmp_path, capsys):
    cp = _script("chroma_pair")
    a = _clip(tmp_path, n_frames=201)
    fr = cp.frames(a, "openmv_ae3")
    assert cp.nearest(fr, 15.04)["seq"] == 150 and fr[150]["offset"] == 150 * fr[0]["bytes"]
    assert cp.box_ok((0, 0, 640, 400), 640, 400) and not cp.box_ok((600, 350, 160, 100), 640, 400)
    other = tmp_path / "b"
    other.mkdir()
    b = _clip(other, n_frames=10)
    frame = _sof_jpeg(320, 200)       # a different framesize in clip b
    (b / "openmv_ae3.mjpeg").write_bytes(frame * 10)
    import csv as _csv
    rows = list(_csv.DictReader((b / "openmv_ae3_frames.csv").open()))
    for r in rows:
        r["bytes"] = len(frame)
    with (b / "openmv_ae3_frames.csv").open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert cp.main([str(a), str(b), "--t", "0.5", "--crop", "0,0,10,10",
                    "--out", str(tmp_path / "out")]) == 1
    assert "frame sizes differ" in capsys.readouterr().out


def test_matched_frames_skips_a_camera_that_never_started(tmp_path):
    mf = _script("matched_frames")
    clip = _clip(tmp_path)
    sync = json.loads((clip / "video_sync.json").read_text())
    sync["cameras"]["openmv_n6"] = {"camera": "openmv_n6", "ok": False, "error": "arm: x"}
    assert mf.usable_cameras(clip, sync) == ["openmv_ae3"]


def test_chroma_pair_never_lets_two_settings_clip01_overwrite_each_other(tmp_path, capsys):
    cp = _script("chroma_pair")
    for setting in ("ctl_1", "ae3_420"):
        (tmp_path / setting).mkdir()
        _clip(tmp_path / setting, n_frames=10)
    out = tmp_path / "out"
    try:
        cp.main([str(tmp_path / "ctl_1" / "clip01"), str(tmp_path / "ae3_420" / "clip01"),
                 "--t", "0.5", "--crop", "0,0,10,10", "--out", str(out)])
    except Exception:  # noqa: BLE001 - ffmpeg may be missing on CI; the JPEG names come first
        pass
    jpgs = sorted(p.name for p in out.glob("*.jpg"))
    assert jpgs == ["ae3_420_clip01_openmv_ae3_seq0005.jpg", "ctl_1_clip01_openmv_ae3_seq0005.jpg"]
    assert cp.main([str(tmp_path / "ctl_1" / "clip01"), str(tmp_path / "ctl_1" / "clip01"),
                    "--crop", "0,0,10,10", "--out", str(out)]) == 1
