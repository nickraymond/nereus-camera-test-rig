"""IMX708 live tag follower (scripts/imx_tags.py, pool 3 spec rev 1+): splitting a growing H.264
stream into frames, taking only the newest IDR, the F7 memory guard, and sync_video's window
summary. Synthetic NAL units and a rendered tag image; no camera, no ffmpeg."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


it = _load("imx_tags_t", "scripts/imx_tags.py")

SPS, PPS = b"\x00\x00\x00\x01\x67\x64\x00\x28", b"\x00\x00\x00\x01\x68\xee\x3c\x80"


def idr(tag=b"I"):
    return b"\x00\x00\x00\x01\x65\x88" + tag * 20     # first_mb_in_slice = 0


def p_slice(first=True, tag=b"P"):
    return b"\x00\x00\x00\x01\x41" + (b"\x9a" if first else b"\x1a") + tag * 20


def stream(n_frames, intra):
    out = b""
    for k in range(n_frames):
        out += SPS + PPS + idr(bytes([65 + k % 26])) if k % intra == 0 else p_slice()
    return out


def test_splitter_numbers_frames_and_returns_each_idr_with_its_sps_pps():
    s = it.H264Frames()
    done = s.feed(stream(12, 5) + p_slice())  # the trailing slice closes frame 11
    assert [k for k, _ in done] == [0, 5, 10]
    k, au = done[1]
    assert au.startswith(SPS + PPS) and au.endswith(idr(b"F")) and s.idrs_seen == 3


def test_splitter_handles_byte_dribble_and_multi_slice_frames():
    data = (SPS + PPS + idr() + p_slice(first=False) + p_slice() + SPS + PPS + idr() + p_slice()
            + p_slice())  # a NAL is complete only when the next start code arrives
    s = it.H264Frames()
    done = []
    for i in range(len(data)):  # one byte at a time, as a file being written
        done += s.feed(data[i:i + 1])
    assert [k for k, _ in done] == [0, 2]
    assert done[0][1] == SPS + PPS + idr() + p_slice(first=False)  # second slice kept


def card_image(ids):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_25h9)
    img = np.full((400, 200 + 220 * len(ids)), 255, np.uint8)
    for n, i in enumerate(ids):
        img[100:280, 100 + 220 * n:280 + 220 * n] = cv2.aruco.generateImageMarker(d, i, 180)
    return img


def run_follower(tmp_path, data_chunks, decode, monkeypatch, mem=500):
    h264, out = tmp_path / "imx708.h264", tmp_path / "imx708_tags.csv"
    h264.write_bytes(b"")
    chunks = list(data_chunks)
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        if chunks:
            with h264.open("ab") as f:
                f.write(chunks.pop(0))
            return False
        return calls["n"] > 50

    monkeypatch.setattr(it, "mem_available_mb", lambda: mem)
    s = it.run(h264, out, [0, 1, 2, 3], 80, poll_s=0, decode=decode, stop=stop)
    return s, list(csv.DictReader(out.open()))


def test_follower_searches_idrs_filters_ids_and_skips_stale_ones(tmp_path, monkeypatch):
    img = card_image([0, 30])
    data = stream(30, 5) + p_slice()
    # the first chunk completes the IDRs at frames 0 and 5 (frame 10's last NAL waits for the
    # next start code): only the newest, 5, is searched; then 10-25 arrive and only 25 is
    s, rows = run_follower(tmp_path, [data[:len(stream(11, 5))], data[len(stream(11, 5)):]],
                           lambda au: img, monkeypatch)
    assert [int(r["frame"]) for r in rows] == [5, 25]
    assert s["idrs_skipped"] == 4 and s["searches"] == 2 and s["idrs_seen"] == 6
    assert json.loads(rows[0]["tag_ids"]) == [0] and json.loads(rows[0]["tag_rej"]) == [30]
    assert len(json.loads(rows[0]["tag_corners"])[0]) == 4
    assert json.loads((tmp_path / "imx708_tags.csv.json").read_text())["exit"] == "stopped"


def test_low_memory_stops_the_follower_before_it_searches(tmp_path, monkeypatch):
    s, rows = run_follower(tmp_path, [stream(12, 5) + p_slice()], lambda au: card_image([0]),
                           monkeypatch, mem=40)
    assert rows == [] and s["exit"].startswith("MemAvailable 40 MB")


def test_a_decode_error_is_counted_and_the_follower_goes_on(tmp_path, monkeypatch):
    def bad(au):
        raise RuntimeError("ffmpeg rc=1")

    s, rows = run_follower(tmp_path, [stream(12, 5) + p_slice()], bad, monkeypatch)
    assert rows == [] and s["errors"] == 1 and "ffmpeg" in s["last_error"]


def test_sync_video_summarises_only_the_synchronized_window(tmp_path):
    sv = _load("sync_video_imx_t", "scripts/sync_video.py")
    imx = sv.ImxVideo(tmp_path, 1280, 720, 30, None, warmup_s=3.0, buffers=4, seconds=2.0,
                      intra=30, bitrate=None, tag_ids=[0, 1, 2, 3])
    assert imx.cmd[1:3] == ["--intra", "30"]

    class Done:
        returncode = 0

    imx.tagger = Done()
    frames = [{"SensorTimestamp": int((9.0 + k / 30) * 1e9)} for k in range(120)]  # 9.0-13.0 s
    with (tmp_path / "imx708_tags.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=it.CSV_FIELDS)
        w.writeheader()
        for k, ids in ((0, [0]), (30, [0, 1]), (60, []), (90, [2])):  # frame 0 is pre-roll
            w.writerow({"frame": k, "idr": 0, "host_mono": 0, "decode_ms": 80 + k, "tag_ms": 200,
                        "tag_ids": json.dumps(ids), "tag_rej": "[]", "tag_corners": "[]"})
    t = imx._tags(frames, fire=10.0, stop_t=11.5)
    assert t["detection_frames"] == 2 and t["hit_pct"] == 50.0      # frames 30 and 60
    assert t["detections_per_s"] == 1.0 and t["h264_decode_ms_median"] == 140.0


def test_imx_flags_parse(tmp_path):
    sv = _load("sync_video_imx_t2", "scripts/sync_video.py")
    a = sv.build_parser().parse_args(["--imx-tags", "--imx-intra", "15", "--imx-bitrate",
                                      "10000000", "--tag-ids", "c1"])
    assert (a.imx_tags, a.imx_intra, a.imx_bitrate) == (True, 15, 10000000)
    assert sv.build_parser().parse_args([]).imx_tags is False
