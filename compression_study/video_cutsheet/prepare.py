"""Pick the Reef Check 5 s windows by RULES.md §1 and write the FFV1 references (§2).

    python -m compression_study.video_cutsheet.prepare

Per video: frame count from ffprobe; for start = 50 %, 25 %, 75 % of the frames, decode the 150-frame
window at 320x180 luma and measure the median global shift between consecutive frames (phase
correlation). The first start whose median is >= 0.5 px is the window. Then one 1280x720 decode of
that window is written as FFV1 at 30 / 15 / 10 fps (decimation 1 / 2 / 3). Writes windows.json.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

import cv2
import numpy as np

from .common import REF, RUN, SHORT, STEP, WIN, H, W, REEFCHECK, q, sh, sha256, src_file

FRACS = [0.50, 0.25, 0.75]
MOTION_MIN_PX = 0.5
SW, SH = 320, 180


def nb_frames(path) -> tuple[int, str]:
    out = sh(f"ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames,r_frame_rate "
             f"-of json {q(path)}").stdout
    s = json.loads(out)["streams"][0]
    return int(s["nb_frames"]), s["r_frame_rate"]


def seek_s(start: int, rate: str) -> str:
    num, den = (int(x) for x in rate.split("/"))
    return f"{start * den / num:.6f}"


def luma_window(path, start: int, rate: str) -> np.ndarray:
    """150 frames of luma at 320x180 (accurate seek: ffmpeg decodes from the key frame before)."""
    cmd = (f"ffmpeg -v error -hwaccel videotoolbox -ss {seek_s(start, rate)} -i {q(path)} "
           f"-frames:v {WIN} -vf scale={SW}:{SH}:flags=area,format=gray -f rawvideo -")
    raw = subprocess.run(cmd, shell=True, check=True, capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, SH, SW)
    if len(fr) != WIN:
        raise RuntimeError(f"{path.name} @ {start}: decoded {len(fr)} frames, expected {WIN}")
    return fr


def motion(fr: np.ndarray) -> dict:
    win = cv2.createHanningWindow((SW, SH), cv2.CV_64F)
    shifts = []
    for a, b in zip(fr[:-1], fr[1:]):
        (dx, dy), _ = cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64), win)
        shifts.append(float(np.hypot(dx, dy)))
    s = np.array(shifts)
    return {"median_shift_px": round(float(np.median(s)), 3), "p90_shift_px": round(float(np.percentile(s, 90)), 3),
            "luma_mean": round(float(fr.mean()), 1), "luma_std": round(float(fr.std()), 1)}


def write_refs(path, start: int, rate: str, short: str) -> dict:
    """One decode of the window at 1280x720 -> FFV1 at 30 / 15 / 10 fps."""
    REF.mkdir(parents=True, exist_ok=True)
    base = (f"scale={W}:{H}:flags=bicubic:in_range=full:out_range=tv,format=yuv420p")
    chains, maps = [], []
    for i, (fps, k) in enumerate(sorted(STEP.items(), reverse=True)):
        sel = f"select='not(mod(n\\,{k}))'," if k > 1 else ""
        chains.append(f"[s{i}]{sel}settb=1/{fps},setpts=N[o{i}]")
        maps.append(f"-map [o{i}] -r {fps} -c:v ffv1 -level 3 -g 1 -an {q(REF / f'{short}_{fps}fps.mkv')}")
    split = f"[0:v]trim=end_frame={WIN},{base},split=3[s0][s1][s2];" + ";".join(chains)
    t0 = time.time()
    sh(f"ffmpeg -v error -y -hwaccel videotoolbox -ss {seek_s(start, rate)} -i {q(path)} "
       f"-filter_complex {q(split)} " + " ".join(maps))
    out = {}
    for fps, k in STEP.items():
        p = REF / f"{short}_{fps}fps.mkv"
        n = int(sh(f"ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames "
                   f"-of csv=p=0 {q(p)}").stdout.strip())
        if n != WIN // k:
            raise RuntimeError(f"{p.name}: {n} frames, expected {WIN // k}")
        out[str(fps)] = {"file": p.name, "frames": n, "bytes": p.stat().st_size, "sha256": sha256(p)}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(RUN).free / 2**30
    if free < 8 + 1.2:
        raise SystemExit(f"stop rule: {free:.1f} GiB free < 8 GiB + largest source")
    res = {"rule": "RULES.md §1", "fracs": FRACS, "motion_min_px": MOTION_MIN_PX, "videos": {}}
    for name in REEFCHECK:
        path = src_file(name)
        n, rate = nb_frames(path)
        tried = []
        pick = None
        for f in FRACS:
            start = int(f * n)
            m = motion(luma_window(path, start, rate))
            tried.append({"frac": f, "start": start, **m})
            print(f"{SHORT[name]}  start {f:.2f} (frame {start}): {m}", flush=True)
            if m["median_shift_px"] >= MOTION_MIN_PX:
                pick = tried[-1]
                break
        label = "" if pick else "low camera motion"
        pick = pick or tried[0]
        refs = write_refs(path, pick["start"], rate, SHORT[name])
        res["videos"][SHORT[name]] = {"source": str(path), "nb_frames": n, "r_frame_rate": rate, "tried": tried,
                                      "window": pick, "label": label, "refs": refs}
    primary = max(res["videos"], key=lambda k: res["videos"][k]["window"]["median_shift_px"])
    res["primary"] = primary
    (RUN / "windows.json").write_text(json.dumps(res, indent=1))
    print("primary window:", primary)


if __name__ == "__main__":
    main()
