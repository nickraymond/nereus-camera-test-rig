"""Pool test 2 W1-W5 scoring driver (docs/pool2_20261021/SPEC.md rev 13+), Mac, after the dive.

Stages, in order; each reads the previous stage's saved output under OUT (a run folder in
~/NereusData/nereus-camera-test-rig/, never in git):

  scan    every frame of every clip of each phase, per camera: tags, range, pose, whole-board,
          sample px + boxes, MTF50, edge widths, patch CIEDE2000 (measure.py)   -> OUT/scan/
  select  W1's pass per camera (the one that ran its part-B chroma), F10 flags across cameras,
          the 5 frames per bin, and the reference-box file                     -> BOXES (json)
  (commit BOXES to git: rev 13 "the box file is written and committed before the detector runs")
  detect  refuses unless BOXES and CARD are committed and unchanged; OWLv2 "purple sea urchin"
          at the rev 17 pin, every box >= 0.05 kept; a sample is hit at threshold T when a
          detection >= T has IoU >= 0.5 with its reference box (T = 0.05, 0.3, 0.5) -> OUT/detect/
  crops   (only if the fallback triggers) shuffled sample crops under neutral names for the blind
          viewer + a separate key                                               -> OUT/crops/
  score   W1 per camera (80 + 50 mm), N6 vs AE3, fallback, W2 + F12, F10, W3, W4, W5 tables, and
          RESULTS.md with every input's sha256                                  -> OUT/score/

Phases: --phase NAME=SPEC, NAME in pass1 | pass2 | check | w5_slow | w5_fast; SPEC = @label
(the clips whose sets.jsonl phase is "video@label") or comma-separated clip dirs relative to
RUN_DIR. A clip is named by its path relative to RUN_DIR (QE 008 R5), so clips of two run folders
never collide; a clip that failed (no video_sync.json) is listed as lost, not scanned (N1).

CARD = the Wed card's values (card_values.py: part-B chroma, first bins, guard cameras, detector
pin), committed in the rig repo before the dive (QE 008 R4). Every stage checks it. The card and
box files live in docs/pool2_20261021/ (results/ is git-ignored). Usage:

  python -m edge_ml.pool2.run scan   RUN_DIR OUT --card CARD --layout L.yaml --phase pass1=@swim1 ..
  python -m edge_ml.pool2.run select OUT --card CARD --boxes docs/pool2_20261021/w1_boxes.json
  python -m edge_ml.pool2.run detect OUT --card CARD --boxes ...
  python -m edge_ml.pool2.run score  OUT --card CARD --boxes ... [--blind answers.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from edge_ml.pool2 import board, card_values, frames, pose, wscore
from edge_ml.pool2.layout import load_layout

log = logging.getLogger("pool2.run")
ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT                # the repo the box and card files must be committed in (tests: tmp)
PHASES = ("pass1", "pass2", "check", "w5_slow", "w5_fast")
MATCH_WINDOW_S = 0.050     # spec rev 5: three frames within 50 ms are "the same instant"
W4_HOLDS_M = (3.0, 2.0, 1.0)
CROP_PAD = 0.25            # blind crops: the reference box + 25 % each side


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_state() -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True,
                              text=True).stdout.strip()
    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--",
                                                               "edge_ml/pool2", "scripts"))}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def _read(path: Path):
    return json.loads(Path(path).read_text())


# -- phases -> clips -----------------------------------------------------------------------------

def phase_clips(run_dir: Path, spec: str, lost: list | None = None) -> list[Path]:
    """@label -> the clips sets.jsonl recorded under phase 'video@label'; else clip dirs. A clip
    whose sync_video failed (sets.jsonl ok false, or no video_sync.json) goes to ``lost`` as
    {"clip", "why"} instead (QE 008 N1: F19's "that pass is lost")."""
    if spec.startswith("@"):
        want = f"video@{spec[1:]}"
        cand = []
        for raw in (run_dir / "sets.jsonl").read_text(errors="replace").splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            v = line.get("video") or {}
            if line.get("phase") == want and v.get("dir"):
                cand.append((run_dir / v["dir"], None if v.get("ok", True) else
                             f"sets.jsonl ok false: {str(v.get('error'))[:120]}"))
        if not cand:
            raise SystemExit(f"no clip in {run_dir / 'sets.jsonl'} has phase {want!r}")
    else:
        cand = [(run_dir / c, None) for c in spec.split(",") if c]
    out = []
    for clip, why in cand:
        if why is None and not (clip / "video_sync.json").exists():
            why = "no video_sync.json"
        if why is None:
            out.append(clip)
        elif lost is not None:
            lost.append({"clip": clip_id(run_dir, clip), "why": why})
    return out


def clip_id(run_dir: Path, clip: Path) -> str:
    """A clip's name everywhere downstream: its path relative to RUN_DIR (QE 008 R5)."""
    return os.path.relpath(clip, run_dir)


def _kv(items: list[str], conv=str) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if not v:
            raise SystemExit(f"expected KEY=VALUE, got {it!r}")
        out[k] = conv(v)
    return out


# -- scan ----------------------------------------------------------------------------------------

def scan_clip(clip: Path, cam: str, lay, colour: bool = True, guard: bool | None = None,
              cid: str | None = None) -> dict:
    """``guard``: the Wed card says this camera is on the rev 16 guard (raw corners); the clip's
    mode must agree, else SystemExit (QE 008 R4). ``cid``: the clip's id (clip_id)."""
    cid = cid or clip.name
    sync = frames.load_sync(clip)
    fr = frames.camera_frames(clip, cam, sync)
    out = {"clip": cid, "camera": cam, "frames": [], "chroma": None,
           "files_sha256": {}}
    if not fr:
        return out
    if cam == "imx708":
        crop = pose.parse_scaler_crop(frames.imx_scaler_crop(clip, sync))
        files = ("imx708.h264", "imx708.meta.json")
    else:
        crop = None
        out["chroma"] = frames.board_chroma(clip, cam)
        files = (f"{cam}.mjpeg", f"{cam}_frames.csv")
    out["files_sha256"] = {f: sha256_file(clip / f) for f in files + ("video_sync.json",)}
    lens = None
    t0 = time.monotonic()
    for f, img in frames.iter_images(clip, cam, fr):
        if img is None:
            out["frames"].append({"clip": cid, "index": f["index"], "t": f["t"],
                                  "decode_error": True, "range_m": None, "tags": 0})
            continue
        if lens is None:
            h, w = img.shape[:2]
            lens = pose.lens_for(cam, w, h, crop)
            if guard is not None and guard == lens.mapped:
                raise SystemExit(
                    f"{cid} {cam} {w}x{h}: the Wed card says guard={guard}, but this mode "
                    f"{'maps' if lens.mapped else 'does not map'} ({lens.why}). Rev 16: raw "
                    "corners only for a mode that can't be mapped; fix the card before scoring")
            out.update(width=w, height=h, scaler_crop=crop, lens=lens.record())
        rec = {"clip": cid, "index": f["index"], "t": round(f["t"], 4)}
        rec.update(measure_frame(img, lens, lay, colour))
        out["frames"].append(rec)
    log.info("%s %s: %d frames scanned in %.0f s", cid, cam, len(out["frames"]),
             time.monotonic() - t0)
    return out


def measure_frame(img, lens, lay, colour):  # indirection so tests can stub the measurement
    from edge_ml.pool2 import measure
    return measure.measure_frame(img, lens, lay, colour)


def load_card(path: str | Path) -> tuple[card_values.CardValues, str]:
    """The Wed card's values, committed and unchanged in REPO (R4); (values, commit)."""
    commit = committed_unchanged(Path(path))
    return card_values.load(path), commit


def cmd_scan(a) -> int:
    card, card_commit = load_card(a.card)
    lay = load_layout(a.layout, allow_template=a.allow_template)
    phases = _kv(a.phase)
    bad = sorted(set(phases) - set(PHASES))
    if bad:
        raise SystemExit(f"unknown phase(s) {bad}; use {PHASES}")
    out, run_dir = Path(a.out), Path(a.run_dir)
    man = {"stage": "scan", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "code": code_state(), "run_dir": str(run_dir), "layout": str(lay.path),
           "layout_sha256": lay.sha256, "layout_measured": lay.measured,
           "card": lay.card.card_id, "card_truth": lay.card.truth_source,
           "tag_edge_mm": lay.tag_edge_mm,
           "card_values": {**card.record(), "commit": card_commit}, "phases": {}, "lenses": {}}
    for name, spec in phases.items():
        lost: list = []
        clips = phase_clips(run_dir, spec, lost)
        ids = [clip_id(run_dir, c) for c in clips]
        man["phases"][name] = {"spec": spec, "clips": ids, "dirs": [str(c) for c in clips],
                               "lost": lost}
        for k in lost:
            log.warning("%s: clip %s lost (%s), not scanned", name, k["clip"], k["why"])
        for cam in a.cameras.split(","):
            res = [scan_clip(c, cam, lay, colour=not a.no_colour,
                             guard=cam in card.guard_cameras, cid=i) for c, i in zip(clips, ids)]
            for r in res:
                if r.get("lens"):
                    man["lenses"].setdefault(cam, {})[f"{r['width']}x{r['height']}"] = r["lens"]
            _write(out / "scan" / name / f"{cam}.json", res)
    _write(out / "scan" / "manifest.json", man)
    print(f"scan: {out / 'scan'} ({', '.join(phases)})")
    return 0


def load_scan(out: Path, phase: str, cam: str) -> list[dict]:
    """All frames of one camera in one phase, in time order (clips concatenated)."""
    p = out / "scan" / phase / f"{cam}.json"
    if not p.exists():
        return []
    fr = [f for clip in _read(p) for f in clip["frames"] if not f.get("decode_error")]
    return sorted(fr, key=lambda f: f["t"])


def phase_chroma(out: Path, phase: str, cam: str) -> str | None:
    """A board's measured chroma over the phase's clips ('420', '444'; 'mixed' if it differs)."""
    p = out / "scan" / phase / f"{cam}.json"
    if not p.exists():
        return None
    subs = {(c.get("chroma") or {}).get("subsampling_measured") for c in _read(p)}
    subs.discard(None)
    if not subs:
        return None
    return subs.pop() if len(subs) == 1 and ";" not in next(iter(subs)) else "mixed"


# -- F10 -----------------------------------------------------------------------------------------

def f10_instants(per_cam: dict[str, list[dict]], key: str = "range_m"
                 ) -> tuple[list[dict], set]:
    """Matched instants: each frame of the slowest stream (AE3, else N6, else IMX708) with a range,
    with every other camera's nearest ranged frame within MATCH_WINDOW_S; the instant's frames span
    <= MATCH_WINDOW_S in all (rev 5; QE 008 reading 5: the farthest pick is dropped until they do).
    Reading (listed for PE 005): an instant with 2 cameras is judged too. Returns the instants
    with >= 2 ranges and the (camera, clip, index) keys of the frames flagged (> 10 %). ``key``:
    range_m (rev 16, undistorted corners: decisions) or range_raw_m (raw corners: report)."""
    ranged = {c: [f for f in fr if f.get(key)] for c, fr in per_cam.items()}
    ref_cam = next((c for c in ("openmv_ae3", "openmv_n6", "imx708") if ranged.get(c)), None)
    if ref_cam is None:
        return [], set()
    times = {c: np.array([f["t"] for f in fr]) for c, fr in ranged.items() if fr}
    instants, flagged = [], set()
    for f in ranged[ref_cam]:
        picks = {ref_cam: f}
        for c, ts in times.items():
            if c == ref_cam:
                continue
            k = int(np.argmin(np.abs(ts - f["t"])))
            if abs(ts[k] - f["t"]) <= MATCH_WINDOW_S:
                picks[c] = ranged[c][k]
        while len(picks) > 2 and (max(p["t"] for p in picks.values())
                                  - min(p["t"] for p in picks.values())) > MATCH_WINDOW_S + 1e-9:
            far = max((c for c in picks if c != ref_cam), key=lambda c: abs(picks[c]["t"] - f["t"]))
            del picks[far]
        if len(picks) < 2:
            continue
        flag = wscore.f10_flags({c: p[key] for c, p in picks.items()})
        instants.append({"t": f["t"], "ranges": {c: p[key] for c, p in picks.items()},
                         "flag": flag})
        if flag:
            flagged |= {(c, p["clip"], p["index"]) for c, p in picks.items()}
    return instants, flagged


def pair_ratios(instants: list[dict]) -> dict[str, float]:
    """Median range ratio per camera pair over the matched instants (report-only, rev 16)."""
    out = {}
    for a, b in (("openmv_n6", "openmv_ae3"), ("imx708", "openmv_n6"), ("imx708", "openmv_ae3")):
        v = [i["ranges"][a] / i["ranges"][b] for i in instants
             if a in i["ranges"] and b in i["ranges"]]
        if v:
            out[f"{a}/{b}"] = round(float(np.median(v)), 4)
    return out


def _f10_summary(instants: list[dict]) -> dict:
    flags = [i["flag"] for i in instants]
    return {"instants": len(instants), "flagged": sum(flags),
            "share": round(sum(flags) / len(flags), 3) if flags else None,
            "pass_unmeasured": wscore.f10_pass_unmeasured(flags),
            "pair_ratio_median": pair_ratios(instants)}


# -- select --------------------------------------------------------------------------------------

def w1_pass(out: Path, cam: str, partb: dict) -> tuple[str | None, str]:
    """Rev 13: W1 runs on the pass that ran the camera's part-B chroma. "If both passes ran the
    same setting for that camera (always the IMX708; the N6 under F15; any camera under F18), W1
    uses pass 1" (QE 008 R3: even when that setting isn't part-B's, e.g. a failed F18 switch)."""
    if cam == "imx708":
        return "pass1", "IMX708: same setting on both passes -> pass 1"
    want = partb.get(cam)
    if want is None:
        raise SystemExit(f"the card's partb has no {cam} (the Wed card's part-B chroma)")
    c1, c2 = phase_chroma(out, "pass1", cam), phase_chroma(out, "pass2", cam)
    if c1 is not None and c1 == c2 and c1 != "mixed":
        return "pass1", f"both passes measured {c1} -> pass 1" + (
            "" if c1 == want else f" (not part-B {want}: F15 / F18)")
    if c1 == want:
        return "pass1", f"pass 1 measured {c1} = part-B {want} (pass 2 measured {c2})"
    if c2 == want:
        return "pass2", f"pass 2 measured {c2} = part-B {want} (pass 1 measured {c1})"
    return None, f"no pass ran part-B {want} (pass 1 {c1}, pass 2 {c2}): W1 not measured"


def _same_card(card: card_values.CardValues, rec: dict | None, where: str) -> None:
    if not rec or rec.get("sha256") != card.sha256:
        raise SystemExit(f"{where} was made with card values {str((rec or {}).get('sha256'))[:12]}"
                         f", not {card.path} ({card.sha256[:12]}): re-run from scan")


def cmd_select(a) -> int:
    out = Path(a.out)
    man = _read(out / "scan" / "manifest.json")
    card, card_commit = load_card(a.card)
    _same_card(card, man.get("card_values"), "the scan")
    cams = a.cameras.split(",")
    partb, first = card.partb, card.first_bin
    flagged_by_pass, f10 = {}, {}
    for ph in ("pass1", "pass2", "check"):
        per = {c: load_scan(out, ph, c) for c in cams}
        if not any(per.values()):
            continue
        inst, flagged = f10_instants(per)
        flagged_by_pass[ph] = flagged
        f10[ph] = {**_f10_summary(inst),
                   "raw_corners": _f10_summary(f10_instants(per, key="range_raw_m")[0])}
    res = {"stage": "select", "scan_manifest_sha256": sha256_file(out / "scan" / "manifest.json"),
           "layout_sha256": man["layout_sha256"], "layout_measured": man.get("layout_measured"),
           "card_values": {**card.record(), "commit": card_commit},
           "lenses": man.get("lenses"), "lost": {k: v.get("lost") for k, v in
                                                  (man.get("phases") or {}).items()},
           "code": code_state(), "f10": f10, "cameras": {}}
    for cam in cams:
        ph, why = w1_pass(out, cam, partb)
        entry = {"w1_pass": ph, "why": why, "first_bin": first[cam], "bins": {}}
        if ph:
            fr = load_scan(out, ph, cam)
            for f in fr:
                f["f10"] = (cam, f["clip"], f["index"]) in flagged_by_pass.get(ph, set())
            sp = wscore.speeds(fr)
            for f, s in zip(fr, sp):
                f["speed_ms"] = round(s, 4) if s is not None else None
            for c in wscore.BIN_CENTRES:
                sel = wscore.select_bin(fr, c)
                entry["bins"][str(c)] = None if sel is None else [
                    {k: f.get(k) for k in ("clip", "index", "t", "range_m", "speed_ms", "tags",
                                           "samples", "mtf50_lpmm", "de00", "f10")} for f in sel]
        res["cameras"][cam] = entry
    _write(Path(a.boxes), res)
    print(f"select: wrote {a.boxes} (sha256 {sha256_file(Path(a.boxes))[:12]}). Commit it before"
          " `detect` (rev 13).")
    return 0


# -- detect --------------------------------------------------------------------------------------

def committed_unchanged(path: Path) -> str:
    """The commit holding ``path`` exactly as it is on disk, in REPO (the rig repo); SystemExit if
    it's outside REPO, not committed, or differs from HEAD. Compares `git hash-object` of the
    file with HEAD's blob, so skip-worktree / assume-unchanged can't hide an edit (QE 008 N2)."""
    path, repo = Path(path).resolve(), Path(REPO).resolve()
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        raise SystemExit(f"{path} is outside the rig repo {repo}: commit it there") from None

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    head = git("rev-parse", "--verify", "--quiet", f"HEAD:{rel}")
    if head.returncode != 0:
        raise SystemExit(f"{path} is not committed: rev 13 needs it committed before this stage")
    disk = git("hash-object", "--", rel).stdout.strip()
    if disk != head.stdout.strip():
        raise SystemExit(f"{path} differs from HEAD: commit it before this stage")
    return git("log", "-1", "--format=%H", "--", rel).stdout.strip()


def clip_dirs(out: Path) -> dict[str, Path]:
    """Clip id (path relative to RUN_DIR) -> its folder, as scan read it (the scan manifest)."""
    man = _read(out / "scan" / "manifest.json")
    run_dir, dirs = Path(man["run_dir"]), {}
    for ph in man["phases"].values():
        paths = [Path(d) for d in ph["dirs"]]
        ids = ph.get("clips") or [clip_id(run_dir, d) for d in paths]
        for i, d in zip(ids, paths):
            if dirs.get(i, d) != d:
                raise SystemExit(f"clip id {i} names two folders: {dirs[i]} and {d}")
            dirs[i] = d
    return dirs


def picked_frames(boxes: dict) -> dict[tuple[str, str, str], list[dict]]:
    """(camera, pass, clip) -> the picked frames of that clip (every bin, incl. report-only)."""
    out: dict = {}
    for cam, e in boxes["cameras"].items():
        for picks in e["bins"].values():
            for p in picks or []:
                out.setdefault((cam, e["w1_pass"], p["clip"]), []).append(p)
    return out


def hits_for(sample_boxes: dict, dets: list[dict], min_score: float, iou_min: float = 0.5
             ) -> dict[str, dict]:
    """Per sample: hit = some detection with score >= min_score has IoU >= iou_min with its box."""
    out = {}
    for name, s in sample_boxes.items():
        best = max((board.iou(s["box"], d["box"]) for d in dets if d["score"] >= min_score),
                   default=0.0)
        out[name] = {"hit": best >= iou_min, "best_iou": round(best, 3)}
    return out


def cached_snapshot(model_id: str, revision: str) -> str | None:
    """The cached snapshot folder of ``revision`` (offline; None if it isn't cached)."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    from huggingface_hub import try_to_load_from_cache
    path = try_to_load_from_cache(model_id, "config.json", revision=revision)
    if not isinstance(path, str):
        return None
    parts = Path(path).parts
    return parts[parts.index("snapshots") + 1] if "snapshots" in parts else None


def load_detector(device: str | None):
    """OWLv2 at the rev 17 pin, loaded with ``revision=`` (QE 008 R1). edge_ml/urchin/owl.py's
    OwlDetector has no revision argument (E6's merged code, not changed here), so its fields are
    filled the same way, with the pinned revision; its detect() (tiling + NMS) is used as is."""
    pin = card_values.DETECTOR
    snap = cached_snapshot(pin["model_id"], pin["revision"])
    if snap != pin["revision"]:
        raise SystemExit(f"{pin['model_id']} @ {pin['revision']} is not in the HF cache "
                         f"(found {snap}); rev 17 refuses any other revision")
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor

    from edge_ml.urchin.owl import QUERY, OwlDetector
    det = OwlDetector.__new__(OwlDetector)
    det.torch = torch
    det.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    det.processor = Owlv2Processor.from_pretrained(pin["model_id"], revision=pin["revision"])
    det.model = Owlv2ForObjectDetection.from_pretrained(
        pin["model_id"], revision=pin["revision"]).to(det.device).eval()
    det.query = QUERY
    return det, snap


def frame_hits(sample_boxes: dict, dets: list[dict]) -> dict[str, dict]:
    """Hits at each rev 17 threshold, from one detection list kept down to DETECT_FLOOR. Greedy
    NMS keeps a box only on higher-scored boxes, so the boxes >= T after NMS at the floor are the
    boxes NMS at T would keep: one detection pass serves every threshold (owl.py's cap of 300
    boxes per still drops only the lowest scores, so only the 0.05 chain could lose any)."""
    return {card_values.tkey(t): hits_for(sample_boxes, dets, t) for t in card_values.THRESHOLDS}


def cmd_detect(a) -> int:
    out, bpath = Path(a.out), Path(a.boxes)
    card, _ = load_card(a.card)
    commit = committed_unchanged(bpath)
    boxes = _read(bpath)
    _same_card(card, boxes.get("card_values"), "the box file")
    dpath = out / "detect" / "detections.json"
    if dpath.exists() and _read(dpath).get("boxes_sha256") != sha256_file(bpath):
        raise SystemExit(f"{dpath} holds detections from another box file: rev 13 commits one box "
                         "file before the detector runs; move that run aside, don't overwrite it")
    dirs = clip_dirs(out)
    det, rev = load_detector(a.device)
    pin = card_values.DETECTOR
    res = {"stage": "detect", "boxes": str(bpath), "boxes_sha256": sha256_file(bpath),
           "boxes_commit": commit, "card_values_sha256": card.sha256,
           "model_id": pin["model_id"], "model_revision": rev, "query": det.query,
           "detect_floor": card_values.DETECT_FLOOR, "threshold": pin["threshold"],
           "thresholds": list(card_values.THRESHOLDS), "iou_min": 0.5, "code": code_state(),
           "frames": {}}
    for (cam, _ph, clip), picks in picked_frames(boxes).items():
        cdir = dirs[clip]
        fr = {f["index"]: f for f in frames.camera_frames(cdir, cam)}
        want = [fr[p["index"]] for p in picks]
        for f, img in frames.iter_images(cdir, cam, want):
            p = next(p for p in picks if p["index"] == f["index"])
            b, s = det.detect(img, min_score=card_values.DETECT_FLOOR)
            dets = [{"box": [round(float(v), 1) for v in bb], "score": round(float(ss), 4)}
                    for bb, ss in zip(b, s)]
            res["frames"][f"{cam}/{clip}/{f['index']}"] = {
                "dets": dets, "hits": frame_hits(p["samples"], dets)}
    _write(dpath, res)
    print(f"detect: {len(res['frames'])} frames -> {dpath}")
    return 0


# -- blind fallback crops ------------------------------------------------------------------------

def cmd_crops(a) -> int:
    """Rev 13 fallback: crops of every sample in every picked frame, shuffled, neutral names; the
    key (crop -> camera / frame / sample) goes to a separate file the viewer never sees."""
    out, boxes = Path(a.out), _read(Path(a.boxes))
    dirs = clip_dirs(out)
    items = []
    for (cam, _ph, clip), picks in picked_frames(boxes).items():
        cdir = dirs[clip]
        fr = {f["index"]: f for f in frames.camera_frames(cdir, cam)}
        for f, img in frames.iter_images(cdir, cam, [fr[p["index"]] for p in picks]):
            p = next(p for p in picks if p["index"] == f["index"])
            for name, s in p["samples"].items():
                x1, y1, x2, y2 = s["box"]
                px, py = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
                h, w = img.shape[:2]
                crop = img[max(0, int(y1 - py)):min(h, int(y2 + py)),
                           max(0, int(x1 - px)):min(w, int(x2 + px))]
                items.append(({"camera": cam, "clip": clip, "index": f["index"], "sample": name},
                              crop))
    rng = random.Random(a.seed)
    rng.shuffle(items)
    cdir_out = out / "crops" / "viewer"
    cdir_out.mkdir(parents=True, exist_ok=True)
    key = {}
    for k, (meta, crop) in enumerate(items):
        name = f"crop_{k:04d}.png"
        cv2.imwrite(str(cdir_out / name), crop)
        key[name] = meta
    _write(out / "crops" / "KEY_do_not_show_viewer.json", {"seed": a.seed, "key": key})
    print(f"crops: {len(items)} under {cdir_out}; key kept apart")
    return 0


# -- score ---------------------------------------------------------------------------------------

def _attach(boxes: dict, detections: dict | None, blind: dict | None,
            threshold: float = card_values.DETECTOR["threshold"]
            ) -> dict[str, dict[float, list | None]]:
    """Per camera, bin centre -> its picked frames (None = fewer than 5 scorable) as wscore frame
    records, each sample's detector ``hit`` at ``threshold`` and blind ``human`` answer filled in.
    A blind file must answer every crop in its key (QE 008 N5: a missing answer is not a "no")."""
    key = (blind or {}).get("key") or {}
    answers = (blind or {}).get("answers") or {}
    missing = sorted(c for c in key if c not in answers)
    if blind and missing:
        raise SystemExit(f"the blind answers miss {len(missing)} crop(s), e.g. {missing[:3]}: "
                         "every crop needs a yes or no")
    human = {(m["camera"], m["clip"], m["index"], m["sample"]): bool(answers[c])
             for c, m in key.items()}
    tk = card_values.tkey(threshold)
    out: dict = {}
    for cam, e in boxes["cameras"].items():
        out[cam] = {}
        for c, picks in e["bins"].items():
            if picks is None:
                out[cam][float(c)] = None
                continue
            recs = []
            for p in picks:
                d = ((detections or {}).get("frames") or {}).get(f"{cam}/{p['clip']}/{p['index']}")
                samples = {name: {"px": s["px"],
                                  "hit": bool(d and d["hits"][tk].get(name, {}).get("hit")),
                                  "human": human.get((cam, p["clip"], p["index"], name))}
                           for name, s in (p.get("samples") or {}).items()}
                recs.append({**p, "samples": samples})
            out[cam][float(c)] = recs
    return out


def w1_score(out: Path, cam: str, entry: dict, picks: dict, size: str, key: str) -> dict:
    """W1 on the box file's picks; every frame of the W1 pass is passed for F16's tag-limited
    test (the farthest bin with tag-decoded frames)."""
    fr = load_scan(out, entry["w1_pass"], cam)
    return wscore.detectable_range(fr, size=size, key=key, first_bin=entry["first_bin"],
                                   select=lambda _frames, c: picks.get(c))


def w4_pairs(out: Path, cam: str) -> list[dict]:
    """W4: per hold (3, 2, 1 m), the hold frame nearest the hold range in pass 1 and in pass 2."""
    rows = []
    for ph in ("pass1", "pass2"):
        fr = load_scan(out, ph, cam)
        sp = wscore.speeds(fr)
        for d in W4_HOLDS_M:
            cand = [(f, s) for f, s in zip(fr, sp) if s is not None and s < wscore.HOLD_SPEED_MS
                    and f.get("range_m") and abs(f["range_m"] - d) < wscore.BIN_HALF_M
                    and f.get("board_whole")]
            if not cand:
                rows.append({"pass": ph, "hold_m": d, "frame": None})
                continue
            f, s = min(cand, key=lambda fs: abs(fs[0]["range_m"] - d))
            de = list((f.get("de00") or {}).values())
            rows.append({"pass": ph, "hold_m": d, "frame": f"{f['clip']}/{f['index']}",
                         "range_m": f["range_m"], "speed_ms": round(s, 3),
                         "board_box": f.get("board_box"),
                         "mtf50_lpmm": f.get("mtf50_lpmm"),
                         "de00_median": round(float(np.median(de)), 2) if de else None,
                         "de00_worst": round(float(max(de)), 2) if de else None})
    return rows


def _frame_image(out: Path, cam: str, ref: str):
    clip, idx = ref.rsplit("/", 1)
    cdir = clip_dirs(out)[clip]
    fr = [f for f in frames.camera_frames(cdir, cam) if f["index"] == int(idx)]
    return next(frames.iter_images(cdir, cam, fr))[1] if fr else None


def w4_images(out: Path, cam: str, pairs: list[dict]) -> list[str]:
    """W4: per hold, pass 1 | pass 2 board crops side by side, the same height, as PNG (lossless,
    so the page adds no chroma loss of its own). Nick judges these by eye."""
    by = {}
    for p in pairs:
        by.setdefault(p["hold_m"], {})[p["pass"]] = p
    written = []
    for d, pp in sorted(by.items()):
        tiles = []
        for ph in ("pass1", "pass2"):
            p = pp.get(ph) or {}
            img = _frame_image(out, cam, p["frame"]) if p.get("frame") else None
            box = p.get("board_box")
            if img is None or not box:
                continue
            x1, y1, x2, y2 = (int(round(v)) for v in box)
            crop = img[y1:y2, x1:x2]
            if crop.size:
                tiles.append(crop)
        if len(tiles) != 2:
            continue
        hh = max(t.shape[0] for t in tiles)
        tiles = [cv2.resize(t, (round(t.shape[1] * hh / t.shape[0]), hh),
                            interpolation=cv2.INTER_NEAREST) for t in tiles]
        path = out / "score" / "w4" / f"{cam}_{d:.0f}m_pass1_vs_pass2.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), np.hstack(tiles))
        written.append(str(path.relative_to(out)))
    return written


W5_SPEED_BINS = (0.0, 0.1, 0.3, 0.6, float("inf"))


def _median_of(rows: list[dict], key: str) -> float | None:
    v = [r[key] for r in rows if r.get(key) is not None]
    return round(float(np.median(v)), 2) if v else None


def w5_summary(rows: list[dict]) -> list[dict]:
    """W5 table: per crossing and speed band, n and the median edge widths (px)."""
    out = []
    for cr in ("w5_slow", "w5_fast"):
        for lo, hi in zip(W5_SPEED_BINS[:-1], W5_SPEED_BINS[1:]):
            rs = [r for r in rows if r["crossing"] == cr and r["speed_ms"] is not None
                  and lo <= r["speed_ms"] < hi]
            if not rs:
                continue
            out.append({"crossing": cr, "speed_ms": f"{lo:g}-{hi:g}", "n": len(rs),
                        "slanted_edge_w_px": _median_of(rs, "slanted_edge_w_px"),
                        "tag_edge_w_px": _median_of(rs, "tag_edge_w_px")})
    return out


def w3_table(boxes: dict) -> dict:
    """W3: CIEDE2000 per patch, median and worst over the W1 frames of the 1.0 and 2.0 m bins."""
    out = {}
    for cam, e in boxes["cameras"].items():
        for c in ("1.0", "2.0"):
            picks = e["bins"].get(c) or []
            per_patch: dict[str, list] = {}
            for p in picks:
                for k, v in (p.get("de00") or {}).items():
                    per_patch.setdefault(k, []).append(v)
            out[f"{cam}@{c}"] = {k: {"median": round(float(np.median(v)), 2),
                                     "worst": round(float(max(v)), 2), "n": len(v)}
                                 for k, v in sorted(per_patch.items())}
    return out


def w5_table(out: Path, cam: str) -> list[dict]:
    """W5: per crossing frame with an edge width, the board speed and both widths (px)."""
    rows = []
    for ph in ("w5_slow", "w5_fast"):
        fr = load_scan(out, ph, cam)
        for f, s in zip(fr, wscore.speeds(fr)):
            if f.get("edge_w_px") is None and f.get("tag_edge_w_px") is None:
                continue
            rows.append({"crossing": ph, "frame": f"{f['clip']}/{f['index']}", "t": f["t"],
                         "range_m": f.get("range_m"),
                         "speed_ms": round(s, 3) if s is not None else None,
                         "slanted_edge_w_px": f.get("edge_w_px"),
                         "tag_edge_w_px": f.get("tag_edge_w_px")})
    return rows


def cmd_score(a) -> int:
    out, bpath = Path(a.out), Path(a.boxes)
    card, card_commit = load_card(a.card)
    boxes = _read(bpath)
    _same_card(card, boxes.get("card_values"), "the box file")
    dpath = out / "detect" / "detections.json"
    if not dpath.exists():
        raise SystemExit(f"no {dpath}: run `detect` first (W1 needs the detector's hits)")
    detections = _read(dpath)
    if detections["boxes_sha256"] != sha256_file(bpath):
        raise SystemExit("detections.json was made from a different box file")
    pin = card_values.DETECTOR
    got = {"model_id": detections.get("model_id"), "revision": detections.get("model_revision"),
           "threshold": detections.get("threshold")}
    if got != pin or detections.get("detect_floor") != card_values.DETECT_FLOOR:
        raise SystemExit(f"detections.json used {got} (floor {detections.get('detect_floor')}), "
                         f"not the rev 17 pin {pin} (floor {card_values.DETECT_FLOOR})")
    blind = _read(Path(a.blind)) if a.blind else None
    picks = _attach(boxes, detections, blind)
    res = {"stage": "score", "code": code_state(), "boxes_sha256": sha256_file(bpath),
           "boxes_commit": (detections or {}).get("boxes_commit"),
           "detections_sha256": sha256_file(dpath) if detections else None,
           "card_values": {**card.record(), "commit": card_commit},
           "layout_measured": boxes.get("layout_measured"), "lenses": boxes.get("lenses"),
           "lost": boxes.get("lost"),
           "detector": {k: detections.get(k) for k in ("model_id", "model_revision", "query",
                                                       "detect_floor", "threshold")},
           "f10": boxes.get("f10"), "w1": {}, "w1_report": {}, "w2": {}, "w4": {}, "w5": {}}
    det_w1 = {}
    for cam, e in boxes["cameras"].items():
        if not e["w1_pass"]:
            res["w1"][cam] = {"not_measured": e["why"]}
            continue
        if (boxes.get("f10") or {}).get(e["w1_pass"], {}).get("pass_unmeasured"):
            res["w1"][cam] = {"not_measured": f"F10: > 25 % of {e['w1_pass']} flagged"}
            continue
        det_w1[cam] = {s: w1_score(out, cam, e, picks[cam], s, "hit") for s in ("80", "50")}
    fallback = wscore.detector_fallback({c: r["80"] for c, r in det_w1.items()})
    key = "human" if fallback else "hit"
    if fallback and not blind:
        raise SystemExit("rev 13 fallback: the detector hit < 4 of 5 in the first bin on every "
                         "camera. Run `crops`, have the PjM's blind viewer answer, then "
                         "`score --blind answers.json` (key + answers).")
    for cam, e in boxes["cameras"].items():
        if cam not in det_w1:
            continue
        res["w1"][cam] = {"pass": e["w1_pass"], "why": e["why"], "first_bin": e["first_bin"],
                          **{f"{s}mm": w1_score(out, cam, e, picks[cam], s, key)
                             for s in ("80", "50")}}
    res["w1_fallback"] = fallback
    for t in card_values.THRESHOLDS:  # rev 17 report-only chains (decisions use the pin)
        if t == pin["threshold"]:
            continue
        pt = _attach(boxes, detections, None, threshold=t)
        res["w1_report"][card_values.tkey(t)] = {
            cam: {f"{s}mm": w1_score(out, cam, boxes["cameras"][cam], pt[cam], s, "hit")
                  for s in ("80", "50")} for cam in det_w1}
    if "openmv_n6" in res["w1"] and "openmv_ae3" in res["w1"] \
            and "80mm" in res["w1"]["openmv_n6"] and "80mm" in res["w1"]["openmv_ae3"]:
        res["n6_vs_ae3"] = wscore.n6_vs_ae3(res["w1"]["openmv_n6"]["80mm"],
                                            res["w1"]["openmv_ae3"]["80mm"])
    for cam in boxes["cameras"]:
        p1 = wscore.w2_secchi(load_scan(out, "pass1", cam))
        ck = wscore.w2_secchi(load_scan(out, "check", cam))
        res["w2"][cam] = {"pass1_m": p1, "check_m": ck, "f12": wscore.f12_water_changed(p1, ck)}
    res["w3"] = w3_table(boxes)
    for cam in ("openmv_n6", "openmv_ae3"):
        if cam not in boxes["cameras"]:
            continue
        c1, c2 = phase_chroma(out, "pass1", cam), phase_chroma(out, "pass2", cam)
        if c1 and c2 and c1 != c2 and "mixed" not in (c1, c2):
            pairs = w4_pairs(out, cam)
            res["w4"][cam] = {"chroma": [c1, c2], "pairs": pairs,
                              "images": w4_images(out, cam, pairs)}
        else:
            res["w4"][cam] = {"not_run": f"pass 1 {c1}, pass 2 {c2} (F15 / F18, or a pass "
                                         "with mixed chroma)"}
    for cam in boxes["cameras"]:
        rows = w5_table(out, cam)
        res["w5"][cam] = {"rows": rows, "by_speed": w5_summary(rows)}
    _write(out / "score" / "results.json", res)
    (out / "score" / "RESULTS.md").write_text(results_md(res))
    print(f"score: {out / 'score' / 'RESULTS.md'}")
    return 0


def _fmt_range(x: dict) -> str:
    return ("≥ " if x["tag_limited"] else "") + (f"{x['range_m']} m" if x["range_m"] else "none")


def results_md(res: dict) -> str:
    cv_ = res.get("card_values") or {}
    L = ["# Pool test 2 W1-W5 results", ""]
    if res.get("layout_measured") is not True:
        L += ["**TEMPLATE LAYOUT (not measured): the W1 numbers below are meaningless; a dry run "
              "only (QE 008 N3).**", ""]
    L += [f"- code {res['code']['sha'][:12]}{' (dirty)' if res['code']['dirty'] else ''}; "
          f"box file sha256 {res['boxes_sha256'][:12]} (commit {str(res['boxes_commit'])[:12]}); "
          f"detections sha256 {str(res['detections_sha256'])[:12]}",
          f"- card values {cv_.get('path')} sha256 {str(cv_.get('sha256'))[:12]} (commit "
          f"{str(cv_.get('commit'))[:12]}); source: {cv_.get('source')}",
          f"- part-B chroma {cv_.get('partb')}; first bins {cv_.get('first_bin')}; guard cameras "
          f"{cv_.get('guard_cameras')}",
          f"- detector: {res['detector']} (W1 decides at the threshold; 0.05 / 0.5 report-only)",
         "- W1 by: " + ("BLIND FALLBACK (detector < 4/5 in the first bin on every camera)"
                        if res["w1_fallback"] else "detector"),
         "", "## W1: detectable range (80 mm; 50 mm reported)", "",
         "| camera | pass | first bin | 80 mm range | end (reason) | 50 mm range | end (reason) |",
         "|---|---|---|---|---|---|---|"]
    for cam, r in res["w1"].items():
        if "not_measured" in r:
            L.append(f"| {cam} | - | - | not measured: {r['not_measured']} | | | |")
            continue
        a, b = r["80mm"], r["50mm"]
        L.append(f"| {cam} | {r['pass']} | {r['first_bin']} m | {_fmt_range(a)} | {a['end_bin']} "
                 f"({a['end_reason']}) | {_fmt_range(b)} | {b['end_bin']} ({b['end_reason']}) |")
    if "n6_vs_ae3" in res:
        L += ["", f"**N6 vs AE3 (longer 80 mm range; tie -> MTF50 at 1.0 m): {res['n6_vs_ae3']}**"]
    L += ["", "Report-only chains at the other rev 17 thresholds (80 mm / 50 mm):", ""]
    for t, per in (res.get("w1_report") or {}).items():
        for cam, r in per.items():
            L.append(f"- {t}: {cam} {_fmt_range(r['80mm'])} / {_fmt_range(r['50mm'])}")
    L += ["", "## Lenses (rev 16; the guard keeps raw corners)", ""]
    for cam, modes in (res.get("lenses") or {}).items():
        for mode, ln in modes.items():
            L.append(f"- {cam} {mode}: {'mapped' if ln.get('mapped') else 'GUARD'}, fx "
                     f"{round(ln['K'][0][0], 1)}, fy {round(ln['K'][1][1], 1)}: {ln.get('why')}")
    lost = {ph: v for ph, v in (res.get("lost") or {}).items() if v}
    if lost:
        L += ["", "## Lost clips (not scanned)", ""]
        for ph, v in lost.items():
            L += [f"- {ph}: {k['clip']} ({k['why']})" for k in v]
    L += ["", "## F10 (cross-camera range check, > 10 % at one instant; rev 16)", "",
          "Decisions use the undistorted corners; raw corners are reported beside them.", "",
          "| pass | corners | instants | flagged | share | W1 not measured | median ratios |",
          "|---|---|---|---|---|---|---|"]
    for ph, f in (res.get("f10") or {}).items():
        for label, g in (("undistorted", f), ("raw", f.get("raw_corners") or {})):
            ratios = ", ".join(f"{k} {v}" for k, v in (g.get("pair_ratio_median") or {}).items())
            L.append(f"| {ph} | {label} | {g.get('instants')} | {g.get('flagged')} | "
                     f"{g.get('share')} | {g.get('pass_unmeasured')} | {ratios} |")
    L += ["", "## W2 camera Secchi + F12", "", "| camera | pass 1 (m) | check (m) | F12 |",
          "|---|---|---|---|"]
    for cam, r in res["w2"].items():
        L.append(f"| {cam} | {r['pass1_m']} | {r['check_m']} | {r['f12']} |")
    L += ["", "## W3 CIEDE2000 per patch (uncorrected), median / worst over the W1 frames", ""]
    for k, patches in res["w3"].items():
        if patches:
            L.append(f"- {k}: " + ", ".join(f"{p} {v['median']}/{v['worst']} (n {v['n']})"
                                            for p, v in patches.items()))
    L += ["", "## W4 full vs half colour at the holds (report-only; Nick judges by eye)", ""]
    for cam, r in res["w4"].items():
        if "not_run" in r:
            L.append(f"- {cam}: not run ({r['not_run']})")
            continue
        L.append(f"- {cam} (chroma {r['chroma'][0]} vs {r['chroma'][1]}):")
        for p in r["pairs"]:
            L.append(f"  - {p['pass']} @ {p['hold_m']} m: {p.get('frame')} MTF50 "
                     f"{p.get('mtf50_lpmm')} lp/mm, ΔE00 median {p.get('de00_median')} / worst "
                     f"{p.get('de00_worst')}")
    for cam, r in res["w4"].items():
        for img in r.get("images") or []:
            L.append(f"  - image: {img}")
    L += ["", "## W5 edge blur (10-90 %, px) vs board speed (every row in results.json)", "",
          "| camera | crossing | speed (m/s) | n | slanted edge | tag edges |",
          "|---|---|---|---|---|---|"]
    for cam, r in res["w5"].items():
        for b in r["by_speed"]:
            L.append(f"| {cam} | {b['crossing']} | {b['speed_ms']} | {b['n']} | "
                     f"{b['slanted_edge_w_px']} | {b['tag_edge_w_px']} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("run_dir")
    s.add_argument("out")
    s.add_argument("--card", required=True, help="the committed Wed card-values file (R4)")
    s.add_argument("--layout", required=True)
    s.add_argument("--phase", action="append", required=True)
    s.add_argument("--cameras", default=",".join(frames.CAMERAS))
    s.add_argument("--allow-template", action="store_true",
                   help="dry runs only: an unmeasured layout (W1 numbers are then meaningless)")
    s.add_argument("--no-colour", action="store_true")
    s = sub.add_parser("select")
    s.add_argument("out")
    s.add_argument("--card", required=True)
    s.add_argument("--boxes", required=True)
    s.add_argument("--cameras", default=",".join(frames.CAMERAS))
    s = sub.add_parser("detect")
    s.add_argument("out")
    s.add_argument("--card", required=True)
    s.add_argument("--boxes", required=True)
    s.add_argument("--device", default=None)
    s = sub.add_parser("crops")
    s.add_argument("out")
    s.add_argument("--boxes", required=True)
    s.add_argument("--seed", type=int, default=20261021)
    s = sub.add_parser("score")
    s.add_argument("out")
    s.add_argument("--card", required=True)
    s.add_argument("--boxes", required=True)
    s.add_argument("--blind", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    return {"scan": cmd_scan, "select": cmd_select, "detect": cmd_detect, "crops": cmd_crops,
            "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
