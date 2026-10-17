"""Run the screen's encode + score jobs in priority order (RULES.md §3-4); resumable.

    python -m compression_study.video_cutsheet.run first     # primary window x boards x 500 KB
    python -m compression_study.video_cutsheet.run reefcheck # 3 windows x boards x 500 / 100 / 1000 KB
    python -m compression_study.video_cutsheet.run pool      # 6 pool clips (10 fps) x AE3 + via-Pi rows
    python -m compression_study.video_cutsheet.run pi        # via-Pi AV1 rows on the Reef Check windows
    python -m compression_study.video_cutsheet.run floor     # MJPEG at its smallest quality (target_kb 0)
    python -m compression_study.video_cutsheet.run nob       # IMX708 proxy without B-frames (sensitivity)

Each job appends one line to <run>/results.jsonl; a job already there is skipped. Two jobs in
parallel (PjM 003 all-clear: nice 10, <= 2 workers, stop below 8 GiB free).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import encode, score
from .common import POOL, RUN, SIZES_KB, STEP, WIN, ref_path

ENC = {  # key: (board, fps, codec label, function)
    "ae3_mjpeg": ("ae3", 10, "MJPEG (OpenMV software JPEG rule)"),
    "n6_mjpeg": ("n6", 15, "MJPEG (VC8000 path: 4:2:0, Q steps of 10)"),
    "n6_h264": ("n6", 15, "H.264 baseline (x264 veryfast, proxy for the N6 hardware encoder)"),
    "imx708_h264": ("imx708", 30, "H.264 high 4.2 (x264 veryfast, proxy for the Pi hardware encoder)"),
    "imx708_h264nob": ("imx708", 30, "H.264 high 4.2, no B-frames like the Pi hardware (sensitivity, post-hoc)"),
    "pi_av1_10": ("via Pi", 10, "AV1 SVT p6 low-mem on the Pi (rig #116 recipe)"),
    "pi_av1_15": ("via Pi", 15, "AV1 SVT p6 low-mem on the Pi (rig #116 recipe)"),
    "pi_av1_30": ("via Pi", 30, "AV1 SVT p6 low-mem on the Pi (rig #116 recipe)"),
}
EXT = {"mjpeg": ".mjpeg", "h264": ".h264", "av1": ".obu"}
RC = ["rc_c3", "rc_c1", "rc_lead2"]  # rc_c3 = primary window (windows.json)
BOARD_ENC = ["ae3_mjpeg", "n6_h264", "n6_mjpeg", "imx708_h264"]


def nframes(clip: str, fps: int) -> int:
    return 50 if clip.startswith("bmcam") else WIN // STEP[fps]


def job(clip: str, enc: str, kb: int) -> dict:
    board, fps, label = ENC[enc]
    nfr = nframes(clip, fps)
    ref = ref_path(clip, fps)
    target = kb * 1000
    d = RUN / "enc" / clip
    d.mkdir(parents=True, exist_ok=True)
    codec = enc.split("_")[1].replace("h264nob", "h264")
    out = d / f"{enc}_{kb}k{EXT[codec]}"
    rec = {"clip": clip, "enc": enc, "board": board, "fps": fps, "label": label, "target_kb": kb,
           "frames": nfr, "ref": ref.name, "file": str(out.relative_to(RUN))}
    t0 = time.time()
    try:
        if codec == "mjpeg" and kb == 0:  # 0 = the floor: smallest quality setting
            r = encode.mjpeg_floor(ref, out, board)
        elif codec == "mjpeg":
            r = encode.mjpeg(ref, out, target, board)
        elif codec == "h264":
            prof = "baseline" if board == "n6" else ("high_nob" if enc.endswith("nob") else "high")
            r = encode.h264(ref, out, target, fps, nfr, prof)
        else:
            r = encode.av1_pi(ref, out, target, fps, nfr)
        rec.update(r)
        if r.get("ok"):
            rec.update(score.score(out, ref, fps, nfr))
    except Exception as e:  # one failed cell never stops the others; it is listed with its reason
        rec.update({"ok": False, "reason": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]})
    rec["wall_s"] = round(time.time() - t0, 1)
    return rec


def plan(stage: str) -> list[tuple[str, str, int]]:
    if stage == "first":
        return [("rc_c3", e, 500) for e in BOARD_ENC]
    if stage == "reefcheck":
        return [(c, e, kb) for kb in SIZES_KB for c in RC for e in BOARD_ENC]
    if stage == "pool":
        return [(c, e, kb) for kb in SIZES_KB for c in POOL for e in ("ae3_mjpeg", "pi_av1_10")]
    if stage == "floor":  # MJPEG at its smallest setting, every Reef Check window
        return [(c, e, 0) for c in RC for e in ("ae3_mjpeg", "n6_mjpeg")]
    if stage == "nob":  # post-hoc sensitivity: IMX708 proxy without B-frames (Pi hardware has none)
        return [(c, "imx708_h264nob", kb) for kb in SIZES_KB for c in RC]
    if stage == "pi":
        return [(c, f"pi_av1_{fps}", kb) for kb in SIZES_KB for c in RC for fps in (10, 15, 30)]
    raise SystemExit(f"unknown stage {stage}")


def main() -> None:
    stage = sys.argv[1]
    res_path = RUN / "results.jsonl"
    done = set()
    if res_path.exists():
        for line in res_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["clip"], r["enc"], r["target_kb"]))
    todo = [j for j in plan(stage) if j not in done]
    print(f"{stage}: {len(todo)} jobs ({len(done)} already done)", flush=True)
    with ProcessPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(job, *j): j for j in todo}
        for f in as_completed(futs):
            free = shutil.disk_usage(RUN).free / 2**30
            r = f.result()
            with res_path.open("a") as fh:
                fh.write(json.dumps(r) + "\n")
            msg = (f"VMAF {r.get('vmaf_mean')} p5 {r.get('vmaf_p5')} S2 {r.get('s2_mean')} {r.get('bytes')} B"
                   if r.get("ok") else f"FAIL {r.get('reason')}")
            print(f"{r['clip']:<16} {r['enc']:<12} {r['target_kb']:>5} KB  {msg}  ({r['wall_s']} s)", flush=True)
            if free < 8:
                print(f"STOP: {free:.1f} GiB free < 8 GiB", flush=True)
                ex.shutdown(cancel_futures=True)
                break


if __name__ == "__main__":
    main()
