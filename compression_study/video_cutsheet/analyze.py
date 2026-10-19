"""Tables for the screen: per-point results CSV, per-encoder group summary and the bracketed knee.

    python -m compression_study.video_cutsheet.analyze

Reads <run>/results.jsonl; writes results.csv + summary.json into the run folder and
results_table.md next to this file (small text, goes in git). Knee (RULES.md §4): per encoder,
the smallest size s such that every size >= s has a worst-clip VMAF within 3.0 of its 1 MB value.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import platform
import subprocess

from .common import DIVER, POOL, RUN, ref_path, sha256

HERE = Path(__file__).parent
GROUPS = {"moving camera (Reef Check, 3 windows)": ["rc_c3", "rc_c1", "rc_lead2"],
          "diver (pool round 3, 2 clips)": DIVER,
          "pool, our camera (6 clips)": POOL}
ORDER = ["imx708_h264", "imx708_h264nob", "pi_av1_30", "n6_mjpeg", "n6_h264", "pi_av1_15", "ae3_mjpeg", "pi_av1_10"]
NAME = {
    "imx708_h264": "IMX708 · H.264 Pi hardware (desk proxy) · 30 fps",
    "imx708_h264nob": "IMX708 · same, no B-frames like the Pi hardware (sensitivity, post-hoc) · 30 fps",
    "pi_av1_30": "IMX708 · AV1 on the Pi (software) · 30 fps",
    "n6_mjpeg": "N6 · MJPEG on-board hardware JPEG · 15 fps",
    "n6_h264": "N6 · H.264 on-board (needs custom firmware; desk proxy) · 15 fps",
    "pi_av1_15": "N6 → Pi · AV1 on the Pi (software) · 15 fps",
    "ae3_mjpeg": "AE3 · MJPEG on-board software JPEG · 10 fps",
    "pi_av1_10": "AE3 → Pi · AV1 on the Pi (software) · 10 fps",
}
SIZES = [100, 500, 1000]


def load() -> list[dict]:
    rows = [json.loads(x) for x in (RUN / "results.jsonl").read_text().splitlines()]
    last = {}
    for r in rows:  # a re-run of a job replaces the earlier line
        last[(r["clip"], r["enc"], r["target_kb"])] = r
    return list(last.values())


def knee(worst: dict[int, float | None]) -> str:
    """Spec rev 3: the knee is a ladder size (ceiling = 1 MB); with three sizes it is only bracketed."""
    if any(worst.get(s) is None for s in SIZES):
        return "n/a (missing sizes)"
    top = worst[1000]
    names = {100: "100 KB", 500: "500 KB", 1000: "1 MB"}
    for i, s in enumerate(SIZES):
        if all(worst[t] >= top - 3.0 for t in SIZES[i:]):
            if i == 0:
                return "100 KB (≤ 100 KB)"
            return f"{names[s]} (bracket {names[SIZES[i - 1]]}–{names[s]})"
    return "1 MB (bracket 500 KB–1 MB)"


def manifest() -> None:
    win = json.loads((RUN / "windows.json").read_text())
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "."], cwd=HERE, capture_output=True, text=True).stdout
    lines = ["# MANIFEST: E7 video screen (desk emulation, Mac only)", "",
             "- What: board x encoder screen per SPEC.md rev 3 + RULES.md (rig PR #122). No hardware touched.",
             f"- Code: nereus-camera-test-rig {sha}{' (dirty: ' + str(len(dirty.splitlines())) + ' files)' if dirty else ''},"
             " compression_study/video_cutsheet/",
             f"- Host: {platform.node()} ({platform.platform()}); unit: none",
             "- Layout: ref/ (FFV1 references), enc/<clip>/ (bitstreams as sent + VMAF logs), results.jsonl/.csv,"
             " summary.json, sheet/ (first-look page), superseded/ (pre-colour-fix run, not used)", "",
             "## Reef Check windows (RULES.md §1) and references", ""]
    for k, v in win["videos"].items():
        w = v["window"]
        lines.append(f"- {k}: {v['source']} frames {w['start']}–{w['start'] + 149} of {v['nb_frames']} "
                     f"({v['r_frame_rate']} fps), median shift {w['median_shift_px']} px{(' — ' + v['label']) if v['label'] else ''}")
        for fps in ("30", "15", "10"):
            r = v["refs"][fps]
            lines.append(f"  - {r['file']}: {r['frames']} frames, sha256 {r['sha256']}")
    lines += ["", f"Primary window: {win['primary']}", "", "## Pool references (rig #116, read-only, frames 20–69)", ""]
    for c in POOL:
        p = ref_path(c, 10)
        lines.append(f"- {p}: sha256 {sha256(p)}")
    lines += ["", "## Also here", "",
              "- checks/ae3_mjpeg_q_sweep_rc_c3.json: AE3 MJPEG size/VMAF vs quality on the primary window "
              "(PjM sanity check, Sat 3:33 PM)",
              "- peek/: contact strips and a 1:1 crop montage used for the visual check",
              "- sheet/: the first-look page as published (https://claude.ai/artifact/BDDpQpuvqjEgqZcUQJxqGi), "
              "spec.json, clips/",
              "- e7_720p_video_screen_offline/ (+ .zip): offline copy of the page for Nick; opens from disk in Chrome",
              "- superseded/: the first scoring pass before the colour-tag fix (results_pre_colorfix.jsonl), not used",
              "- Scores were produced by the scoring code as of e36447a (unchanged since; QE 006 reproduced two cells "
              "at a2931c5); the head above regenerated the tables and this file."]
    (RUN / "MANIFEST.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    manifest()
    rows = load()
    cols = ["clip", "enc", "board", "fps", "target_kb", "ok", "bytes", "quality", "subsampling", "kbps", "frames",
            "vmaf_mean", "vmaf_p5", "vmaf_min", "worst_frame", "s2_mean", "s2_min", "min_bytes", "reason"]
    with (RUN / "results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["enc"], r["clip"], r["target_kb"])):
            w.writerow(r)
    by = {(r["clip"], r["enc"], r["target_kb"]): r for r in rows}
    summary, md = {}, ["# Results table (desk emulation; see RESULTS.md)", ""]
    for gname, clips in GROUPS.items():
        md += [f"## {gname}", "",
               "| encoder | 100 KB worst / mean VMAF | 500 KB | 1 MB | knee (bracketed) | MJPEG floor (smallest setting) |",
               "|---|---|---|---|---|---|"]
        for enc in ORDER:
            cells, worst = [], {}
            have = False
            for s in SIZES:
                rs = [by.get((c, enc, s)) for c in clips]
                if all(r is None for r in rs):
                    cells.append("not run")
                    worst[s] = None
                    continue
                have = True
                ok = [r for r in rs if r and r.get("ok")]
                if len(ok) < len(clips):
                    fails = [r for r in rs if r and not r.get("ok")]
                    if fails and all("smallest Q" in (r.get("reason") or "") for r in fails):
                        cells.append(f"can't reach (floor {min(r['min_bytes'] for r in fails) / 1000:.0f} KB)")
                    else:
                        cells.append(f"{len(ok)}/{len(clips)} ok")
                    worst[s] = None if not ok else None
                    continue
                v = [r["vmaf_mean"] for r in ok]
                worst[s] = min(v)
                cells.append(f"{min(v):.1f} / {sum(v) / len(v):.1f}")
            if not have:
                continue
            fl = [by.get((c, enc, 0)) for c in clips]
            fl = [r for r in fl if r and r.get("ok")]
            floor = (f"{min(r['bytes'] for r in fl) / 1000:.0f}–{max(r['bytes'] for r in fl) / 1000:.0f} KB, "
                     f"worst VMAF {min(r['vmaf_mean'] for r in fl):.1f}") if fl else "—"
            k = knee(worst)
            summary.setdefault(gname, {})[enc] = {"worst": worst, "knee": k, "cells": cells, "floor": floor}
            md.append(f"| {NAME[enc]} | " + " | ".join(cells) + f" | {k} | {floor} |")
        md.append("")
    (RUN / "summary.json").write_text(json.dumps(summary, indent=1))
    (HERE / "results_table.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
