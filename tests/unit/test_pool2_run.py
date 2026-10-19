"""edge_ml/pool2/run.py: phases, W1 pass choice, F10 instants, the committed box file, scoring."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from edge_ml.pool2 import run, wscore

SAMPLES = ("u80a", "u80b", "u50a", "u50b")


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


def _manifest(out: Path, run_dir: Path):
    (out / "scan" / "manifest.json").write_text(json.dumps(
        {"run_dir": str(run_dir), "layout_sha256": "0" * 64}))


def test_phase_clips_by_label_and_by_dir(tmp_path):
    lines = [{"phase": "video@swim1", "video": {"dir": "video/clip003"}},
             {"phase": "video@swim2", "video": {"dir": "video/clip004"}},
             {"phase": "video@swim1", "video": {"dir": "video/clip005"}}]
    (tmp_path / "sets.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n{torn")
    assert run.phase_clips(tmp_path, "@swim1") == [tmp_path / "video/clip003",
                                                   tmp_path / "video/clip005"]
    assert run.phase_clips(tmp_path, "video/clip009") == [tmp_path / "video/clip009"]
    with pytest.raises(SystemExit, match="video@nope"):
        run.phase_clips(tmp_path, "@nope")


@pytest.mark.parametrize("c1,c2,want,expect", [
    ("420", "420", "420", "pass1"),   # both ran it (N6 under F15) -> pass 1
    ("444", "420", "420", "pass2"),   # AE3: part-B 4:2:0 holds, pass 2 ran it
    ("444", "420", "444", "pass1"),
    ("444", "444", "420", None),      # F18: the switch failed, no pass ran part-B
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


def test_detect_refuses_an_uncommitted_box_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    f = tmp_path / "w1_boxes.json"
    f.write_text("{}")
    with pytest.raises(SystemExit, match="not committed"):
        run.committed_unchanged(f)
    env = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(tmp_path), "add", f.name], check=True)
    subprocess.run(["git", "-C", str(tmp_path), *env, "commit", "-qm", "boxes"], check=True)
    assert len(run.committed_unchanged(f)) == 40
    f.write_text('{"changed": 1}')
    with pytest.raises(SystemExit, match="differs from HEAD"):
        run.committed_unchanged(f)


def _scored(tmp_path, hit_until_m: float, n6_px80_scale=1.0):
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
    _manifest(out, tmp_path)
    boxes = tmp_path / "w1_boxes.json"
    cams = "openmv_n6,openmv_ae3"
    run.main(["select", str(out), "--boxes", str(boxes), "--partb", "openmv_n6=420",
              "--partb", "openmv_ae3=420", "--cameras", cams])
    b = json.loads(boxes.read_text())
    frames = {}
    for cam, e in b["cameras"].items():
        for picks in e["bins"].values():
            for p in picks or []:
                hit = p["range_m"] <= hit_until_m + 1e-9
                frames[f"{cam}/{p['clip']}/{p['index']}"] = {
                    "dets": [], "hits": {n: {"hit": hit} for n in SAMPLES}}
    (out / "detect").mkdir(parents=True)
    (out / "detect" / "detections.json").write_text(json.dumps(
        {"boxes_sha256": run.sha256_file(boxes), "boxes_commit": "c" * 40, "frames": frames,
         "model_id": "m", "model_revision": None, "query": "purple sea urchin",
         "min_score": 0.3}))
    run.main(["score", str(out), "--boxes", str(boxes)])
    return json.loads((out / "score" / "results.json").read_text()), out


def test_select_picks_five_hold_frames_per_bin_and_score_chains(tmp_path):
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
    assert (out / "score" / "RESULTS.md").read_text().startswith("# Pool test 2 W1-W5 results")


def test_n6_vs_ae3_longer_range_wins(tmp_path):
    res, _ = _scored(tmp_path, hit_until_m=2.0, n6_px80_scale=0.7)  # N6 px fall under 30 sooner
    assert res["w1"]["openmv_n6"]["80mm"]["range_m"] < res["w1"]["openmv_ae3"]["80mm"]["range_m"]
    assert res["n6_vs_ae3"] == "openmv_ae3"


def test_score_refuses_detections_from_another_box_file(tmp_path):
    _, out = _scored(tmp_path, hit_until_m=2.0)
    boxes = tmp_path / "w1_boxes.json"
    boxes.write_text(boxes.read_text().replace('"stage": "select"', '"stage": "select" '))
    with pytest.raises(SystemExit, match="different box file"):
        run.main(["score", str(out), "--boxes", str(boxes)])


def test_speed_uses_the_3d_pose():
    fr = [{"t": 0.0, "range_m": 1.0, "pos_m": [0.0, 0.0, 1.0]},
          {"t": 0.1, "range_m": 1.0, "pos_m": [0.05, 0.0, 1.0]},
          {"t": 0.2, "range_m": 1.0, "pos_m": [0.10, 0.0, 1.0]}]
    sp = wscore.speeds(fr)
    assert sp[1] == pytest.approx(0.5)  # lateral motion: range alone would read 0
