"""Summarise the video bench: quality at each budget, size for a quality, device cost (Mac).

    python -m compression_study.video.analyze --data <data>     # → <data>/summary.json

Reads ``scores.jsonl`` (score.py), ``enc_pi/jobs.jsonl`` + ``enc_mac/jobs.jsonl`` (timings),
``sources/sources.json`` and the N6 master/probe records.

Quality at a budget B is read off each encoder's own size→VMAF curve (piecewise linear in
log size, made monotone), i.e. what that encoder gives when its target is tuned to land on B:
- B inside the encoder's measured size range → interpolated;
- B above its largest file → the largest file's quality (it did not need more), flagged;
- B below its smallest file → ``None`` with the floor size: the encoder cannot get that small
  (the Pi hardware H.264 at 1080p with motion bottoms out near 410 kB whatever the target).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from compression_study.video.score import load_scores

BUDGETS = [50, 100, 200, 400, 800, 1600]
VMAF_TARGETS = [60, 70, 80, 90]
# Same camera and scene at different resolutions / frame rates, all scored full-screen
GROUPS = {
    "imx_bob": ["imx_bob_1080", "imx_bob_720", "imx_bob_720_10", "imx_bob_360_10"],
    "imx_static": ["imx_static_1080", "imx_static_720", "imx_static_720_10"],
    "n6_bob": ["n6_bob", "n6_bob_9", "n6_bob_400_9"],
    "n6_static": ["n6_static"],
}


def curve(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort by size, keep a monotone (non-decreasing) VMAF envelope."""
    pts = sorted(points)
    out, best = [], -1e9
    for s, v in pts:
        best = max(best, v)
        out.append((s, best))
    return out


def at_size(c: list[tuple[float, float]], kb: float) -> dict:
    if not c:
        return {"vmaf": None}
    if kb < c[0][0]:
        return {"vmaf": None, "floor_kB": round(c[0][0], 1)}
    if kb >= c[-1][0]:
        return {"vmaf": round(c[-1][1], 1), "capped": True, "used_kB": round(c[-1][0], 1)}
    for (s0, v0), (s1, v1) in zip(c, c[1:]):
        if s0 <= kb <= s1:
            t = (math.log(kb) - math.log(s0)) / (math.log(s1) - math.log(s0)) if s1 > s0 else 0
            return {"vmaf": round(v0 + t * (v1 - v0), 1)}
    return {"vmaf": None}


def size_for(c: list[tuple[float, float]], vmaf: float):
    if not c or c[-1][1] < vmaf:
        return None
    if c[0][1] >= vmaf:
        return {"kB": round(c[0][0], 1), "at_or_below": True}
    for (s0, v0), (s1, v1) in zip(c, c[1:]):
        if v0 <= vmaf <= v1 and v1 > v0:
            t = (vmaf - v0) / (v1 - v0)
            return {"kB": round(math.exp(math.log(s0) + t * (math.log(s1) - math.log(s0))), 1)}
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    a = ap.parse_args(argv)
    d = a.data
    scores = load_scores(d)
    sources = json.loads((d / "sources/sources.json").read_text())
    jobs = []
    for f in ("enc_pi/jobs.jsonl", "enc_mac/jobs.jsonl"):
        if (d / f).exists():
            jobs += [
                dict(json.loads(ln), host=f.split("/")[0])
                for ln in (d / f).read_text().splitlines()
                if ln.strip()
            ]
    decode = {j["source"]: j for j in jobs if j.get("kind") == "decode_only"}
    idle_w = next((j["mean_W"] for j in jobs if j.get("kind") == "idle"), None)

    pts = defaultdict(list)
    pts_disp = defaultdict(list)
    rows = []
    for s in scores:
        key = (s["source"], s["encoder"])
        kb = s["size_B"] / 1000
        pts[key].append((kb, s["vmaf"]))
        s["vmaf_display"] = s.get("vmaf_display", s.get("vmaf_1080", s["vmaf"]))
        pts_disp[key].append((kb, s["vmaf_display"]))
        rows.append(
            {
                k: s[k]
                for k in (
                    "source",
                    "encoder",
                    "budget_kB",
                    "size_B",
                    "real_bps",
                    "vmaf",
                    "vmaf_p5",
                    "vmaf_min",
                    "psnr_y",
                    "ssim",
                    "keyframes",
                    "codec",
                )
            }
            | {"vmaf_display": s["vmaf_display"], "path": s["path"]}
        )
    curves = {f"{k[0]}|{k[1]}": curve(v) for k, v in pts.items()}
    curves_disp = {f"{k[0]}|{k[1]}": curve(v) for k, v in pts_disp.items()}

    at_budget = {k: {b: at_size(c, b) for b in BUDGETS} for k, c in curves.items()}
    at_budget_display = {k: {b: at_size(c, b) for b in BUDGETS} for k, c in curves_disp.items()}
    size_needed = {k: {v: size_for(c, v) for v in VMAF_TARGETS} for k, c in curves.items()}
    size_needed_display = {
        k: {v: size_for(c, v) for v in VMAF_TARGETS} for k, c in curves_disp.items()
    }
    encoders = sorted({k.split("|")[1] for k in curves})
    best = {}
    for g, stems in GROUPS.items():
        for enc in encoders:
            for b in BUDGETS:
                cands = [
                    (at_budget_display[f"{st}|{enc}"][b].get("vmaf"), st)
                    for st in stems
                    if f"{st}|{enc}" in at_budget_display
                ]
                cands = [c for c in cands if c[0] is not None]
                if cands:
                    v, st = max(cands)
                    best.setdefault(g, {}).setdefault(enc, {})[b] = {"vmaf": v, "source": st}

    cost = []
    for j in jobs:
        if j.get("kind") != "encode":
            continue
        dec = decode.get(j["source"], {})
        rec = {
            k: j.get(k)
            for k in (
                "host",
                "source",
                "encoder",
                "budget_kB",
                "size_B",
                "status",
                "input",
                "wall_s",
                "cpu_s",
                "maxrss_MB",
                "energy_J",
                "mean_W",
                "min_avail_MB",
                "frames",
                "duration_s",
            )
        }
        if j["host"] == "enc_pi" and dec and j.get("status") == "ok":
            # encode-only estimate: total minus decoding the lossless FFV1 source alone
            rec["encode_only_s"] = round(max(0.0, j["wall_s"] - dec["wall_s"]), 1)
            if idle_w and j.get("energy_J") is not None:
                rec["energy_above_idle_J"] = round(j["energy_J"] - idle_w * j["wall_s"], 1)
                rec["decode_above_idle_J"] = round(dec["energy_J"] - idle_w * dec["wall_s"], 1)
        cost.append(rec)

    n6 = {}
    for q in ("q90", "q70"):
        p = d / f"n6_{q}/master.json"
        if p.exists():
            m = json.loads(p.read_text())
            n6[q] = {
                k: m[k]
                for k in (
                    "jpeg_quality",
                    "frames",
                    "duration_s",
                    "fps_mean",
                    "dt_ms",
                    "bytes_per_frame_mean",
                    "link_MBps",
                    "dims",
                )
            }
    probe = d / "n6_probe/n6_probe.jsonl"
    if probe.exists():
        n6["probe"] = []
        for ln in probe.read_text().splitlines():
            line = json.loads(ln)["line"]
            if line.startswith("#R "):
                _, tag, js = line.split(" ", 2)
                n6["probe"].append({"tag": tag, **json.loads(js)})

    summary = {
        "budgets_kB": BUDGETS,
        "vmaf_targets": VMAF_TARGETS,
        "sources": {k: v for k, v in sources.items() if not k.startswith("_")},
        "motion": sources.get("_motion"),
        "idle_W": idle_w,
        "decode_only": decode,
        "curves": curves,
        "curves_display": curves_disp,
        "at_budget": at_budget,
        "at_budget_display": at_budget_display,
        "size_needed": size_needed,
        "size_needed_display": size_needed_display,
        "groups": GROUPS,
        "best_per_budget": best,
        "rows": rows,
        "cost": cost,
        "n6": n6,
    }
    (d / "summary.json").write_text(json.dumps(summary, indent=1))
    # console digest
    for k in sorted(at_budget):
        line = "  ".join(
            f"{b}:{(r['vmaf'] if r.get('vmaf') is not None else '-'):>5}"
            for b, r in at_budget[k].items()
        )
        print(f"{k:34s} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
