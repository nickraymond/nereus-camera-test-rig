"""E6 full run: per-video extraction from the unpack ledger (SPEC_e6_unpack_tierb rev 1)."""

import csv
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from edge_ml.urchin import archive_zips as az
from edge_ml.urchin import fullrun as fr

needs_vt = pytest.mark.skipif(
    not shutil.which("ffmpeg") or sys.platform != "darwin", reason="needs ffmpeg + VideoToolbox"
)
ROOT = Path("/")  # a real mount point stands in for the SSD


def _clip(path, codec="libx264", seed=7):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"life=s=320x180:r=10:ratio=0.3:seed={seed}",
         "-t", "6", "-pix_fmt", "yuv420p", "-c:v", codec, str(path)],
        check=True,
    )  # fmt: skip


def _unpack_dir(tmp_path, specs, status="unpacked"):
    """specs: (group, file, codec). Writes the videos + an unpack ledger like archive_zips."""
    ud = tmp_path / "ssd" / "unpack"
    rows = []
    for i, (group, name, codec) in enumerate(specs, start=1):
        (ud / group).mkdir(parents=True, exist_ok=True)
        _clip(ud / group / name, codec, seed=i)
        sha = hashlib.sha256((ud / group / name).read_bytes()).hexdigest()
        rows.append({"zip": "z.zip", "member": f"{group}/{name}", "group": group, "file": name,
                     "bytes": 1, "sha256": sha, "status": status, "utc": "t"})  # fmt: skip
    az._write_tsv(ud / "unpack_ledger.tsv", rows, az.LEDGER_FIELDS)
    return ud


def test_video_list_is_the_ledger_latest_row_per_target_sorted(tmp_path):
    rows = [
        {"group": "SW", "file": "b.MP4", "status": "crc_fail: x"},
        {"group": "SW", "file": "b.MP4", "status": "unpacked"},  # later row wins
        {"group": "Oct", "file": "z.MP4", "status": "skipped_identical"},
        {"group": "SW", "file": "a.MP4", "status": "crc_fail: y"},
    ]
    full = [{"zip": "z", "member": "m", "bytes": 1, "sha256": "s", "utc": "t"} | r for r in rows]
    az._write_tsv(tmp_path / "unpack_ledger.tsv", full, az.LEDGER_FIELDS)
    got = [(v["group"], v["file"]) for v in fr.unpacked_videos(tmp_path)]
    assert got == [("Oct", "z.MP4"), ("SW", "b.MP4")]


def test_first_two_rule_uses_ten_stills_per_min():
    ok = {"quality": {"stills_per_min": 10.0}}
    low = {"quality": {"stills_per_min": 9.9}}
    bad = {"failed": "decode", "quality": {"stills_per_min": 30.0}}
    e = lambda d, t: d | {"start_utc": t}  # noqa: E731
    assert fr.first_two_fail([e(low, "1"), e(bad, "2")])
    assert fr.first_two_fail([e(low, "1"), e(ok, "2"), e(low, "0")])  # first 2 by start time
    assert not fr.first_two_fail([e(ok, "1"), e(low, "2")])
    assert not fr.first_two_fail([e(low, "1")])  # one video: no call yet


def test_stops_before_any_write_when_the_ssd_is_unmounted(tmp_path):
    ud = tmp_path / "unpack"
    ud.mkdir()
    row = {"zip": "z", "member": "m", "group": "SW", "file": "a.mp4", "bytes": 1, "sha256": "s",
           "status": "unpacked", "utc": "t"}  # fmt: skip
    az._write_tsv(ud / "unpack_ledger.tsv", [row], az.LEDGER_FIELDS)
    run = tmp_path / "run"
    assert fr.cmd_extract(ud, run, sys.executable, tmp_path / "not_mounted") == 2
    assert "SSD unmounted" in fr.read_json(run / "run.json")["events"][-1]["stop"]
    assert not (ud / "extract").exists() and not (run / "inputs").exists()


@needs_vt
def test_extract_end_to_end_one_video_per_call(tmp_path):
    ud = _unpack_dir(tmp_path, [("South Wreck", "b.mp4", "libx264"),
                                ("October 2022", "a.MP4", "libx264")])  # fmt: skip
    run = tmp_path / "mac" / "run"
    assert fr.cmd_extract(ud, run, sys.executable, ROOT) == 0
    state = fr.read_json(run / "run.json")
    assert [(e["nn"], e["video"]) for e in state["extract"].values()] == [
        ("01", "a.MP4"),
        ("02", "b.mp4"),
    ]  # sorted by group, then file
    assert state["events"][-1]["u6_extra"] == []
    for nn in ("01", "02"):  # stills and per-video records live on the "SSD"
        assert (ud / "extract" / nn / "extract_run.json").exists()
        assert len(list((run / "inputs" / nn).iterdir())) == 1  # one video per call
    stills = list((ud / "extract").rglob("*.jpg"))
    assert stills and all(not s.is_symlink() for s in stills)
    assert [p for p in run.rglob("*.jpg") if not p.is_symlink()] == []  # no still on the Mac
    q = list(csv.DictReader((run / "quality.csv").open()))
    assert [r["video"] for r in q] == ["a.MP4", "b.mp4"]
    assert all(float(r["stills_per_min"]) > 0 and r["failed"] == "" for r in q)
    merged = list(csv.DictReader((run / "stills.csv").open()))
    assert {r["video"] for r in merged} == {"a.MP4", "b.mp4"}
    assert (run / "stills" / "a").is_symlink() and any((run / "stills" / "a").iterdir())
    assert fr.cmd_extract(ud, run, sys.executable, ROOT) == 0  # resume: nothing re-extracted
    assert fr.read_json(run / "run.json")["extract"] == state["extract"]


@needs_vt
def test_stops_when_neither_of_the_first_two_videos_works(tmp_path):
    ud = _unpack_dir(tmp_path, [("A", "a1.mkv", "ffv1"), ("A", "a2.mkv", "ffv1"),
                                ("B", "b1.mp4", "libx264")])  # fmt: skip
    run = tmp_path / "run"
    assert fr.cmd_extract(ud, run, sys.executable, ROOT) == 2
    state = fr.read_json(run / "run.json")
    assert state["events"][-1]["stop"].startswith("neither of the first 2 videos")
    assert {e["video"]: e.get("failed") for e in state["extract"].values()} == {
        "a1.mkv": "decode",
        "a2.mkv": "decode",
    }  # b1 never started
    assert [r["failed"] for r in csv.DictReader((run / "quality.csv").open())] == ["decode"] * 2


@needs_vt
def test_stops_when_the_file_is_not_the_one_the_ledger_recorded(tmp_path):
    ud = _unpack_dir(tmp_path, [("A", "a.mp4", "libx264")])
    (ud / "A" / "a.mp4").unlink()
    _clip(ud / "A" / "a.mp4", seed=99)  # replaced after unpack
    run = tmp_path / "run"
    assert fr.cmd_extract(ud, run, sys.executable, ROOT) == 2
    assert "differs from unpack_ledger.tsv" in fr.read_json(run / "run.json")["events"][-1]["stop"]
    assert not (ud / "extract" / "01" / "stills").exists()
