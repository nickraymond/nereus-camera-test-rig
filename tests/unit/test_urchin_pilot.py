"""E6 night pilot driver (SPEC_e6_night_pilot rev 3): selection, stop rules, scoring."""

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from edge_ml.urchin import pilot as pl

FOLDERS = ["Oct 2022"] * 2 + ["Oct 2025"] * 3 + ["South Wreck"] * 3


def _cands():
    return [
        {"num": i + 1, "folder": f, "video": f"v{i + 1}.mp4", "path": f"/x/v{i + 1}.mp4"}
        for i, f in enumerate(FOLDERS)
    ]


def _nums(picked):
    return [c["num"] for c in picked]


def test_select_two_per_folder_in_manifest_order():
    marks = {str(i): "yes" for i in range(1, 9)}
    assert _nums(pl.select(_cands(), marks)) == [1, 2, 3, 4, 6, 7]


def test_select_fills_to_six_from_remaining_yes_in_manifest_order():
    marks = {"1": "yes", "2": "no", "3": "yes", "4": "Yes", "5": "yes", "6": "yes", "7": "yes"}
    marks["8"] = "yes"
    # Oct 2022 gives 1; Oct 2025 gives 3, 4; South Wreck gives 6, 7; fill: 5 (before 8).
    assert _nums(pl.select(_cands(), marks)) == [1, 3, 4, 6, 7, 5]


def test_select_unsure_and_unmarked_are_not_yes_and_n_is_variable():
    marks = {"1": "unsure", "3": "yes", "8": "yes", "2": "no"}
    assert _nums(pl.select(_cands(), marks)) == [3, 8]


def test_select_replaces_a_failed_video_by_the_same_rule():
    marks = {str(i): "yes" for i in (1, 3, 4, 5, 6, 7, 8)}
    assert _nums(pl.select(_cands(), marks, replace={3})) == [1, 4, 5, 6, 7, 8]


def test_screen_outcomes():
    assert pl.screen_outcome({}, 8) == "no screen answer"
    assert pl.screen_outcome({"1": "no", "2": "unsure"}, 8) == "no urchin footage"
    assert pl.screen_outcome({"1": "no", "2": "yes"}, 8) is None


def test_wilson_matches_reference_values():
    lo, hi = pl.wilson(60, 100)
    assert lo == pytest.approx(0.5020, abs=1e-4) and hi == pytest.approx(0.6906, abs=1e-4)
    assert pl.wilson(0, 0) == (0.0, 1.0)
    lo, hi = pl.wilson(10, 10)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.7225, abs=1e-4)


def _calls(video, n, usable):
    return [
        {"video": video, "usable": "yes" if i < usable else "no", "urchin": "yes"} for i in range(n)
    ]


def test_verdict_bar_point_and_lower_bound():
    folder_of = {"a": "F1", "b": "F1", "c": "F2"}
    calls = _calls("a", 40, 24) + _calls("b", 40, 24) + _calls("c", 20, 12)  # 60/100
    v = pl.verdict(calls, folder_of, 3)
    assert v["outcome"] == "good enough to work" and v["usable"]["lo"] >= 0.5
    calls = _calls("a", 40, 24) + _calls("b", 40, 23) + _calls("c", 20, 12)  # 59/100
    assert pl.verdict(calls, folder_of, 3)["outcome"] == "not good enough"
    assert "Not a Q1 pass" in pl.verdict_line(v)


def test_verdict_minimum_n_and_folder_recommendations():
    folder_of = {"a": "F1", "b": "F1", "c": "F2"}
    calls = _calls("a", 40, 30) + _calls("b", 40, 30) + _calls("c", 9, 9)
    v = pl.verdict(calls, folder_of, 3)
    assert v["folders"]["F1"]["recommendation"] == "process in full"
    assert v["folders"]["F2"]["recommendation"].startswith("no recommendation (1 video")
    assert v["videos"]["c"]["verdict"] == "too few to say"
    two = pl.verdict(_calls("a", 50, 50) + _calls("b", 50, 50), {"a": "F1", "b": "F1"}, 2)
    assert two["outcome"].startswith("no verdict")
    assert two["folders"]["F1"]["recommendation"].startswith("no recommendation")


def test_video_quality_shares_are_of_window_bests():
    rows = (
        [{"status": "kept", "sharpness": s} for s in ("10", "20", "30")]
        + [{"status": "blur", "sharpness": "1"}]
        + [{"status": "not_sharpest", "sharpness": "5"}] * 12
    )
    q = pl.video_quality(rows, 120.0)
    assert q["kept"] == 3 and q["window_best"] == 4 and q["blur_share"] == 0.25
    assert q["stills_per_min"] == 1.5 and q["median_sharpness"] == 20.0
    assert not pl.video_ok(q, failed=False)


def test_candidate_shares():
    dets = {"a": [{"score": 0.4}], "b": [{"score": 0.06}], "c": []}
    assert pl.candidate_shares(dets, ["a", "b", "c", "d"]) == {
        "cand_share_0.05": 0.5,
        "cand_share_0.3": 0.25,
    }


def test_thermal_parse():
    calm = "Note: No thermal warning level has been recorded\nNote: No CPU power status"
    assert not pl.thermal_limited(calm)
    assert pl.thermal_limited("CPU_Scheduler_Limit \t= 100\nCPU_Speed_Limit \t= 70")
    assert not pl.thermal_limited("CPU_Speed_Limit \t= 100")
    assert pl.thermal_limited("Thermal Warning Level set to 2.")
    assert pl.thermal_limited("Note: No thermal warning level\nPerformance Warning Level set to 1")
    assert not pl.thermal_limited("Note: No performance warning level has been recorded")


def test_wait_cool_pauses_until_clear(monkeypatch):
    monkeypatch.setattr(pl, "THERM_PAUSE_S", 0.0)
    texts = iter(["CPU_Speed_Limit = 50", "CPU_Speed_Limit = 100"])
    notes = []
    pl.wait_cool(notes, "x", therm=lambda: next(texts))
    assert [n["limited"] for n in notes] == [True, False]


def test_env_problem_unmounted(tmp_path):
    assert pl.env_problem(tmp_path, tmp_path / "nope").startswith("SSD unmounted")
    assert pl.env_problem(tmp_path, Path("/")) is None
    assert "GiB" in pl.env_problem(tmp_path, Path("/"), min_free=1e9)


def test_run_watched_stops_on_check():
    assert pl.run_watched([sys.executable, "-c", "pass"], lambda: None, 0.05) == "ok"
    assert pl.run_watched([sys.executable, "-c", "raise SystemExit(3)"], lambda: None) == "rc=3"
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert pl.run_watched(cmd, lambda: "disk low", 0.05) == "disk low"


needs_vt = pytest.mark.skipif(
    not shutil.which("ffmpeg") or sys.platform != "darwin", reason="needs ffmpeg + VideoToolbox"
)


def _clip(path, codec="libx264", seed=7):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"life=s=320x180:r=10:ratio=0.3:seed={seed}",
         "-t", "6", "-pix_fmt", "yuv420p", "-c:v", codec, str(path)],
        check=True,
    )  # fmt: skip


def _run_dir(tmp_path, specs):
    """specs: (folder, file name, codec); all marked yes."""
    src, run = tmp_path / "src", tmp_path / "run"
    src.mkdir()
    run.mkdir()
    cands = []
    for i, (folder, name, codec) in enumerate(specs, start=1):
        _clip(src / name, codec, seed=i)
        cands.append({"num": i, "folder": folder, "video": name, "path": str(src / name)})
    pl.write_json(run / "candidates.json", cands)
    pl.write_json(run / "marks.json", {"marks": {str(c["num"]): "yes" for c in cands}})
    return run


def _fake_owl(run, names):
    dets = {n: [{"box": [10, 10, 60, 60], "score": 0.4 if i % 2 else 0.1}] for i, n in
            enumerate(names)}  # fmt: skip
    pl.write_json(run / "owl_prelabels.json", {"model": "m", "query": "q", "min_score": 0.05,
                                               "detections": dets})  # fmt: skip


def _fill_calls(run):
    for name in ("pilot_sample", "scale_sample"):
        rows = pl.read_csv(run / f"{name}_calls.csv")
        for r in rows:
            r |= {"usable": "yes", "urchin": "unsure", "scale_ref": "none"}
        pl.write_csv(run / f"{name}_calls.csv", rows, list(rows[0]))


@needs_vt
def test_replacement_picked_during_extract_is_scored(tmp_path):
    """QE 005 R1: the real layout, all 8 yes; #3 fails E0, so #5 replaces it and is scored."""
    specs = [(f, f"v{i}.mp4", "libx264") for i, f in enumerate(FOLDERS, start=1)]
    run = _run_dir(tmp_path, specs)
    (tmp_path / "src" / "v3.mp4").write_bytes(b"\x00not a video" * 100)  # unreadable: E0 fails
    assert pl.cmd_select(run) == 0
    assert [c["num"] for c in pl.read_json(run / "pilot_set.json")["videos"]] == [1, 2, 3, 4, 6, 7]
    assert pl.cmd_extract(run, sys.executable, Path("/")) == 0
    state = pl.read_json(run / "run.json")
    assert state["extract"]["3"]["failed"] == "E0 ffprobe"
    assert pl.read_json(run / "pilot_set.json")["extracted"] == [1, 2, 4, 5, 6, 7]
    names = pl.kept_names(run)
    _fake_owl(run, names)
    assert pl.cmd_samples(run) == 0
    _fill_calls(run)
    assert pl.cmd_score(run) == 0
    q = list(csv.DictReader((run / "quality.csv").open()))
    assert [int(r["num"]) for r in q] == [1, 2, 4, 5, 6, 7]
    v = pl.read_json(run / "verdict.json")
    assert v["n_videos"] == 6 and v["folders"]["Oct 2025"]["videos"] == 2
    assert "6 videos" in v["line"]


@needs_vt
def test_extract_stops_when_first_two_videos_fail(tmp_path):
    run = _run_dir(
        tmp_path, [("A", "a1.mkv", "ffv1"), ("A", "a2.mkv", "ffv1"), ("B", "b1.mp4", "libx264")]
    )
    assert pl.cmd_select(run) == 0
    assert pl.cmd_extract(run, sys.executable, Path("/")) == 2
    state = pl.read_json(run / "run.json")
    assert state["events"][-1]["stop"].startswith("first 2 videos")
    assert {e["video"]: e.get("failed") for e in state["extract"].values()} == {
        "a1.mkv": "decode",
        "a2.mkv": "decode",
    }


@needs_vt
def test_pilot_end_to_end_on_synthetic_clips(tmp_path):
    run = _run_dir(
        tmp_path,
        [("A", "a1.mp4", "libx264"), ("A", "a2.mkv", "ffv1"), ("A", "a3.mp4", "libx264"),
         ("B", "b1.mp4", "libx264")],
    )  # fmt: skip
    assert pl.cmd_select(run) == 0
    assert pl.cmd_extract(run, sys.executable, Path("/")) == 0
    state = pl.read_json(run / "run.json")
    # a2 fails to decode (FFV1 has no VideoToolbox decoder) and is replaced by a3 (same folder).
    assert state["extract"]["2"]["failed"] == "decode"
    assert [e["nn"] for e in state["extract"].values()] == ["01", "02", "03", "04"]
    names = pl.kept_names(run)
    assert {n.split("/")[0] for n in names} == {"a1", "a3", "b1"}
    assert all((run / "stills" / n).is_file() for n in names)
    inv = pl.read_csv(run / "inventory.csv")
    assert sorted(r["file"] for r in inv) == ["a1.mp4", "a3.mp4", "b1.mp4"]

    assert pl.read_json(run / "pilot_set.json")["extracted"] == [1, 3, 4]
    _fake_owl(run, names)
    assert pl.cmd_samples(run) == 0
    assert len((run / "pilot_sample.txt").read_text().split()) == len(names)  # < 100: all
    assert list((run / "sheets" / "pilot_sample").glob("still_sheet_*.jpg"))
    with pytest.raises(ValueError, match="not filled"):
        pl.load_calls(run)
    _fill_calls(run)
    assert pl.cmd_score(run) == 0
    v = pl.read_json(run / "verdict.json")
    assert v["n_videos"] == 3 and v["outcome"] == "good enough to work"
    assert v["urchin_present"]["unsure"] == len(names)
    q = list(csv.DictReader((run / "quality.csv").open()))
    assert [r["video"] for r in q] == ["a1.mp4", "a3.mp4", "b1.mp4"]
    assert q[0]["resolution"] == "320x180" and q[0]["scale_ref"].startswith("0/")
    assert pl.cmd_audit(run) == 0
    idx = json.loads((run / "batch" / "audit_index.json").read_text())
    assert len(idx) == len(names) and (run / "batch" / "audit_1.jpg").exists()
