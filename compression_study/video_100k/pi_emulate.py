"""Pi Zero 2 W feasibility WITHOUT the Pi: Debian trixie arm64 container, calibrated (Mac).

EMULATED, NOT MEASURED ON A PI. The container has the Pi's package versions (ffmpeg 7.1.5,
SVT-AV1 2.3.0, libaom 3.12.1, x264 0.164, libjxl 0.11.2) and is pinned to 4 vCPUs of the
M1 Max. Peak RSS is measured directly (same arm64 binaries; RSS does not depend on CPU speed).
Time on the Zero = container time × a factor calibrated on real nereus002 runs:

    python -m compression_study.video_100k.pi_emulate calibrate --data <data> \
        --pr88 <rig>/data/video_20261002          # re-runs PR #88's Zero jobs in the container
    python -m compression_study.video_100k.pi_emulate measure --data <data> \
        --cands tag:stage2 --clips all            # rate search + timing in the container

The Mac must be otherwise idle while this runs (timing). Docker image: ``Dockerfile.pi`` (here)
(``docker build --platform linux/arm64 -t video100k-pi:trixie``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from .candidates import registry
from .common import REF_DIR, append_jsonl, clip_set, read_jsonl

HERE = Path(__file__).resolve().parent
IMAGE = "video100k-pi:trixie"


class Container:
    """One long-lived container (4 vCPUs, 1 GiB, no swap); commands run via docker exec."""

    def __init__(self, mounts: list[Path], cpus: str = "0-3", mem: str = "1g"):
        vols = []
        for m in mounts:
            vols += ["-v", f"{m}:{m}"]
        self.cid = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--platform",
                "linux/arm64",
                "--cpuset-cpus",
                cpus,
                "--memory",
                mem,
                "--memory-swap",
                mem,
                *vols,
                IMAGE,
                "sleep",
                "infinity",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def run(self, cmd: str, log: Path | None = None) -> dict:
        p = subprocess.run(
            ["docker", "exec", self.cid, "python3", str(HERE / "pi_probe.py"), cmd],
            capture_output=True,
            text=True,
        )
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                f.write(f"[pi-container] $ {cmd}\n{p.stdout[-500:]}\n{p.stderr[-3000:]}\n")
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        if p.returncode != 0 or not lines:
            raise RuntimeError(
                f"container command failed rc={p.returncode}: {cmd}\n{p.stderr[-1500:]}"
            )
        return json.loads(lines[-1])

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.cid], capture_output=True)


# ------------------------------------------------------------------ calibration


def calibrate(data: Path, pr88: Path, tag: str = "") -> None:
    """Re-run PR #88's real nereus002 jobs (same sources, same commands) in the container."""
    meta = json.loads((pr88 / "sources" / "sources.json").read_text())
    pi_jobs = [
        j
        for j in read_jsonl(pr88 / "enc_pi" / "jobs.jsonl")
        if j.get("status", "ok") == "ok" and j.get("kind") in ("encode", "decode_only")
    ]
    # 720p / 360p / N6 sources only (the 1080p raw files are 1.4 GB each: disk on the Mac is tight)
    stems = [
        "imx_static_720_10",
        "imx_bob_720_10",
        "imx_bob_360_10",
        "imx_static_720",
        "imx_bob_720",
    ]
    out = data / f"pi_calibration{tag}.jsonl"
    box = Container([pr88, data, HERE])
    try:
        for stem in stems:
            m = meta[stem]
            src = pr88 / "sources" / m["file"]
            for pj in [j for j in pi_jobs if j["source"] == stem and j["kind"] == "decode_only"][
                :1
            ]:
                r = box.run(f"ffmpeg -hide_banner -nostats -y -i {src} -f null -")
                append_jsonl(
                    out, {"workload": "ffv1_decode", "source": stem, "pi": _pi(pj), "box": r}
                )
                print(stem, "decode", pj["wall_s"], "vs", r["wall_s"], flush=True)
            raw = Path(f"/var/tmp/{stem}.yuv")
            box.run(f"ffmpeg -v error -y -i {src} -f rawvideo -pix_fmt yuv420p {raw}")
            inp = (
                f"-f rawvideo -pix_fmt yuv420p -s {m['width']}x{m['height']} "
                f"-r {m['fps']:.6f} -i {raw}"
            )
            for pj in [
                j for j in pi_jobs if j["source"] == stem and j.get("encoder") == "av1_svt_p12"
            ]:
                o = Path(f"/var/tmp/{stem}_{pj['budget_kB']}.mp4")
                r = box.run(
                    f"ffmpeg -hide_banner -nostats -y {inp} -c:v libsvtav1 -preset 12 "
                    f"-b:v {pj['target_bps']} "
                    f"-g 600 -svtav1-params {pj['svt_params']} -an {o}"
                )
                append_jsonl(
                    out,
                    {
                        "workload": "svt_p12_lp1",
                        "source": stem,
                        "budget_kB": pj["budget_kB"],
                        "pi": _pi(pj),
                        "box": r,
                    },
                )
                print(stem, pj["budget_kB"], "svt", pj["wall_s"], "vs", r["wall_s"], flush=True)
            box.run(f"rm -f {raw} /var/tmp/{stem}_*.mp4")
    finally:
        box.close()
    summarize_calibration(data, tag)


def _pi(j: dict) -> dict:
    return {k: j.get(k) for k in ("wall_s", "cpu_s", "maxrss_MB", "size_B")}


def summarize_calibration(data: Path, tag: str = "") -> dict:
    rows = read_jsonl(data / f"pi_calibration{tag}.jsonl")
    res = {}
    for w in sorted({r["workload"] for r in rows}):
        rw = [r for r in rows if r["workload"] == w]
        wall = [r["pi"]["wall_s"] / r["box"]["wall_s"] for r in rw]
        cpu = [r["pi"]["cpu_s"] / r["box"]["cpu_s"] for r in rw]
        rss = [r["pi"]["maxrss_MB"] / (r["box"]["peak_proc_rss_MiB"] * 1.048576) for r in rw]
        res[w] = {
            "n": len(rw),
            "wall_ratio_median": round(statistics.median(wall), 2),
            "wall_ratio_min": round(min(wall), 2),
            "wall_ratio_max": round(max(wall), 2),
            "cpu_ratio_median": round(statistics.median(cpu), 2),
            "rss_pi_over_box_median": round(statistics.median(rss), 2),
        }
    (data / f"pi_calibration{tag}_summary.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return res


# ------------------------------------------------------------------ candidate measurement


def prebuild_backgrounds(box: Container, data: Path, clips: list[str]) -> None:
    """The cached background is a once-a-day job on the unit: time it on its own (one per
    reference clip) so the per-clip residual timings do not include it."""
    from .smart import bg_ref_clip, cached_bg

    log = data / "pi_bg_jobs.jsonl"
    seen = {r["ref"] for r in read_jsonl(log)}
    for clip in clips:
        ref = bg_ref_clip(clip)
        if ref in seen:
            continue
        cmd, obu, _ = cached_bg(data, clip, 20, py="python3")
        r = box.run(cmd)
        append_jsonl(log, {"ref": ref, "bg_bytes": obu.stat().st_size, **r})
        seen.add(ref)
        print(
            f"background {ref}: {obu.stat().st_size} B, box {r['wall_s']} s, "
            f"{r['peak_tree_rss_MiB']} MiB",
            flush=True,
        )


def expand(reg: dict, name: str) -> list[str]:
    """A candidate name, or 'tag:<tag>' for every candidate carrying that tag."""
    if name.startswith("tag:"):
        return [k for k, v in reg.items() if name[4:] in v.tags]
    if name not in reg:
        raise SystemExit(f"unknown candidate: {name}")
    return [name]


def measure(data: Path, names: list[str], clips: list[str], score_it: bool = True) -> None:
    from .bench import rate_search, score_cand

    reg = registry()
    names = [m for n in names for m in expand(reg, n)]
    box = Container([data, REF_DIR, HERE])
    out = data / "results.jsonl"
    done = {
        (r["cand"], r["clip"]) for r in read_jsonl(out) if r.get("status") in ("ok", "over_cap")
    }
    try:
        if any("bgcache" in n for n in names):
            prebuild_backgrounds(box, data, clips)
        for n in names:
            c = reg[n]
            for clip in clips:
                if (n, clip) in done:
                    continue
                t0 = time.time()
                rec = {
                    "cand": n,
                    "clip": clip,
                    "family": c.family,
                    "label": c.label,
                    "tags": c.tags,
                    "k": c.k,
                    "fps": 10 / c.k,
                    "wh": list(c.wh),
                    "pre": c.pre,
                    "display": c.display,
                    "host": "pi-container (emulated)",
                }
                rec.update(rate_search(c, data, clip, threads=4, runner=box.run))
                if rec["status"] == "ok" and score_it:
                    rec.update(score_cand(c, data, clip, data / rec["file"]))
                rec["job_wall_s"] = round(time.time() - t0, 1)
                append_jsonl(out, rec)
                pf = rec.get("pi_final") or {}
                print(
                    f"DONE {n} {clip} {rec['status']} {rec['size_B']} B VMAF {rec.get('vmaf')} "
                    f"box {pf.get('wall_s')} s rss {pf.get('peak_tree_rss_MiB')} MiB",
                    flush=True,
                )
    finally:
        box.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c1 = sub.add_parser("calibrate")
    c1.add_argument("--data", type=Path, required=True)
    c1.add_argument("--pr88", type=Path, required=True)
    c1.add_argument("--tag", default="", help="e.g. _after: a second run to check host-load drift")
    c2 = sub.add_parser("measure")
    c2.add_argument("--data", type=Path, required=True)
    c2.add_argument("--cands", nargs="+", required=True)
    c2.add_argument("--clips", default="explore")
    c2.add_argument("--no-score", action="store_true")
    c3 = sub.add_parser("summary")
    c3.add_argument("--data", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "calibrate":
        calibrate(a.data, a.pr88, a.tag)
    elif a.cmd == "measure":
        measure(a.data, a.cands, clip_set(a.clips), not a.no_score)
    else:
        summarize_calibration(a.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
