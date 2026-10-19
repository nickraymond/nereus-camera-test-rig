"""edge_ml/pool2/run.py: phases, W1 pass choice, F10 instants, the committed box file, scoring."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from edge_ml.pool2 import card_values, run, wscore

SAMPLES = ("u80a", "u80b", "u50a", "u50b")
GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t"]


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *GIT_ID, *a], check=True, capture_output=True)


def _card(**over):
    d = {"source": "test card", "partb": {"openmv_n6": "420", "openmv_ae3": "420"},
         "first_bin": {}, "guard_cameras": [], "detector": dict(card_values.DETECTOR)}
    d.update(over)
    return d


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo standing in for the rig repo (run.REPO), with a committed card-values file."""
    r = tmp_path / "rig"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "card.json").write_text(json.dumps(_card()))
    _git(r, "add", "card.json")
    _git(r, "commit", "-qm", "card")
    monkeypatch.setattr(run, "REPO", r)
    return r


def _frame(clip, i, t, rng, px80=40.0, px50=25.0, whole=True, pos=None):
    return {"clip": clip, "index": i, "t": t, "range_m": rng, "tags": 4, "board_whole": whole,
            "pos_m": pos if pos is not None else [0.0, 0.0, rng], "mtf50_lpmm": 1.0,
            "de00": {"red": 10.0, "gray_mid": 5.0},
            "samples": {n: {"px": px80 if n.startswith("u80") else px50,
                            "box": [10.0 * i, 0.0, 10.0 * i + 8.0, 8.0]} for n in SAMPLES},
            "edge_w_px": 2.0, "tag_edge_w_px": 2.5}


def _swim(clip="clip001", t0=0.0, start=2.6, stop=0.8, fps=10, holds=(2.0, 1.0)):
    """A slow swim-in from ``start`` to ``stop`` m with 5 s holds at ``holds``, at fps."""
    out, t, r, i = [], t0, start, 0
    while r > stop:
        for h in holds:
            if abs(r - h) < 0.005:
                for _ in range(5 * fps):
                    out.append(_frame(clip, i, round(t, 3), h, px80=60 / h, px50=38 / h))
                    i, t = i + 1, t + 1 / fps
        out.append(_frame(clip, i, round(t, 3), round(r, 3), px80=60 / r, px50=38 / r))
        i, t, r = i + 1, t + 1 / fps, round(r - 0.02, 3)
    return out


def _write_scan(out: Path, phase: str, cam: str, fr: list[dict], chroma: str | None):
    p = out / "scan" / phase / f"{cam}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    clips = sorted({f["clip"] for f in fr})
    p.write_text(json.dumps([{"clip": c, "camera": cam,
                              "chroma": {"subsampling_measured": chroma} if chroma else None,
                              "frames": [f for f in fr if f["clip"] == c]} for c in clips]))


def _manifest(out: Path, run_dir: Path, card: Path | None = None):
    cv = {"sha256": run.sha256_file(card)} if card else None
    (out / "scan" / "manifest.json").write_text(json.dumps(
        {"run_dir": str(run_dir), "layout_sha256": "0" * 64, "layout_measured": True,
         "card_values": cv, "phases": {}}))


def test_phase_clips_by_label_and_by_dir(tmp_path):
    lines = [{"phase": "video@swim1", "video": {"dir": "video/clip003"}},
             {"phase": "video@swim2", "video": {"dir": "video/clip004"}},
             {"phase": "video@swim1", "video": {"dir": "video/clip005"}}]
    (tmp_path / "sets.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n{torn")
    for c in ("clip003", "clip004", "clip005", "clip009"):
        (tmp_path / "video" / c).mkdir(parents=True)
        (tmp_path / "video" / c / "video_sync.json").write_text("{}")
    assert run.phase_clips(tmp_path, "@swim1") == [tmp_path / "video/clip003",
                                                   tmp_path / "video/clip005"]
    assert run.phase_clips(tmp_path, "video/clip009") == [tmp_path / "video/clip009"]
    with pytest.raises(SystemExit, match="video@nope"):
        run.phase_clips(tmp_path, "@nope")


@pytest.mark.parametrize("c1,c2,want,expect", [
    ("420", "420", "420", "pass1"),   # both ran it (N6 under F15) -> pass 1
    ("444", "420", "420", "pass2"),   # AE3: part-B 4:2:0 holds, pass 2 ran it
    ("444", "420", "444", "pass1"),
    ("444", "444", "420", "pass1"),   # F18: both ran the same setting -> pass 1 (QE 008 R3)
    ("mixed", "444", "420", None),    # no pass ran part-B
    ("444", "mixed", "444", "pass1"),
])
def test_w1_pass_follows_the_measured_chroma(tmp_path, c1, c2, want, expect):
    _write_scan(tmp_path, "pass1", "openmv_ae3", [_frame("c1", 0, 0.0, 1.0)], c1)
    _write_scan(tmp_path, "pass2", "openmv_ae3", [_frame("c2", 0, 0.0, 1.0)], c2)
    ph, why = run.w1_pass(tmp_path, "openmv_ae3", {"openmv_ae3": want})
    assert ph == expect, why
    assert run.w1_pass(tmp_path, "imx708", {})[0] == "pass1"


def test_mixed_chroma_in_a_phase_is_not_a_match(tmp_path):
    _write_scan(tmp_path, "pass1", "openmv_n6", [_frame("c1", 0, 0.0, 1.0)], "420;444")
    assert run.phase_chroma(tmp_path, "pass1", "openmv_n6") == "mixed"


def test_f10_instants_flag_disagreeing_ranges_only_within_the_window():
    ae3 = [_frame("c", k, k * 0.1, 2.0) for k in range(10)]
    n6 = [_frame("c", k, k * 0.1 + 0.01, 2.0 if k < 5 else 2.4) for k in range(10)]
    imx = [_frame("c", k, k * 0.1 + 0.2, 2.0) for k in range(10)]  # nearest is 0 ms or 100 ms off
    inst, flagged = run.f10_instants({"openmv_ae3": ae3, "openmv_n6": n6, "imx708": imx})
    assert len(inst) == 10 and sum(i["flag"] for i in inst) == 5
    assert ("openmv_n6", "c", 7) in flagged and ("openmv_ae3", "c", 7) in flagged
    assert ("openmv_n6", "c", 2) not in flagged


def test_hits_need_score_and_iou():
    boxes = {"u80a": {"box": [0, 0, 10, 10]}, "u80b": {"box": [50, 50, 60, 60]}}
    dets = [{"box": [1, 1, 10, 10], "score": 0.4}, {"box": [50, 50, 60, 60], "score": 0.05}]
    h = run.hits_for(boxes, dets, min_score=0.1)
    assert h["u80a"]["hit"] is True and h["u80b"]["hit"] is False  # u80b's det is below 0.1


def test_detect_refuses_an_uncommitted_box_file(repo, tmp_path):
    f = repo / "w1_boxes.json"
    f.write_text("{}")
    with pytest.raises(SystemExit, match="not committed"):
        run.committed_unchanged(f)
    _git(repo, "add", f.name)
    _git(repo, "commit", "-qm", "boxes")
    assert len(run.committed_unchanged(f)) == 40
    f.write_text('{"changed": 1}')
    with pytest.raises(SystemExit, match="differs from HEAD"):
        run.committed_unchanged(f)


def test_commit_check_sees_through_skip_worktree_and_other_repos(repo, tmp_path):
    """QE 008 N2: skip-worktree hides an edit from `git diff`; hash-object doesn't. A file in
    some other repo is refused."""
    f = repo / "card.json"
    _git(repo, "update-index", "--skip-worktree", "card.json")
    f.write_text(json.dumps(_card(source="edited after commit")))
    with pytest.raises(SystemExit, match="differs from HEAD"):
        run.committed_unchanged(f)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / "card.json").write_text("{}")
    _git(other, "add", "card.json")
    _git(other, "commit", "-qm", "x")
    with pytest.raises(SystemExit, match="outside the rig repo"):
        run.committed_unchanged(other / "card.json")


@pytest.mark.parametrize("over,match", [
    ({"first_bin": {"openmv_n6": 0.5}}, "bin centre >= 1.0"),
    ({"first_bin": {"openmv_n6": 1.2}}, "bin centre >= 1.0"),
    ({"partb": {"openmv_n6": "420"}}, "partb"),
    ({"guard_cameras": ["n7"]}, "unknown camera"),
    ({"detector": {**card_values.DETECTOR, "threshold": 0.05}}, "rev 17 pin"),
    ({"detector": {**card_values.DETECTOR, "revision": "main"}}, "rev 17 pin"),
    ({"source": ""}, "source"),
])
def test_card_values_are_checked(tmp_path, over, match):
    f = tmp_path / "card.json"
    f.write_text(json.dumps(_card(**over)))
    with pytest.raises(SystemExit, match=match):
        card_values.load(f)


def test_card_values_fill_first_bins():
    from pathlib import Path as P
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = P(d) / "c.json"
        f.write_text(json.dumps(_card(first_bin={"openmv_ae3": 1.5})))
        c = card_values.load(f)
    assert c.first_bin == {"imx708": 1.0, "openmv_n6": 1.0, "openmv_ae3": 1.5}


def test_clips_of_two_run_folders_never_collide(tmp_path):
    """QE 008 R5: pass 1 runA/clip003 and pass 2 runB/clip003 (F19: a reset, a new run folder)."""
    run_dir = tmp_path / "runA"
    for r in ("runA", "runB"):
        (tmp_path / r / "video" / "clip003").mkdir(parents=True)
        (tmp_path / r / "video" / "clip003" / "video_sync.json").write_text("{}")
    a = run.phase_clips(run_dir, "video/clip003")
    b = run.phase_clips(run_dir, "../runB/video/clip003")
    ids = [run.clip_id(run_dir, c) for c in a + b]
    assert ids == ["video/clip003", "../runB/video/clip003"]
    out = tmp_path / "out"
    (out / "scan").mkdir(parents=True)
    (out / "scan" / "manifest.json").write_text(json.dumps(
        {"run_dir": str(run_dir), "phases": {
            "pass1": {"clips": ids[:1], "dirs": [str(a[0])]},
            "pass2": {"clips": ids[1:], "dirs": [str(b[0])]}}}))
    dirs = run.clip_dirs(out)
    assert dirs[ids[0]] == a[0] and dirs[ids[1]] == b[0]


def test_a_failed_clip_is_lost_not_a_crash(tmp_path):
    """QE 008 N1: sets.jsonl keeps a failed clip's dir; scan lists it as lost."""
    lines = [{"phase": "video@swim1", "video": {"ok": True, "dir": "video/clip001"}},
             {"phase": "video@swim1", "video": {"ok": False, "error": "Timeout",
                                                "dir": "video/clip002"}},
             {"phase": "video@swim1", "video": {"ok": True, "dir": "video/clip003"}}]
    (tmp_path / "sets.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    (tmp_path / "video" / "clip001").mkdir(parents=True)
    (tmp_path / "video" / "clip001" / "video_sync.json").write_text("{}")
    (tmp_path / "video" / "clip003").mkdir(parents=True)   # ok but its summary is missing
    lost: list = []
    assert run.phase_clips(tmp_path, "@swim1", lost) == [tmp_path / "video/clip001"]
    assert [k["clip"] for k in lost] == ["video/clip002", "video/clip003"]
    assert "Timeout" in lost[0]["why"] and lost[1]["why"] == "no video_sync.json"


def test_f10_instant_spans_at_most_50_ms():
    """QE 008 reading 5: N6 +40 ms and IMX708 -40 ms of the AE3 frame span 80 ms: drop the
    farther one."""
    ae3 = [_frame("c", 0, 1.0, 2.0)]
    n6 = [_frame("c", 0, 1.040, 2.0)]
    imx = [_frame("c", 0, 0.955, 2.5)]
    inst, _ = run.f10_instants({"openmv_ae3": ae3, "openmv_n6": n6, "imx708": imx})
    assert len(inst) == 1 and set(inst[0]["ranges"]) == {"openmv_ae3", "openmv_n6"}  # IMX 45 ms off


def test_frame_hits_at_each_rev17_threshold():
    boxes = {"u80a": {"box": [0, 0, 10, 10]}}
    dets = [{"box": [0, 0, 10, 10], "score": 0.2}]
    h = run.frame_hits(boxes, dets)
    assert set(h) == {"0.05", "0.3", "0.5"}
    assert h["0.05"]["u80a"]["hit"] and not h["0.3"]["u80a"]["hit"] and not h["0.5"]["u80a"]["hit"]


def _scored(tmp_path, hit_until_m: float, n6_px80_scale=1.0, card=None, det_over=None):
    """Scan files -> select -> fake detections -> score. ``card``: the committed card file
    (the repo fixture's by default)."""
    card = card or run.REPO / "card.json"
    out = tmp_path / "out"
    swim = _swim()
    for cam in ("openmv_n6", "openmv_ae3"):
        fr = [dict(f) for f in swim]
        if cam == "openmv_n6":
            for f in fr:
                for n in ("u80a", "u80b"):
                    px = f["samples"][n]["px"] * n6_px80_scale
                    f["samples"] = {**f["samples"], n: {**f["samples"][n], "px": px}}
        _write_scan(out, "pass1", cam, fr, "420")
        _write_scan(out, "check", cam, fr, "420")
    _manifest(out, tmp_path, card)
    boxes = tmp_path / "w1_boxes.json"
    cams = "openmv_n6,openmv_ae3"
    run.main(["select", str(out), "--card", str(card), "--boxes", str(boxes), "--cameras", cams])
    b = json.loads(boxes.read_text())
    frames = {}
    for cam, e in b["cameras"].items():
        for picks in e["bins"].values():
            for p in picks or []:
                hit = p["range_m"] <= hit_until_m + 1e-9
                frames[f"{cam}/{p['clip']}/{p['index']}"] = {
                    "dets": [], "hits": {
                        "0.05": {n: {"hit": True} for n in SAMPLES},
                        "0.3": {n: {"hit": hit} for n in SAMPLES},
                        "0.5": {n: {"hit": hit and p["range_m"] <= 1.0} for n in SAMPLES}}}
    (out / "detect").mkdir(parents=True)
    pin = card_values.DETECTOR
    (out / "detect" / "detections.json").write_text(json.dumps(
        {"boxes_sha256": run.sha256_file(boxes), "boxes_commit": "c" * 40, "frames": frames,
         "model_id": pin["model_id"], "model_revision": pin["revision"],
         "query": "purple sea urchin", "detect_floor": 0.05, "threshold": pin["threshold"],
         **(det_over or {})}))
    run.main(["score", str(out), "--card", str(card), "--boxes", str(boxes)])
    return json.loads((out / "score" / "results.json").read_text()), out


def test_select_picks_five_hold_frames_per_bin_and_score_chains(repo, tmp_path):
    res, out = _scored(tmp_path, hit_until_m=2.0)
    b = json.loads((tmp_path / "w1_boxes.json").read_text())
    picks = b["cameras"]["openmv_ae3"]["bins"]["1.0"]
    assert len(picks) == 5 and all(p["range_m"] == 1.0 for p in picks)  # the 1 m hold frames
    w1 = res["w1"]["openmv_ae3"]["80mm"]
    # bins 1.0 .. 2.0 hit; 2.5 m: px 24 < 30 -> the chain ends there on px
    assert w1["range_m"] == 2.0 and w1["end_bin"] == 2.5 and w1["end_reason"] == "px"
    assert res["w1"]["openmv_ae3"]["pass"] == "pass1"
    assert res["w2"]["openmv_ae3"]["pass1_m"] == pytest.approx(2.6)
    assert res["w2"]["openmv_ae3"]["f12"] is False
    md = (out / "score" / "RESULTS.md").read_text()
    assert md.startswith("# Pool test 2 W1-W5 results") and "TEMPLATE" not in md
    assert run.sha256_file(repo / "card.json")[:12] in md
    # rev 17 report-only chains: 0.05 hits everywhere (px ends it at 2.5 m), 0.5 only at 1.0 m
    assert res["w1_report"]["0.05"]["openmv_ae3"]["80mm"]["range_m"] == 2.0
    assert res["w1_report"]["0.5"]["openmv_ae3"]["80mm"]["range_m"] == 1.0
    assert set(res["w1_report"]) == {"0.05", "0.5"}


def test_n6_vs_ae3_longer_range_wins(repo, tmp_path):
    res, _ = _scored(tmp_path, hit_until_m=2.0, n6_px80_scale=0.7)  # N6 px fall under 30 sooner
    assert res["w1"]["openmv_n6"]["80mm"]["range_m"] < res["w1"]["openmv_ae3"]["80mm"]["range_m"]
    assert res["n6_vs_ae3"] == "openmv_ae3"


def test_score_refuses_detections_from_another_box_file(repo, tmp_path):
    _, out = _scored(tmp_path, hit_until_m=2.0)
    boxes = tmp_path / "w1_boxes.json"
    boxes.write_text(boxes.read_text().replace('"stage": "select"', '"stage": "select" '))
    with pytest.raises(SystemExit, match="different box file"):
        run.main(["score", str(out), "--card", str(repo / "card.json"), "--boxes", str(boxes)])


@pytest.mark.parametrize("over", [{"model_revision": "main"}, {"threshold": 0.05},
                                  {"detect_floor": 0.3}])
def test_score_refuses_detections_off_the_rev17_pin(repo, tmp_path, over):
    with pytest.raises(SystemExit, match="rev 17 pin"):
        _scored(tmp_path, hit_until_m=2.0, det_over=over)


def test_select_refuses_a_card_other_than_the_scans(repo, tmp_path):
    other = repo / "card2.json"
    other.write_text(json.dumps(_card(partb={"openmv_n6": "444", "openmv_ae3": "420"})))
    _git(repo, "add", other.name)
    _git(repo, "commit", "-qm", "card2")
    out = tmp_path / "out"
    _write_scan(out, "pass1", "openmv_ae3", [_frame("c1", 0, 0.0, 1.0)], "420")
    _manifest(out, tmp_path, repo / "card.json")
    with pytest.raises(SystemExit, match="re-run from scan"):
        run.main(["select", str(out), "--card", str(other), "--boxes",
                  str(tmp_path / "b.json"), "--cameras", "openmv_ae3"])


def test_blind_answers_must_cover_every_crop():
    blind = {"key": {"crop_0000.png": {"camera": "openmv_n6", "clip": "c", "index": 0,
                                       "sample": "u80a"},
                     "crop_0001.png": {"camera": "openmv_n6", "clip": "c", "index": 1,
                                       "sample": "u80a"}},
             "answers": {"crop_0000.png": True}}
    with pytest.raises(SystemExit, match="miss 1 crop"):
        run._attach({"cameras": {}}, None, blind)


def test_speed_uses_the_3d_pose():
    fr = [{"t": 0.0, "range_m": 1.0, "pos_m": [0.0, 0.0, 1.0]},
          {"t": 0.1, "range_m": 1.0, "pos_m": [0.05, 0.0, 1.0]},
          {"t": 0.2, "range_m": 1.0, "pos_m": [0.10, 0.0, 1.0]}]
    sp = wscore.speeds(fr)
    assert sp[1] == pytest.approx(0.5)  # lateral motion: range alone would read 0


def test_w5_summary_bins_by_speed():
    rows = [{"crossing": "w5_slow", "speed_ms": 0.05, "slanted_edge_w_px": 2.0,
             "tag_edge_w_px": 3.0},
            {"crossing": "w5_slow", "speed_ms": 0.07, "slanted_edge_w_px": 4.0,
             "tag_edge_w_px": None},
            {"crossing": "w5_fast", "speed_ms": 0.9, "slanted_edge_w_px": None,
             "tag_edge_w_px": 9.0}]
    out = run.w5_summary(rows)
    assert out == [{"crossing": "w5_slow", "speed_ms": "0-0.1", "n": 2, "slanted_edge_w_px": 3.0,
                    "tag_edge_w_px": 3.0},
                   {"crossing": "w5_fast", "speed_ms": "0.6-inf", "n": 1,
                    "slanted_edge_w_px": None, "tag_edge_w_px": 9.0}]


def test_w4_images_are_lossless_side_by_side_crops(tmp_path):
    import cv2
    import numpy as np
    from test_pool2_frames import _board_clip
    clip = _board_clip(tmp_path)   # 4 frames of 64 x 40, AE3
    out = tmp_path / "out"
    (out / "scan").mkdir(parents=True)
    (out / "scan" / "manifest.json").write_text(json.dumps(
        {"run_dir": str(tmp_path), "phases": {"pass1": {"dirs": [str(clip)]}}}))
    pairs = [{"pass": "pass1", "hold_m": 1.0, "frame": "clip001/0", "board_box": [0, 0, 32, 20]},
             {"pass": "pass2", "hold_m": 1.0, "frame": "clip001/3", "board_box": [8, 0, 40, 20]}]
    imgs = run.w4_images(out, "openmv_ae3", pairs)
    assert imgs == ["score/w4/openmv_ae3_1m_pass1_vs_pass2.png"]
    im = cv2.imread(str(out / imgs[0]))
    assert im.shape[:2] == (20, 64) and np.abs(im[:, :32].mean() - 0) < 3


def test_score_refuses_without_detections(repo, tmp_path):
    _, out = _scored(tmp_path, hit_until_m=2.0)
    (out / "detect" / "detections.json").unlink()
    with pytest.raises(SystemExit, match="run `detect` first"):
        run.main(["score", str(out), "--card", str(repo / "card.json"), "--boxes",
                  str(tmp_path / "w1_boxes.json")])


def test_scan_refuses_a_card_guard_that_disagrees_with_the_mode(tmp_path, monkeypatch):
    """QE 008 R4: raw corners only for a mode that can't be mapped (the 64 x 40 test clip is
    unmapped, so the card must list the AE3 as a guard camera)."""
    from test_pool2_frames import _board_clip
    clip = _board_clip(tmp_path)
    monkeypatch.setattr(run, "measure_frame", lambda *a, **k: {})
    with pytest.raises(SystemExit, match="says guard=False"):
        run.scan_clip(clip, "openmv_ae3", None, guard=False, cid="video/clip001")
    rec = run.scan_clip(clip, "openmv_ae3", None, guard=True, cid="video/clip001")
    assert rec["clip"] == "video/clip001" and rec["lens"]["mapped"] is False
    assert all(f["clip"] == "video/clip001" for f in rec["frames"])


def test_detect_refuses_when_detections_from_another_box_file_exist(repo, tmp_path):
    card = repo / "card.json"
    boxes = repo / "w1_boxes.json"
    boxes.write_text(json.dumps({"card_values": {"sha256": run.sha256_file(card)},
                                 "cameras": {}}))
    _git(repo, "add", boxes.name)
    _git(repo, "commit", "-qm", "boxes")
    out = tmp_path / "out"
    (out / "detect").mkdir(parents=True)
    (out / "detect" / "detections.json").write_text(json.dumps({"boxes_sha256": "0" * 64}))
    with pytest.raises(SystemExit, match="another box file"):
        run.main(["detect", str(out), "--card", str(card), "--boxes", str(boxes)])
