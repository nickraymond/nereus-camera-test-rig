#!/usr/bin/env python3
"""pool2_bench.py — run one pool 2 bench setting: n synced clips, the test card's bars, fail fast.

Purpose: pool test 2 prep part B (docs/pool2_20261021/TEST_CARD_B.md §4–§5). One command per
  setting, so every setting runs and is judged the same way:
  - before each clip:
    - ~/.no_dive_autostart present;
    - no recorder process running (ci-record, rpicam-*, sync_video.py, ...);
    - SD free >= 2 GB;
    - UPS VBAT >= 3.4 V unless on the charger;
  - one clip = scripts/sync_video.py with the setting's per-board args;
  - the clip's rows come from scripts/video_clip_table.py, scored against the card's bars;
  - a throttled clip is VOID: cool down 5 min and re-run it; 2 throttled in a row -> stop;
  - the setting stops at its first clip that misses a bar (one miss fails the setting).

Usage (on the Pi, repo root):
  python scripts/pool2_bench.py <label> --under openmv_n6 [--n 3] [--ae3-floor 9.1] \\
      -- --n6-framesize HD --timing
  --under: the board this setting changes (its fps bar applies); the other board stays at its
  control and is recorded (a drop is flagged "cross_effect", not a bar).
Outputs: results/pool2_bench/<label>/clipNN/ (sync_video files) + setting.json (rows, misses,
  verdict). Exit 0 = the setting HOLDS, 1 = it fails or stopped, 2 = a pre-check refused.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "results" / "pool2_bench"
DIVE_FLAG = Path.home() / ".no_dive_autostart"
SD_MIN_FREE_B = 2_000_000_000
VBAT_MIN_MV = 3400
COOL_DOWN_S = 300
# The card's bars (TEST_CARD_B.md §5 = spec pass/fail).
FPS_MIN = 9.5
SKEW_MAX_MS = 25.0
IMX_FPS_MIN = 26.0
IMX_GAP_MAX_MS = 100.0
AE3_REL_BELOW = 0.2


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clip_misses(rows: list[dict], under: str, ae3_floor: float | None = None) -> dict:
    """The card's bars for one clip. Pure: ``rows`` are video_clip_table.clip_rows() output.
    Returns misses (bar names), void (throttled), skew_only (the only miss is a start skew that
    involves the IMX708, for d55) and cross-effect flags for the board not under test."""
    by = {r["camera"]: r for r in rows}
    misses, cross = [], []
    imx = by.get("imx708") or {}
    if (imx.get("fps") or 0) < IMX_FPS_MIN:
        misses.append("imx708_fps")
    if imx.get("max_gap_ms") is None or imx["max_gap_ms"] > IMX_GAP_MAX_MS:
        misses.append("imx708_max_gap")
    for cam in ("openmv_n6", "openmv_ae3"):
        r = by.get(cam) or {}
        floor = ae3_floor if (cam == "openmv_ae3" and ae3_floor) else FPS_MIN
        err = str(r.get("error") or "")
        lost = r.get("frames_lost")
        bad = []
        if (r.get("fps") or 0) < floor:
            bad.append(f"{cam}_fps")
        if lost != 0 or err:  # an errored stream or an unknown count counts as lost
            bad.append(f"{cam}_lost")
        if err.startswith("arm:"):
            bad.append(f"{cam}_wedge")
        if cam == under:
            misses += bad
        else:
            cross += bad
    skew = rows[0].get("start_skew_ms") if rows else None
    skew_miss = skew is None or skew > SKEW_MAX_MS
    if skew_miss:
        misses.append("start_skew")
    delays = [r.get("first_frame_delay_ms") for r in rows if r.get("first_frame_delay_ms")
              is not None]
    imx_d = (by.get("imx708") or {}).get("first_frame_delay_ms")
    imx_involved = imx_d is not None and imx_d in (max(delays), min(delays))
    throttled = any((r.get("host_throttled") or "0x0") != "0x0" for r in rows)
    return {"misses": misses, "void": throttled, "cross_effect": cross,
            "skew_only": skew_miss and misses == ["start_skew"] and imx_involved}


def prechecks() -> list[str]:
    """Card §4 step 0 / §8, before every clip."""
    gate = _load("gate_cycle")
    refuse = []
    if not DIVE_FLAG.exists():
        refuse.append("~/.no_dive_autostart missing (dive autostart would grab the cameras)")
    procs = gate.recorder_procs()
    if procs:
        refuse.append(f"recorder running: {procs}")
    free = shutil.disk_usage(ROOT / "results").free
    if free < SD_MIN_FREE_B:
        refuse.append(f"SD free {free} B < 2 GB")
    try:
        pw = _load("pool_capture").ups_power()
    except Exception:  # noqa: BLE001 - no UPS reading is a refusal, not a crash
        pw = {}
    if not pw:
        refuse.append("no UPS reading")
    elif pw.get("on_battery") and pw.get("vbat_mv", 0) < VBAT_MIN_MV:
        refuse.append(f"on battery at {pw.get('vbat_mv')} mV < {VBAT_MIN_MV}")
    return refuse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("label")
    ap.add_argument("--under", required=True, choices=("openmv_n6", "openmv_ae3"))
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--ae3-floor", type=float, default=None,
                    help="AE3 relative bar: its own ctl 640x400 rate - 0.2 (card §5), if B0 failed")
    ap.add_argument("--no-fail-fast", action="store_true", help="probe runs only (N9)")
    ap.add_argument("sync_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    sync_args = [x for x in a.sync_args if x != "--"]
    table = _load("video_clip_table")
    out = OUT_ROOT / a.label
    out.mkdir(parents=True, exist_ok=True)
    rec = {"label": a.label, "under": a.under, "n": a.n, "sync_args": sync_args,
           "ae3_floor": a.ae3_floor, "started_utc": datetime.now(timezone.utc).isoformat(),
           "clips": []}
    voids_in_row, k = 0, len(list(out.glob("clip[0-9]*")))
    scored_n = 0
    while scored_n < a.n:
        refuse = prechecks()
        if refuse:
            rec["stopped"] = f"precheck: {refuse}"
            print(f"pool2_bench {a.label}: REFUSED {refuse}", flush=True)
            break
        k += 1
        clip = out / f"clip{k:02d}"
        cmd = [sys.executable, str(ROOT / "scripts" / "sync_video.py"), "--seconds",
               str(a.seconds), "--out", str(clip), *sync_args]
        print(f"pool2_bench {a.label}: clip {k}: {' '.join(cmd[2:])}", flush=True)
        subprocess.run(cmd, timeout=a.seconds + 180)
        rows = table.clip_rows(clip) if (clip / "video_sync.json").exists() else []
        res = clip_misses(rows, a.under, a.ae3_floor) if rows else {
            "misses": ["no_video_sync_json"], "void": False, "cross_effect": [],
            "skew_only": False}
        rec["clips"].append({"clip": clip.name, **res, "rows": rows})
        print(f"pool2_bench {a.label}: clip {k}: misses {res['misses']} void {res['void']} "
              f"cross {res['cross_effect']}", flush=True)
        if res["void"]:
            voids_in_row += 1
            if voids_in_row >= 2:
                rec["stopped"] = "2 throttled clips in a row (card §8 stop rule 3)"
                break
            time.sleep(COOL_DOWN_S)
            continue
        voids_in_row = 0
        scored_n += 1
        if res["misses"] and not a.no_fail_fast:
            break
    scored = [c for c in rec["clips"] if not c["void"]]
    rec["holds"] = (len(scored) >= a.n and all(not c["misses"] for c in scored[:a.n])
                    and "stopped" not in rec)
    rec["skew_only"] = bool(scored) and all(c["skew_only"] or not c["misses"] for c in scored) \
        and any(c["skew_only"] for c in scored)
    (out / "setting.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
    print(f"pool2_bench {a.label}: {'HOLDS' if rec['holds'] else 'FAILS'}"
          f"{' (skew-only, d55)' if rec['skew_only'] else ''} · {len(scored)} scored clip(s) · "
          f"{rec.get('stopped', '')}", flush=True)
    if "stopped" in rec and rec["stopped"].startswith("precheck"):
        return 2
    return 0 if rec["holds"] else 1


if __name__ == "__main__":
    sys.exit(main())
