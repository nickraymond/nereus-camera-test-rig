"""gate_audit.py — offline A8 audit of a gate run: every counted cycle's IMX708 clip must decode.

Purpose: PREREG A8 (2026-10-15). The live gate judges the IMX708 clip on its per-frame metadata
  and a non-empty file; a truncated H.264 behind complete metadata would pass. This decodes
  every counted cycle's ``imx708.h264`` with ffprobe and recomputes the streak.

Input: a pulled gate run folder (cycles.csv + video/cycleNNN/imx708.h264 + imx708.meta.json).
Output: per cycle (decoded frames, metadata frames, verdict) on stdout, ``audit_a8.csv`` and
  ``audit_a8.json`` (final verdict: longest audited PASS streak) in the run folder.

Usage:
  python -m host_tools.gate_audit ~/NereusPool/20261015/nereus002/gate/gate007_...
Needs ffprobe on PATH. Rule: decoded >= 200 (2/3 x 30 fps x 10 s) AND decoded >=
  metadata records - 2, AND ffprobe exit 0. A cycle that was not PASS keeps its result.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

MIN_FRAMES = 200          # 2/3 x 30 fps x 10 s (A8)
META_SLACK = 2


def decoded_frames(h264: Path) -> tuple[int | None, str]:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                            str(h264)], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, repr(exc)
    if r.returncode != 0:
        return None, r.stderr.strip()[-200:]
    try:
        return int(r.stdout.strip().split(",")[0]), r.stderr.strip()[-200:]
    except ValueError:
        return None, f"unparsable: {r.stdout.strip()[:80]}"


def meta_records(meta: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from sync_video import read_frame_metadata

    return len(read_frame_metadata(meta))


def audit(run: Path) -> dict:
    rows = list(csv.DictReader((run / "cycles.csv").open()))
    out, streak, best = [], 0, 0
    for r in rows:
        n = int(r["cycle"])
        clip = run / "video" / f"cycle{n:03d}"
        rec = {"cycle": n, "live_result": r["result"], "audited_result": r["result"]}
        if r["result"] == "PASS":
            frames, err = decoded_frames(clip / "imx708.h264")
            meta = meta_records(clip / "imx708.meta.json")
            ok = frames is not None and frames >= MIN_FRAMES and frames >= meta - META_SLACK
            rec.update(decoded=frames, meta=meta, ffprobe=err, a8="ok" if ok else "FAIL")
            if not ok:
                rec["audited_result"] = "FAIL"
        if rec["audited_result"] == "PASS":
            streak += 1
        elif rec["audited_result"] != "VOID":
            streak = 0
        best = max(best, streak)
        rec["audited_streak"] = streak
        out.append(rec)
        print(f"cycle {n:3d} live {rec['live_result']:4s} audited {rec['audited_result']:4s} "
              f"decoded {rec.get('decoded')} meta {rec.get('meta')} streak {streak}")
    with (run / "audit_a8.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cycle", "live_result", "audited_result", "decoded",
                                          "meta", "a8", "ffprobe", "audited_streak"])
        w.writeheader()
        w.writerows(out)
    summary = {"run": run.name, "cycles": len(out),
               "a8_failures": [o["cycle"] for o in out if o.get("a8") == "FAIL"],
               "best_audited_streak": best, "final_audited_streak": streak}
    (run / "audit_a8.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run")
    a = ap.parse_args()
    s = audit(Path(a.run).expanduser())
    return 0 if not s["a8_failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
