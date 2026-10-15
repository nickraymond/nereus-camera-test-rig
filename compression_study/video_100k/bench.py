"""Encode + score candidates on the pool clips at ≤ 100,000 B per clip (Mac).

    python -m compression_study.video_100k.bench --data <data> --cands x264_vs svt3_p4 \
        --clips explore --jobs 3

Per (candidate, clip): search the rate knob until the bitstream lands in 95–100 % of the cap
(``kbps``: proportional correction, ≤ 7 attempts; ``q``: bisection), keep the largest attempt
≤ cap, then score it (``score.py``). One JSON line per job in ``<data>/results.jsonl`` with the
attempts, final size, encode wall time on the Mac and all metrics. A (candidate, clip) already
in results.jsonl is skipped, so the run resumes.

Outputs: ``<data>/enc/<cand>/<clip>.<ext>`` (the sent file), ``<data>/logs/<cand>/<clip>.log``.
A candidate that cannot get under the cap is recorded with status ``over_cap`` (not scored).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .candidates import KBPS0, Cand, Ctx, n_frames, registry, source_vf
from .common import CLIPS, EXPLORE, NFR, append_jsonl, q, read_jsonl, sh, src_path
from .score import score

LOCK = threading.Lock()
Y4M_LOCKS: dict[str, threading.Lock] = {}


def y4m_for(data: Path, clip: str, vf: str, fr: str) -> Path:
    """Cached y4m of the filtered source (for encoders that need a seekable file)."""
    key = hashlib.sha1(f"{clip}|{vf}|{fr}".encode()).hexdigest()[:10]
    p = data / "y4m" / f"{clip}_{key}.y4m"
    with LOCK:
        lk = Y4M_LOCKS.setdefault(str(p), threading.Lock())
    with lk:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            vfa = f'-vf "{vf}"' if vf else ""
            sh(
                f"ffmpeg -hide_banner -v error -y -i {q(src_path(clip))} {vfa} -fps_mode cfr "
                f"-r {fr} -pix_fmt yuv420p -f yuv4mpegpipe {q(p)}.part && mv {q(p)}.part {q(p)}"
            )
    return p


def attempt(
    c: Cand, data: Path, clip: str, knob: float, i: int, threads: int, runner=None
) -> tuple[int, float, Path, dict | None]:
    """One encode at one knob value. ``runner`` (pi_emulate) runs it in the Pi container and
    returns its measurements; the y4m (if any) is then made inside the timed command."""
    out_dir = data / "enc" / c.name / "attempts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{clip}_{i}.{c.ext}"
    tmp = data / "tmp" / c.name / f"{clip}_{i}"
    tmp.mkdir(parents=True, exist_ok=True)
    vf, fr = source_vf(c)
    ctx = Ctx(
        clip=clip, src=src_path(clip), out=out, tmp=tmp, vf=vf, fr=fr, threads=c.threads or threads
    )
    meas = None
    log = data / "logs" / c.name / f"{clip}.log"
    if runner is None:
        if c.needs_y4m:
            ctx.y4m = y4m_for(data, clip, vf, fr)
        wall = sh(c.build(ctx, knob), log=log)
    else:
        pre = ""
        if c.needs_y4m:  # on the device the y4m is part of the job: timed, on disk (SD card)
            ctx.y4m = Path(f"/var/tmp/{clip}_{i}.y4m")
            vfa = f'-vf "{vf}"' if vf else ""
            pre = (
                f"ffmpeg -hide_banner -v error -y -i {q(src_path(clip))} {vfa} "
                f"-fps_mode cfr -r {fr} "
                f"-pix_fmt yuv420p -f yuv4mpegpipe {q(ctx.y4m)} && "
            )
        cmd = pre + c.build(ctx, knob) + (f" && rm -f {q(ctx.y4m)}" if c.needs_y4m else "")
        meas = runner(cmd, log)
        wall = meas["wall_s"]
    shutil.rmtree(tmp, ignore_errors=True)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"{c.name}/{clip}: encoder produced no output")
    return out.stat().st_size, wall, out, meas


def rate_search(c: Cand, data: Path, clip: str, threads: int, runner=None) -> dict:
    tries = []
    cap = c.cap  # per-candidate budget (100,000 B unless a bundle splits it)

    def run(knob):
        size, wall, path, meas = attempt(c, data, clip, knob, len(tries), threads, runner)
        tries.append(
            {
                "knob": round(knob, 4),
                "size_B": size,
                "wall_s": round(wall, 2),
                "path": path,
                **({"pi": meas} if meas else {}),
            }
        )
        print(
            f"  {c.name:22s} {clip} try{len(tries)} knob={knob:.3f} -> {size} B ({wall:.0f} s)",
            flush=True,
        )
        return size

    if c.knob == "kbps":
        t = KBPS0 * 0.97 * cap / 100_000 * NFR / c.nfr
        for _ in range(7):
            size = run(t)
            if 0.95 * cap <= size <= cap:
                break
            ratio = min(3.0, max(0.33, 0.975 * cap / size))
            if any(abs(tr["knob"] - t * ratio) < 1e-3 for tr in tries):
                break
            t *= ratio
    else:
        lo, hi, integer = c.q_range
        if run(hi) > cap:  # even the lowest quality does not fit
            pass
        else:
            a, b = lo, hi  # invariant: size(b) <= cap
            if run(a) <= cap:
                b = a
            else:
                for _ in range(10):
                    if integer and b - a <= 1:
                        break
                    if not integer and b - a < 0.01:
                        break
                    m = (a + b) // 2 if integer else (a + b) / 2
                    s = run(m)
                    if s <= cap:
                        b = m
                        if s >= 0.97 * cap:
                            break
                    else:
                        a = m
    ok = [t for t in tries if t["size_B"] <= cap]
    best = max(ok, key=lambda t: t["size_B"]) if ok else None
    final = data / "enc" / c.name / f"{clip}.{c.ext}"
    if best:
        shutil.copy(best["path"], final)
    for t in tries:
        Path(t["path"]).unlink(missing_ok=True)
        t["path"] = str(Path(t["path"]).name)
    return {
        "attempts": tries,
        "status": "ok" if best else "over_cap",
        "size_B": best["size_B"] if best else min(t["size_B"] for t in tries),
        "knob": best["knob"] if best else None,
        "enc_wall_mac_s": best["wall_s"] if best else None,
        "pi_final": best.get("pi") if best else None,
        "file": str(final.relative_to(data)) if best else None,
        "cap_B": cap,
    }


def score_cand(c: Cand, data: Path, clip: str, f: Path, s2: bool = True) -> dict:
    """Score the sent file; candidates with a custom display (stills, background+residual)
    first rebuild what the viewer sees as a lossless 293-frame file."""
    cache = data / "cache"
    if c.display_file is None:
        return score(
            clip,
            f,
            k=c.k,
            wh=c.wh,
            display=c.display,
            dec=c.dec,
            expect_frames=n_frames(c),
            cache=cache,
            s2=s2,
            nfr=c.nfr,
        )
    disp, extra = c.display_file(f, clip, data / "tmp" / c.name / f"{clip}_display")
    try:
        rec = score(clip, disp, expect_frames=NFR, cache=cache, s2=s2)
    finally:
        disp.unlink(missing_ok=True)
    rec.update(extra)
    return rec


def run_job(c: Cand, data: Path, clip: str, threads: int, s2: bool) -> None:
    t0 = time.time()
    try:
        rec = {
            "cand": c.name,
            "clip": clip,
            "family": c.family,
            "label": c.label,
            "tags": c.tags,
            "k": c.k,
            "fps": 10 / c.k,
            "wh": list(c.wh),
            "pre": c.pre,
            "display": c.display,
        }
        rec.update(rate_search(c, data, clip, threads))
        if rec["status"] == "ok":
            rec.update(score_cand(c, data, clip, data / rec["file"], s2))
        rec["job_wall_s"] = round(time.time() - t0, 1)
        with LOCK:
            append_jsonl(data / "results.jsonl", rec)
        print(
            f"DONE {c.name:22s} {clip} {rec['status']} {rec['size_B']} B "
            f"VMAF {rec.get('vmaf')} S2 {rec.get('ssimulacra2')} card {rec.get('card_ssim_y')}",
            flush=True,
        )
    except Exception as e:  # loud, recorded, and the batch continues
        traceback.print_exc()
        with LOCK:
            append_jsonl(
                data / "results.jsonl",
                {"cand": c.name, "clip": clip, "status": "error", "error": str(e)[-1500:]},
            )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--cands", nargs="+", required=True, help="candidate names, or 'tag:<tag>'")
    ap.add_argument("--clips", default="explore", help="explore | all | comma list")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--no-s2", action="store_true")
    ap.add_argument("--redo", action="store_true", help="re-run jobs already in results.jsonl")
    a = ap.parse_args(argv)
    reg = registry()
    names = []
    for n in a.cands:
        names += [k for k, v in reg.items() if n[4:] in v.tags] if n.startswith("tag:") else [n]
    missing = [n for n in names if n not in reg]
    if missing:
        raise SystemExit(f"unknown candidates: {missing}")
    clips = EXPLORE if a.clips == "explore" else CLIPS if a.clips == "all" else a.clips.split(",")
    done = {
        (r["cand"], r["clip"])
        for r in read_jsonl(a.data / "results.jsonl")
        if r.get("status") != "error"
    }
    jobs = [(reg[n], c) for n in names for c in clips if a.redo or (n, c) not in done]
    print(f"data={a.data} jobs={len(jobs)} parallel={a.jobs} threads/job={a.threads}", flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for c, clip in jobs:
            ex.submit(run_job, c, a.data, clip, a.threads, not a.no_s2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
