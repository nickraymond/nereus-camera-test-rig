"""E6 night pilot driver: are the Reef Check videos good enough to work? (SPEC_e6_night_pilot rev 3)

Runs the existing pipeline unchanged (extract_stills.py, owl.py, split.py, review.py) on the few
videos Nick marked "urchins: yes", one video at a time, with the spec's stop rules, then builds the
per-video quality table, scores the 100-still pilot sample (Wilson 95 %) and draws Nick's audit
sheet. Study-only code; never imported from src/.

    python -m edge_ml.urchin.pilot select  <run_dir>   # marks → pilot_set.json
    python -m edge_ml.urchin.pilot extract <run_dir>   # E0 + E1–E5 per video, stop rules
    python -m edge_ml.urchin.pilot owl     <run_dir>   # model A pre-labels (>= 0.05)
    python -m edge_ml.urchin.pilot samples <run_dir>   # pilot (100) + scale (20/video)
    python -m edge_ml.urchin.pilot score   <run_dir>   # quality.csv + verdict.json from the calls
    python -m edge_ml.urchin.pilot audit   <run_dir>   # 24-still audit sheet with boxes + calls

Claude fills `pilot_sample_calls.csv` and `scale_sample_calls.csv` by viewing the still sheets
between `samples` and `score`.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

log = logging.getLogger("urchin.pilot")

# Spec rev 3 + the proposal's stop-early lines.
N_MAX = 6
PER_FOLDER = 2
MIN_FREE_GIB = 8.0
MIN_STILLS_PER_MIN = 5.0
POLL_S = 5.0
SSD_MOUNT = Path("/Volumes/Nick_R_SSD")
# Spec must-do 4–6 and pass/fail.
SAMPLE_N = 100
SCALE_K = 20
AUDIT_N = 24
CAND_SCORES = (0.05, 0.3)
USABLE_BAR = 0.60
LOWER_BAR = 0.50
MIN_VIDEO_SAMPLE = 10
MIN_VIDEOS_VERDICT = 3
MIN_VIDEOS_FOLDER = 2
Z95 = 1.959963984540054
THERM_PAUSE_S = 60.0
THERM_MAX_WAIT_S = 1800.0


# ---------------------------------------------------------------- selection (spec rev 3)


def select(cands: list[dict], marks: dict[str, str], replace: set[int] | None = None) -> list[dict]:
    """Pilot set from Nick's marks. cands are in MANIFEST order with keys num, folder, video, path.

    Up to PER_FOLDER "yes" videos per folder (folders in order of first appearance), then fill to
    N_MAX from the remaining "yes" videos in MANIFEST order. Unmarked or "unsure" is not-yes.
    `replace` lists candidate numbers that failed E0/decode (P4): they are skipped, and the same
    rule then picks the next "yes" video of their folder, else the next in MANIFEST order."""
    skip = replace or set()
    yes = [
        c
        for c in cands
        if marks.get(str(c["num"]), "").strip().lower() == "yes" and c["num"] not in skip
    ]
    folders = list(dict.fromkeys(c["folder"] for c in cands))
    picked: list[dict] = []
    for folder in folders:
        picked += [c for c in yes if c["folder"] == folder][:PER_FOLDER]
    for c in yes:
        if len(picked) >= N_MAX:
            break
        if c not in picked:
            picked.append(c)
    return picked[:N_MAX]


def screen_outcome(marks: dict[str, str], n_cands: int) -> str | None:
    """Stop reason before any work, or None to go on."""
    if not marks:
        return "no screen answer"
    if not any(v.strip().lower() == "yes" for v in marks.values()):
        return "no urchin footage"
    if len(marks) > n_cands:
        raise ValueError(f"{len(marks)} marks for {n_cands} candidates")
    return None


# ---------------------------------------------------------------- environment checks


def free_gib(path: Path) -> float:
    return shutil.disk_usage(path).free / 2**30


def thermal_limited(pmset_text: str) -> bool:
    """True if `pmset -g therm` reports a thermal or performance warning level > 0 (what arm64
    Macs print) or a CPU speed limit < 100 (Intel Macs)."""
    for line in pmset_text.splitlines():
        if m := re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", line):
            if int(m.group(1)) < 100:
                return True
        if m := re.search(
            r"(?:thermal|performance) warning level (?:set to|=)\s*(\d+)", line, re.I
        ):
            if int(m.group(1)) > 0:
                return True
    return False


def pmset_therm() -> str:
    out = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, check=False)
    return (out.stdout + out.stderr).strip()


def env_problem(run_dir: Path, mount: Path, min_free: float = MIN_FREE_GIB) -> str | None:
    """A stop reason if the SSD is unmounted or the run's disk is below min_free GiB."""
    if not os.path.ismount(mount):
        return f"SSD unmounted ({mount})"
    gib = free_gib(run_dir)
    if gib < min_free:
        return f"free disk {gib:.1f} GiB < {min_free:g} GiB"
    return None


def wait_cool(notes: list[dict], what: str, therm: Callable[[], str] = pmset_therm) -> None:
    """P3: pause while the Mac reports thermal limiting; every check is noted."""
    waited = 0.0
    while True:
        text = therm()
        limited = thermal_limited(text)
        notes.append({"utc": _now(), "before": what, "limited": limited, "pmset": text})
        if not limited:
            return
        if waited >= THERM_MAX_WAIT_S:
            log.warning("still thermal-limited after %.0f s before %s; going on", waited, what)
            return
        log.warning("thermal limit before %s; pausing %.0f s", what, THERM_PAUSE_S)
        time.sleep(THERM_PAUSE_S)
        waited += THERM_PAUSE_S


def run_watched(cmd: list[str], check: Callable[[], str | None], poll_s: float = POLL_S) -> str:
    """Run cmd; every poll_s call check(). Returns "ok", "rc=<n>" or the stop reason (process
    terminated). Output goes to the caller's log file via the inherited stdout/stderr."""
    proc = subprocess.Popen(cmd)
    while True:
        try:
            rc = proc.wait(timeout=poll_s)
            return "ok" if rc == 0 else f"rc={rc}"
        except subprocess.TimeoutExpired:
            if reason := check():
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return reason


# ---------------------------------------------------------------- per-video facts


def video_quality(rows: list[dict], duration_s: float) -> dict:
    """stills.csv rows of one video → yield and drop shares (PREREG Q1 measure).

    Shares are of the E2 window-best stills (kept + blur + duplicate), the stills E3/E4 judge."""
    kept = [r for r in rows if r["status"] == "kept"]
    blur = sum(r["status"] == "blur" for r in rows)
    dup = sum(r["status"] == "duplicate" for r in rows)
    best = len(kept) + blur + dup
    return {
        "candidates": len(rows),
        "window_best": best,
        "kept": len(kept),
        "stills_per_min": round(len(kept) / (duration_s / 60), 2) if duration_s > 0 else 0.0,
        "blur_share": round(blur / best, 3) if best else 0.0,
        "dup_share": round(dup / best, 3) if best else 0.0,
        "median_sharpness": round(float(np.median([float(r["sharpness"]) for r in kept])), 1)
        if kept
        else 0.0,
    }


def video_ok(q: dict, failed: bool) -> bool:
    return not failed and q["stills_per_min"] >= MIN_STILLS_PER_MIN


def candidate_shares(dets: dict[str, list[dict]], stills: list[str]) -> dict[str, float]:
    """Share of stills with >= 1 model-A candidate at each CAND_SCORES threshold."""
    out = {}
    for thr in CAND_SCORES:
        hit = sum(any(d["score"] >= thr for d in dets.get(s, [])) for s in stills)
        out[f"cand_share_{thr:g}"] = round(hit / len(stills), 3) if stills else 0.0
    return out


# ---------------------------------------------------------------- scoring


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def share(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {
        "k": k,
        "n": n,
        "p": round(k / n, 3) if n else None,
        "lo": round(lo, 3),
        "hi": round(hi, 3),
    }


def per_video_verdict(k: int, n: int) -> str:
    if n < MIN_VIDEO_SAMPLE:
        return "too few to say"
    return "good enough" if k / n >= USABLE_BAR else "not good enough"


def verdict(calls: list[dict], folder_of: dict[str, str], n_videos: int) -> dict:
    """Pilot verdict on the usable-share half of PREREG Q1 (never a Q1 pass: no labels yet).

    calls: rows with video, usable (yes/no), urchin (yes/no/unsure). folder_of: video → folder."""
    n = len(calls)
    k = sum(c["usable"] == "yes" for c in calls)
    overall = share(k, n)
    urchin = share(sum(c["urchin"] == "yes" for c in calls), n)
    urchin["unsure"] = sum(c["urchin"] == "unsure" for c in calls)
    videos: dict[str, dict] = {}
    for v in sorted({c["video"] for c in calls} | set(folder_of)):
        vc = [c for c in calls if c["video"] == v]
        kv = sum(c["usable"] == "yes" for c in vc)
        videos[v] = {**share(kv, len(vc)), "verdict": per_video_verdict(kv, len(vc))}
    folders: dict[str, dict] = {}
    for f in dict.fromkeys(folder_of.values()):
        vids = [v for v, fo in folder_of.items() if fo == f]
        fc = [c for c in calls if c["video"] in vids]
        kf = sum(c["usable"] == "yes" for c in fc)
        if len(vids) < MIN_VIDEOS_FOLDER or n_videos < MIN_VIDEOS_VERDICT:
            rec = f"no recommendation ({len(vids)} video(s) from this folder)"
        elif len(fc) < MIN_VIDEO_SAMPLE:
            rec = "no recommendation (too few sampled stills)"
        else:
            rec = "process in full" if kf / len(fc) >= USABLE_BAR else "not good enough"
        folders[f] = {**share(kf, len(fc)), "videos": len(vids), "recommendation": rec}
    if n_videos < MIN_VIDEOS_VERDICT:
        outcome = f"no verdict (n = {n_videos} videos < {MIN_VIDEOS_VERDICT})"
    elif overall["p"] is not None and overall["p"] >= USABLE_BAR and overall["lo"] >= LOWER_BAR:
        outcome = "good enough to work"
    else:
        outcome = "not good enough"
    return {
        "outcome": outcome,
        "usable": overall,
        "urchin_present": urchin,
        "videos": videos,
        "folders": folders,
        "n_videos": n_videos,
    }


def verdict_line(v: dict) -> str:
    u = v["usable"]
    pct = f"{u['p']:.0%} (Wilson 95 % {u['lo']:.0%}–{u['hi']:.0%}, n = {u['n']} stills)"
    return (
        f"Pilot, Nick-screened urchin footage only, {v['n_videos']} videos: usable {pct} → "
        f"{v['outcome']} on the usable-share half of PREREG Q1 (bar ≥ 60 %, lower bound ≥ 50 %). "
        "Not a Q1 pass: the label minimums are not tested yet."
    )


# ---------------------------------------------------------------- run-folder helpers


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_json(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def write_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def read_csv(p: Path) -> list[dict]:
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(p: Path, rows: list[dict], fields: list[str]) -> None:
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def tool_cmd(python: str, module: str, *args: str) -> list[str]:
    return ["nice", "-n", "10", python, "-m", f"edge_ml.urchin.{module}", *args]


# ---------------------------------------------------------------- commands


def cmd_select(run: Path) -> int:
    cands = read_json(run / "candidates.json")
    marks = read_json(run / "marks.json", {}).get("marks", {})
    if reason := screen_outcome(marks, len(cands)):
        write_json(run / "pilot_set.json", {"stop": reason, "videos": []})
        log.error("STOP: %s", reason)
        return 2
    picked = select(cands, marks)
    write_json(run / "pilot_set.json", {"stop": None, "selected_utc": _now(), "videos": picked})
    log.info("pilot set: %s", [c["num"] for c in picked])
    return 0


def cmd_extract(run: Path, python: str, mount: Path) -> int:
    cands = read_json(run / "candidates.json")
    marks = read_json(run / "marks.json")["marks"]
    state = read_json(run / "run.json", {"extract": {}, "therm": [], "events": []})
    failed_nums: set[int] = {int(n) for n, e in state["extract"].items() if e.get("failed")}
    order: list[dict] = []
    while True:
        todo = [c for c in select(cands, marks, failed_nums) if c not in order]
        if not todo:
            break
        c = todo[0]
        order.append(c)
        key = str(c["num"])
        if state["extract"].get(key, {}).get("done"):
            continue
        if reason := env_problem(run, mount):
            state["events"].append({"utc": _now(), "stop": reason, "before": c["video"]})
            write_json(run / "run.json", state)
            log.error("STOP before #%s: %s", key, reason)
            return 2
        wait_cool(state["therm"], f"extract #{key}")
        nn = state["extract"].get(key, {}).get("nn") or f"{len(state['extract']) + 1:02d}"
        in_dir, out_dir = run / "inputs" / nn, run / "extract" / nn
        in_dir.mkdir(parents=True, exist_ok=True)
        link = in_dir / Path(c["path"]).name
        if not link.is_symlink():
            link.symlink_to(c["path"])
        entry = {"num": c["num"], "video": c["video"], "nn": nn, "start_utc": _now()}
        t0 = time.monotonic()
        res = subprocess.run(
            tool_cmd(python, "extract_stills", "inventory", str(in_dir), str(out_dir))
        )
        inv = read_csv(out_dir / "inventory.csv") if (out_dir / "inventory.csv").exists() else []
        if res.returncode or not inv or inv[0].get("error"):
            entry |= {"failed": "E0 ffprobe", "done": True}
        else:
            status = run_watched(
                tool_cmd(python, "extract_stills", "extract", str(in_dir), str(out_dir)),
                lambda: env_problem(run, mount),
            )
            if status not in ("ok", "rc=1"):
                entry["stop"] = status
                state["extract"][key] = entry
                state["events"].append({"utc": _now(), "stop": status, "during": c["video"]})
                write_json(run / "run.json", state)
                log.error("STOP during #%s: %s", key, status)
                return 2
            ex_run = read_json(out_dir / "extract_run.json")
            if ex_run is None:  # a crash, not a bad video: stop so the step gets a repo fix
                state["events"].append({"utc": _now(), "stop": f"extract crashed ({status})"})
                write_json(run / "run.json", state)
                log.error("STOP: extract crashed on #%s (%s)", key, status)
                return 2
            rows = read_csv(out_dir / "stills.csv") if (out_dir / "stills.csv").exists() else []
            q = video_quality(rows, float(inv[0]["duration_s"] or 0))
            entry |= {"quality": q, "done": True}
            if ex_run.get("failed") or not rows:
                entry["failed"] = "decode"
        entry["wall_s"] = round(time.monotonic() - t0, 1)
        state["extract"][key] = entry
        if entry.get("failed"):
            failed_nums.add(c["num"])
            order.remove(c)  # P4: listed and skipped; the rule picks a replacement
        write_json(run / "run.json", state)
        log.info("#%s %s: %s", key, c["video"], entry.get("failed") or entry["quality"])
        first2 = sorted(state["extract"].values(), key=lambda e: e["start_utc"])[:2]
        if len(first2) == 2 and not any(
            video_ok(e.get("quality", {"stills_per_min": 0}), bool(e.get("failed"))) for e in first2
        ):
            reason = "first 2 videos fail to decode or keep < 5 stills/min"
            state["events"].append({"utc": _now(), "stop": reason})
            write_json(run / "run.json", state)
            log.error("STOP: %s", reason)
            return 2
    merge_stills(run, state)
    write_json(run / "run.json", state)
    ps = read_json(run / "pilot_set.json", {})
    ps |= {"extracted": [c["num"] for c in extracted_set(run)], "extracted_utc": _now()}
    write_json(run / "pilot_set.json", ps)
    return 0


def extracted_set(run: Path) -> list[dict]:
    """The videos actually extracted (done, not failed), in processing order, as candidates.

    This, not select()'s first pick, is the pilot set that gets scored: a P4 replacement chosen
    during `extract` is in it, a failed video is not."""
    cands = {c["num"]: c for c in read_json(run / "candidates.json")}
    entries = read_json(run / "run.json", {"extract": {}})["extract"].values()
    ok = [e for e in entries if e.get("done") and not e.get("failed")]
    return [cands[e["num"]] for e in sorted(ok, key=lambda e: e["start_utc"])]


def merge_stills(run: Path, state: dict) -> None:
    """extract/NN/ → one stills.csv + inventory.csv, and stills/<stem> links (one stills root)."""
    rows, inv = [], []
    (run / "stills").mkdir(exist_ok=True)
    for e in sorted(state["extract"].values(), key=lambda e: e["start_utc"]):
        if e.get("failed") or not e.get("done"):
            continue
        d = run / "extract" / e["nn"]
        rows += read_csv(d / "stills.csv")
        inv += read_csv(d / "inventory.csv")
        stem = Path(e["video"]).stem
        link = run / "stills" / stem
        if not link.is_symlink():
            link.symlink_to(Path("..") / "extract" / e["nn"] / "stills" / stem)
    if rows:
        write_csv(run / "stills.csv", rows, list(rows[0]))
        write_csv(run / "inventory.csv", inv, list(inv[0]))


def kept_names(run: Path) -> list[str]:
    """Kept stills as '<video stem>/<still>' (split.read_stills_csv naming)."""
    from edge_ml.urchin.split import read_stills_csv

    return [s.name for s in read_stills_csv(run / "stills.csv")]


def cmd_owl(run: Path, python: str) -> int:
    state = read_json(run / "run.json")
    names = kept_names(run)
    by_video: dict[str, list[str]] = {}
    for n in names:
        by_video.setdefault(n.split("/", 1)[0], []).append(n)
    (run / "owl").mkdir(exist_ok=True)
    merged: dict[str, list] = {}
    meta = {}
    for stem, part in by_video.items():
        out = run / "owl" / f"{stem}.json"
        if not out.exists():
            wait_cool(state["therm"], f"owl {stem}")
            lst = run / "owl" / f"{stem}.txt"
            lst.write_text("".join(f"{n}\n" for n in part))
            t0 = time.monotonic()
            rc = subprocess.run(
                tool_cmd(
                    python, "owl", str(lst), str(run / "stills"), str(out), "--min-score", "0.05"
                )
            ).returncode
            state.setdefault("owl", {})[stem] = {"rc": rc, "wall_s": round(time.monotonic() - t0)}
            write_json(run / "run.json", state)
            if rc:
                log.error("owl failed on %s (rc=%s)", stem, rc)
                return 1
        o = read_json(out)
        meta = {k: o[k] for k in ("model", "query", "min_score")}
        merged |= o["detections"]
    write_json(run / "owl_prelabels.json", {**meta, "detections": merged})
    return 0


def cmd_samples(run: Path) -> int:
    from edge_ml.urchin.review import still_sheets
    from edge_ml.urchin.split import evenly_spaced, proportional_sample, read_stills_csv

    stills = read_stills_csv(run / "stills.csv")
    sample = proportional_sample(stills, SAMPLE_N, seed=0)
    (run / "pilot_sample.txt").write_text("".join(f"{s.name}\n" for s in sample))
    scale = []
    for v in sorted({s.video for s in stills}):
        scale += evenly_spaced([s for s in stills if s.video == v], SCALE_K)
    (run / "scale_sample.txt").write_text("".join(f"{s.name}\n" for s in scale))
    for name, part in (("pilot_sample", sample), ("scale_sample", scale)):
        still_sheets([s.name for s in part], run / "stills", run / "sheets" / name)
        calls = run / f"{name}_calls.csv"
        if not calls.exists():
            fields = ["idx", "still", "video"] + (
                ["usable", "urchin", "note"] if name == "pilot_sample" else ["scale_ref", "note"]
            )
            write_csv(
                calls,
                [{"idx": i, "still": s.name, "video": s.video} for i, s in enumerate(part)],
                fields,
            )
    log.info("pilot sample %d stills, scale sample %d stills", len(sample), len(scale))
    return 0


SCALE_VALUES = {"none", "tape", "quadrat", "ruler", "laser", "other"}


def load_calls(run: Path) -> tuple[list[dict], list[dict]]:
    calls = read_csv(run / "pilot_sample_calls.csv")
    for c in calls:
        if c["usable"] not in ("yes", "no") or c["urchin"] not in ("yes", "no", "unsure"):
            raise ValueError(f"pilot call {c['idx']} {c['still']}: usable/urchin not filled")
    scale = read_csv(run / "scale_sample_calls.csv")
    for c in scale:
        if c["scale_ref"] not in SCALE_VALUES:
            raise ValueError(f"scale call {c['idx']} {c['still']}: scale_ref {c['scale_ref']!r}")
    return calls, scale


def cmd_score(run: Path) -> int:
    pilot = extracted_set(run)
    state = read_json(run / "run.json")
    calls, scale = load_calls(run)
    dets = read_json(run / "owl_prelabels.json")["detections"]
    names = kept_names(run)
    inv = {Path(r["file"]).stem: r for r in read_csv(run / "inventory.csv")}
    done = {
        e["video"]: e for e in state["extract"].values() if e.get("done") and not e.get("failed")
    }
    folder_of = {Path(c["video"]).stem: c["folder"] for c in pilot if c["video"] in done}
    v = verdict(calls, folder_of, len(folder_of))
    rows = []
    for c in pilot:
        stem = Path(c["video"]).stem
        if c["video"] not in done:
            continue
        i, q = inv[stem], done[c["video"]]["quality"]
        sc = [s for s in scale if s["video"] == stem]
        refs = sorted({s["scale_ref"] for s in sc} - {"none"})
        vv = v["videos"].get(stem, share(0, 0) | {"verdict": "too few to say"})
        rows.append(
            {
                "num": c["num"],
                "folder": c["folder"],
                "video": c["video"],
                "duration_s": i["duration_s"],
                "resolution": f"{i['width']}x{i['height']}",
                "fps": i["fps"],
                "codec": i["codec"],
                **q,
                **candidate_shares(dets, [n for n in names if n.startswith(stem + "/")]),
                "scale_ref": f"{sum(s['scale_ref'] != 'none' for s in sc)}/{len(sc)}"
                + (f" ({', '.join(refs)})" if refs else ""),
                "sample_usable": f"{vv['k']}/{vv['n']}",
                "video_verdict": vv["verdict"],
            }
        )
    write_csv(run / "quality.csv", rows, list(rows[0]))
    v["line"] = verdict_line(v)
    v["scored_utc"] = _now()
    write_json(run / "verdict.json", v)
    print(v["line"])
    return 0


def cmd_audit(run: Path) -> int:
    """24 seeded stills of the pilot sample with model-A boxes (>= 0.3 thick green, 0.05–0.3 thin
    yellow) and Claude's calls; Nick's audit of the calls."""
    import cv2

    calls, _ = load_calls(run)
    dets = read_json(run / "owl_prelabels.json")["detections"]
    rng = np.random.default_rng(0)
    pick = sorted(rng.choice(len(calls), min(AUDIT_N, len(calls)), replace=False).tolist())
    out = run / "batch"
    out.mkdir(exist_ok=True)
    width, cols, lab = 960, 2, 30
    tiles = []
    for j, idx in enumerate(pick):
        c = calls[idx]
        img = cv2.imread(str(run / "stills" / c["still"]))
        if img is None:
            raise FileNotFoundError(run / "stills" / c["still"])
        k = width / img.shape[1]
        h = round(img.shape[0] * k)
        small = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
        for d in sorted(dets.get(c["still"], []), key=lambda d: d["score"]):
            x1, y1, x2, y2 = (round(t * k) for t in d["box"])
            strong = d["score"] >= CAND_SCORES[1]
            cv2.rectangle(
                small,
                (x1, y1),
                (x2, y2),
                (0, 220, 0) if strong else (0, 200, 255),
                3 if strong else 1,
            )
        tile = np.full((h + lab, width, 3), 30, np.uint8)
        tile[:h] = small
        n05 = len(dets.get(c["still"], []))
        n3 = sum(d["score"] >= CAND_SCORES[1] for d in dets.get(c["still"], []))
        text = (
            f"A{j + 1:02d}  usable: {c['usable']}  urchin: {c['urchin']}  "
            f"[A >=0.05: {n05}, >=0.3: {n3}]  {Path(c['still']).name}"
        )
        cv2.putText(
            tile, text, (6, h + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA
        )
        tiles.append(tile)
    th = max(t.shape[0] for t in tiles)
    per_sheet = 12
    paths = []
    for s in range(math.ceil(len(tiles) / per_sheet)):
        part = tiles[s * per_sheet : (s + 1) * per_sheet]
        rows = math.ceil(len(part) / cols)
        sheet = np.full((rows * th, cols * width, 3), 30, np.uint8)
        for j, t in enumerate(part):
            r, cc = divmod(j, cols)
            sheet[r * th : r * th + t.shape[0], cc * width : (cc + 1) * width] = t
        p = out / f"audit_{s + 1}.jpg"
        cv2.imwrite(str(p), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        paths.append(p)
    write_json(
        out / "audit_index.json", [{"id": f"A{j + 1:02d}", **calls[i]} for j, i in enumerate(pick)]
    )
    print("\n".join(str(p) for p in paths))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["select", "extract", "owl", "samples", "score", "audit"])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the pipeline tools")
    ap.add_argument("--mount", type=Path, default=SSD_MOUNT)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run = args.run_dir
    if args.command == "select":
        return cmd_select(run)
    if args.command == "extract":
        return cmd_extract(run, args.python, args.mount)
    if args.command == "owl":
        return cmd_owl(run, args.python)
    if args.command == "samples":
        return cmd_samples(run)
    if args.command == "score":
        return cmd_score(run)
    return cmd_audit(run)


if __name__ == "__main__":
    sys.exit(main())
