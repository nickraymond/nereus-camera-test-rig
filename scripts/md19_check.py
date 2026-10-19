#!/usr/bin/env python3
"""md19_check.py — pool test 2 must do 19 (spec rev 15): CMA bar + capture bar per setting.

Purpose: spec rev 15 runs three 30 s clips per IMX708 buffer setting (a) --imx-buffers 4, then (b)
  --imx-buffers 2, at the exact Wed program, with scripts/pool2_bench.py (one label per setting,
  TEST_CARD_B rev 4). pool2_bench judges fps / lost / max_gap / sync per clip but reads no kernel
  log, so this script adds what rev 15 asks for, per clip of a label:
  - driver-held CMA peak: /sys/kernel/debug/cma/*/used vs count. Not readable on nereus002
    (kernel 6.18.34+rpt-rpi-v8 has CONFIG_CMA_DEBUGFS / CONFIG_CMA_SYSFS not set, rig session
    Sun 10/18), so it is reported "unknown" and the CMA bar is the next line only (rev 15);
  - CMA / dma-buf allocation errors in the kernel log for the clip window: every kernel line of
    this boot (`journalctl -k -b -o short-monotonic`) inside [first fire - 5 s, last fire +
    seconds + 10 s] on CLOCK_MONOTONIC (the clip's own clock) that names CMA / DMA / dma-buf /
    memory AND a failure. Every CMA / DMA line in the window is listed verbatim either way;
  - host reset: none when this boot's monotonic clock is past every clip's end and this boot's
    journal starts before the first clip (a reset during or after the clips makes the windows
    not this boot's: "unknown: rebooted since", so run this right after each label);
  - lost frames, rev 9 fps / max_gap per camera, from the label's setting.json rows: each board
    0 frames_lost and fps_clip >= 9.5; the IMX708 fps_clip >= 26.0 and max_gap <= 100 ms (its
    frames_lost counts frames under the 30 fps nominal, which rev 9's 26 fps bar allows);
  - mem_avail: min per clip from the rows (report-only; rev 4 flag 150 MB).
  Bars, all 3 clips: CMA = 0 allocation errors (driver-held peak unknown); capture = 0 lost, fps /
  max_gap hold, no host reset.

Usage (on the Pi, repo root, right after `pool2_bench.py <label> ...`):
  python scripts/md19_check.py md19_buf4 [--bench-root results/pool2_bench] [--journal FILE]
  --journal: read the kernel log from FILE instead of running journalctl (tests, or a saved copy).
Writes <bench-root>/<label>/md19.json and prints one line per clip + the verdict.
Exit: 0 = both bars pass, 1 = the capture bar fails, 5 = only the CMA bar fails, 6 = can't judge
  (fewer than 3 clips, a clip without its files, or the windows aren't this boot's), 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N_CLIPS = 3
BOARD_FPS_MIN = 9.5      # rev 9 (TEST_CARD_B §5)
IMX_FPS_MIN = 26.0
IMX_GAP_MAX_MS = 100.0
MEM_FLAG_MB = 150        # rev 4, report-only
WINDOW_BEFORE_S, WINDOW_AFTER_S = 5.0, 10.0
CMA_DEBUGFS = Path("/sys/kernel/debug/cma")
EXIT_OK, EXIT_CAPTURE, EXIT_USAGE, EXIT_CMA, EXIT_UNKNOWN = 0, 1, 2, 5, 6
LINE_RE = re.compile(r"^\[\s*(\d+\.\d+)\]\s*(.*)$")
TOPIC_RE = re.compile(r"\bcma\b|cma_alloc|dma[-_ ]?buf|dma_alloc|dmabuf|\bdma\b|memory|oom|"
                      r"page allocation", re.I)
FAIL_RE = re.compile(r"fail|error|out of memory|oom-kill|cannot|unable|no space|-12\b|ENOMEM",
                     re.I)


def parse_journal(text: str) -> list[tuple[float, str]]:
    """journalctl -o short-monotonic lines -> [(monotonic s, message)]; other lines dropped."""
    out = []
    for raw in text.splitlines():
        m = LINE_RE.match(raw.strip())
        if m:
            out.append((float(m.group(1)), m.group(2)))
    return out


def clip_window(clip: Path) -> tuple[float, float] | None:
    """[first fire - 5 s, last fire + seconds + 10 s] on the clip's CLOCK_MONOTONIC."""
    try:
        sync = json.loads((clip / "video_sync.json").read_text())
    except (OSError, ValueError):
        return None
    fires = sync.get("fire_host_mono") or {}
    fires = [v for v in (fires.values() if isinstance(fires, dict) else [fires]) if v]
    if not fires or not sync.get("seconds"):
        return None
    return min(fires) - WINDOW_BEFORE_S, max(fires) + float(sync["seconds"]) + WINDOW_AFTER_S


def kernel_lines(journal: list[tuple[float, str]], win: tuple[float, float]) -> dict:
    inside = [(t, m) for t, m in journal if win[0] <= t <= win[1] and TOPIC_RE.search(m)]
    errors = [(t, m) for t, m in inside if FAIL_RE.search(m)]
    return {"cma_dma_lines": [f"[{t:.6f}] {m}" for t, m in inside],
            "alloc_errors": [f"[{t:.6f}] {m}" for t, m in errors]}


def capture_misses(rows: list[dict]) -> list[str]:
    """Rev 15's capture bar for one clip, every camera (setting.json rows)."""
    why, cams = [], {r.get("camera"): r for r in rows}
    for cam in ("imx708", "openmv_n6", "openmv_ae3"):
        r = cams.get(cam)
        if r is None:
            why.append(f"{cam}:no_row")
            continue
        if not r.get("ok", False) or r.get("error"):
            why.append(f"{cam}:not_ok")
        fps = r.get("fps_clip")
        if cam == "imx708":
            if fps is None or fps < IMX_FPS_MIN:
                why.append(f"imx708:fps_clip {fps}")
            if r.get("max_gap_ms") is None or r["max_gap_ms"] > IMX_GAP_MAX_MS:
                why.append(f"imx708:max_gap {r.get('max_gap_ms')}")
        else:
            if r.get("frames_lost") is None or r["frames_lost"] != 0:
                why.append(f"{cam}:lost {r.get('frames_lost')}")
            if fps is None or fps < BOARD_FPS_MIN:
                why.append(f"{cam}:fps_clip {fps}")
    return why


def check_label(label_dir: Path, journal: list[tuple[float, str]], now_mono: float,
                debugfs: bool) -> dict:
    setting = json.loads((label_dir / "setting.json").read_text())
    clips = setting.get("clips") or []
    boot_start = min((t for t, _ in journal), default=None)
    res = {"label": setting.get("label", label_dir.name), "build": setting.get("build"),
           "sync_args": setting.get("sync_args"),
           "driver_held_cma": "unknown: /sys/kernel/debug/cma not readable (CONFIG_CMA_DEBUGFS "
                              "not set); CMA bar judged on kernel-log errors only (rev 15)"
           if not debugfs else "not sampled during the clips (debugfs present; card to say)",
           "clips": [], "unknown": []}
    for c in clips:
        cdir = label_dir / c["clip"]
        win = clip_window(cdir)
        rec = {"clip": c["clip"], "void": c.get("void", False), "bench_misses": c.get("misses")}
        if win is None:
            res["unknown"].append(f"{c['clip']}: no video_sync.json window")
            res["clips"].append(rec)
            continue
        this_boot = boot_start is not None and boot_start <= win[0] and now_mono >= win[1]
        rec.update(window_mono=[round(win[0], 3), round(win[1], 3)], this_boot=this_boot,
                   **kernel_lines(journal, win))
        if not this_boot:
            res["unknown"].append(f"{c['clip']}: window is not this boot's (rebooted since?)")
        rows = c.get("rows") or []
        rec["capture_misses"] = capture_misses(rows)
        mem = [r.get("host_min_mem_avail_mb") for r in rows if r.get("host_min_mem_avail_mb")]
        rec["min_mem_avail_mb"] = min(mem) if mem else None
        rec["mem_flag"] = bool(mem) and min(mem) < MEM_FLAG_MB
        cma = [r.get("host_min_cma_free_mb") for r in rows
               if r.get("host_min_cma_free_mb") is not None]
        rec["min_cma_free_mb"] = min(cma) if cma else None
        res["clips"].append(rec)
    counted = [r for r in res["clips"] if not r.get("void")]
    if len(counted) < N_CLIPS:
        res["unknown"].append(f"{len(counted)} non-void clips < {N_CLIPS}")
    res["cma_bar"] = all(not r.get("alloc_errors") for r in counted) and bool(counted)
    res["capture_bar"] = bool(counted) and all(
        r.get("this_boot") and not r.get("capture_misses") for r in counted)
    res["verdict"] = ("unknown" if res["unknown"] else
                      "pass" if res["cma_bar"] and res["capture_bar"] else
                      "capture_fails" if not res["capture_bar"] else "cma_fails")
    return res


def read_journal(path: str | None) -> str:
    if path:
        return Path(path).read_text(errors="replace")
    return subprocess.run(["journalctl", "-k", "-b", "-o", "short-monotonic", "--no-pager"],
                          capture_output=True, text=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("label")
    ap.add_argument("--bench-root", type=Path, default=ROOT / "results" / "pool2_bench")
    ap.add_argument("--journal", default=None)
    a = ap.parse_args(argv)
    label_dir = a.bench_root / a.label
    if not (label_dir / "setting.json").exists():
        print(f"no {label_dir / 'setting.json'}: run pool2_bench.py {a.label} first")
        return EXIT_USAGE
    journal = parse_journal(read_journal(a.journal))
    res = check_label(label_dir, journal, time.monotonic(), CMA_DEBUGFS.exists())
    (label_dir / "md19.json").write_text(json.dumps(res, indent=1))
    for r in res["clips"]:
        print(f"{r['clip']}: alloc errors {len(r.get('alloc_errors') or [])}, cma/dma lines "
              f"{len(r.get('cma_dma_lines') or [])}, capture "
              f"{'ok' if not r.get('capture_misses') else r['capture_misses']}, mem_avail min "
              f"{r.get('min_mem_avail_mb')} MB{' (< 150 flag)' if r.get('mem_flag') else ''}, "
              f"CmaFree min {r.get('min_cma_free_mb')} MB")
    print(f"{res['label']}: driver-held CMA {res['driver_held_cma'].split(':')[0]}; "
          f"CMA bar {'PASS' if res['cma_bar'] else 'FAIL'}, capture bar "
          f"{'PASS' if res['capture_bar'] else 'FAIL'} -> {res['verdict'].upper()}"
          + (f" ({'; '.join(res['unknown'])})" if res["unknown"] else ""))
    return {"pass": EXIT_OK, "capture_fails": EXIT_CAPTURE, "cma_fails": EXIT_CMA,
            "unknown": EXIT_UNKNOWN}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
