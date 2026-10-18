#!/usr/bin/env python3
"""pool2_bench.py — run one pool 2 bench setting: n synced clips, the test card's bars, fail fast.

Purpose: pool test 2 prep part B (docs/pool2_20261021/TEST_CARD_B.md §4–§5). One command per
  setting, so every setting runs and is judged the same way:
  - before each clip:
    - ~/.no_dive_autostart present; no gate (~/.gate_armed.json) or pool mode (~/.pool_armed)
      armed;
    - the process check ran and found no recorder (ci-record, rpicam-*, sync_video.py, ...);
    - SD free >= 2 GB;
    - UPS VBAT >= 3.4 V unless on the charger;
  - one clip = scripts/sync_video.py with the setting's per-board args;
  - the clip's rows come from scripts/video_clip_table.py, scored against the card's bars. The
    fps bars use fps_clip = frames captured in the clip's 30 s / 30 (QE 005 R2), so a camera
    that stops early fails;
  - a throttled clip is VOID (a current throttle bit, or a sticky bit new during the clip): cool
    down 5 min and re-run it; 2 throttled in a row -> stop. No throttle reading = a miss;
  - sync (d55, rev 9): N6 <-> AE3 start skew <= 25 ms is a bar; the IMX708's first frame <= 34 ms
    after the trigger is reported as its own verdict line, not part of "the setting holds";
  - one miss fails the setting, so it stops at its first clip that misses a bar.
  - the verdict is over EVERY clip in the label's folder (QE 005 R4): setting.json is rewritten
    after each clip, clip folders it doesn't list (a crash mid-clip) are scored from their
    files, and a label whose clips already decide the setting gets no new clips.

Usage (on the Pi, repo root):
  python scripts/pool2_bench.py <label> --under openmv_n6 [--n 3] [--ae3-floor 9.1] \\
      -- --n6-framesize HD --timing
  python scripts/pool2_bench.py n6_960_probe --under openmv_n6 --probe 960x600 \\
      -- --n6-framesize 960x600                     # N9: one clip, never a verdict
  --under: the board this setting changes (its fps bar applies); the other board stays at its
  control and is recorded (a drop is flagged "cross_effect", not a bar). Everything after `--`
  goes to sync_video.py unchanged.
  --probe WxH: one clip, not scored as a setting (N9). PROBE PASS = the board under test's
  frames are all whole JPEGs at WxH and its stream ran the whole clip (last frame >= clip
  seconds - 0.5 s).
Outputs: results/pool2_bench/<label>/clipNN/ (sync_video files) + setting.json (rows, misses,
  verdict).
Exit: 0 = the setting HOLDS (probe: PROBE PASS), 1 = it FAILS (probe: PROBE FAIL), 2 = usage
  error, 3 = a pre-check refused or the label's folder can't be resumed, 4 = stopped after 2
  throttled clips in a row (card §8 stop rule 3: stop the bench).
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
ARM_FILES = (Path.home() / ".gate_armed.json", Path.home() / ".pool_armed")
SD_MIN_FREE_B = 2_000_000_000
VBAT_MIN_MV = 3400
COOL_DOWN_S = 300
# The card's bars (TEST_CARD_B.md §5 = spec pass/fail).
FPS_MIN = 9.5
SKEW_MAX_MS = 25.0      # d55 (a), Nick 10/17 3:32 PM: N6 <-> AE3 start skew per clip
IMX_START_MAX_MS = 34.0  # d55 (b) / rev 9: IMX708 first frame - fire <= 34 ms, its own verdict line
IMX_FPS_MIN = 26.0
IMX_GAP_MAX_MS = 100.0
AE3_REL_BELOW = 0.2
USABLE_MIN = 0.95   # spec rev 7 (d57): an N6 clip is usable with >= 95 % of its expected frames
N6_USABLE_OF_3 = 2  # an N6 setting holds when >= 2 of 3 clips are usable + every other bar holds
PROBE_TAIL_S = 0.5  # N9 probe: the stream ran the clip when its last frame is this close to the end
EXIT_HOLDS, EXIT_FAILS, EXIT_USAGE, EXIT_REFUSED, EXIT_THROTTLED = 0, 1, 2, 3, 4
# setting.json must match these to resume a label (QE 005 R4)
SAME_ARGS = ("under", "n", "seconds", "ae3_floor", "sync_args", "probe")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv: list[str]) -> argparse.Namespace:
    """This script's options come before `--`, sync_video's after it (QE 005 R1: an
    argparse.REMAINDER positional swallowed --under). Without `--` there are no sync args."""
    i = argv.index("--") if "--" in argv else len(argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("label")
    ap.add_argument("--under", required=True, choices=("openmv_n6", "openmv_ae3"))
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--ae3-floor", type=float, default=None,
                    help="AE3 relative bar: its own ctl 640x400 rate - 0.2 (card §5), if B0 failed")
    ap.add_argument("--probe", default=None, metavar="WxH",
                    help="N9 probe: one clip, PROBE PASS/FAIL on WxH frames for the whole clip")
    ap.add_argument("--no-fail-fast", action="store_true", help="keep going after a miss")
    a = ap.parse_args(argv[:i])
    a.sync_args = list(argv[i + 1:])
    if a.probe:
        a.n, a.no_fail_fast = 1, True
    return a


def clip_misses(rows: list[dict], under: str, ae3_floor: float | None = None,
                seconds: float = 30.0) -> dict:
    """The card's bars for one clip. Pure: ``rows`` are video_clip_table.clip_rows() output.
    Returns misses (bar names), void (throttled), cross-effect flags for the board not under
    test, and the N6's ``usable`` (spec rev 7, d57): frames >= 95 % of expected, expected =
    asked fps x clip seconds (10 x 30 = 300; not the seq span, which a dying stream would
    shorten). N6 under test (QE R3): the fps, skew, IMX708 and host bars apply to its usable
    clips; an unusable clip counts only in the usable share, so its misses are kept in
    ``ignored``. The AE3 keeps 0 frames lost. Every fps bar is on ``fps_clip`` (QE 005 R2)."""
    by = {r["camera"]: r for r in rows}
    misses, cross, wedges = [], [], []
    imx = by.get("imx708") or {}
    if (imx.get("fps_clip") or 0) < IMX_FPS_MIN:
        misses.append("imx708_fps")
    if imx.get("max_gap_ms") is None or imx["max_gap_ms"] > IMX_GAP_MAX_MS:
        misses.append("imx708_max_gap")
    if not imx.get("ok") or imx.get("rc") != 0:  # QE 005 R2: rpicam-vid failed or exited non-zero
        misses.append("imx708_not_ok")
    n6 = by.get("openmv_n6") or {}
    expected = float(n6.get("fps_asked") or 10) * seconds
    usable = bool(n6) and (n6.get("frames") or 0) >= USABLE_MIN * expected \
        and not str(n6.get("error") or "").startswith(("arm:", "fire:"))
    for cam in ("openmv_n6", "openmv_ae3"):
        r = by.get(cam) or {}
        floor = ae3_floor if (cam == "openmv_ae3" and ae3_floor) else FPS_MIN
        err = str(r.get("error") or "")
        lost = r.get("frames_lost")
        bad = []
        if (r.get("fps_clip") or 0) < floor:
            bad.append(f"{cam}_fps")
        if cam == "openmv_ae3" and (lost != 0 or err):  # errored / unknown count = lost
            bad.append(f"{cam}_lost")
        if cam == under:
            misses += bad
        else:
            cross += bad
        if err.startswith("arm:"):  # spec F2 / rev 8: a wedge on either board fails the setting
            wedges.append(f"{cam}_wedge")
    # d55: the sync bar is N6 <-> AE3; the IMX708 gets its own start bar.
    offs = [(by.get(c) or {}).get("start_offset_ms") for c in ("openmv_n6", "openmv_ae3")]
    board_skew = abs(offs[0] - offs[1]) if None not in offs else None
    if board_skew is None or board_skew > SKEW_MAX_MS:
        misses.append("board_skew")
    imx_d = imx.get("first_frame_delay_ms")
    imx_start_ok = imx_d is not None and imx_d <= IMX_START_MAX_MS  # reported, not a setting bar
    # Throttle (card §5 host bar): void on a current bit or a sticky bit new during the clip;
    # no reading means the bar can't be shown to hold, so it is a miss, not a pass.
    voids = [r.get("host_throttle_void") for r in rows]
    throttled = any(v is True for v in voids)
    if not rows or any(v is None for v in voids):
        misses.append("host_no_throttle_reading")
    ignored = []
    if under == "openmv_n6" and not usable:
        ignored, misses = misses, []
    misses += wedges
    return {"misses": misses, "ignored": ignored, "void": throttled, "cross_effect": cross,
            "board_skew_ms": board_skew, "imx_start_ok": imx_start_ok,
            "imx_first_frame_ms": imx_d, "imx_period_ms": round(1000 / imx["fps"], 1)
            if imx.get("fps") else None, "n6_usable": usable,
            "n6_frames": n6.get("frames"), "n6_expected": expected,
            "n6_lost": n6.get("frames_lost")}


def probe_result(rows: list[dict], under: str, dims: str, seconds: float = 30.0) -> dict:
    """N9 probe verdict (not a setting verdict): the board under test's frames are all whole
    JPEGs at ``dims`` and its stream ran the whole clip. Pure."""
    r = next((x for x in rows if x.get("camera") == under), {})
    why = []
    if r.get("resolution_measured") != dims:
        why.append(f"dims:{r.get('resolution_measured')}")
    if r.get("bad_frames") != 0:
        why.append(f"bad_frames:{r.get('bad_frames')}")
    if (r.get("last_frame_s") or 0) < seconds - PROBE_TAIL_S:
        why.append(f"ended_at_s:{r.get('last_frame_s')}")
    if r.get("error"):
        why.append(f"error:{str(r['error'])[:80]}")
    return {"probe_ok": not why, "probe_why": why}


def setting_holds(clips: list[dict], under: str, n: int = 3) -> bool:
    """A setting holds on its first n scored clips: no misses on any of them (for the N6 under
    test an unusable clip carries none, QE R3) and, for the N6, >= 2 of 3 usable (spec rev 7;
    scaled for other n). Pure."""
    first = [c for c in clips if not c["void"]][:n]
    if len(first) < n or any(c["misses"] for c in first):
        return False
    if under == "openmv_n6":
        return sum(bool(c.get("n6_usable")) for c in first) >= -(-N6_USABLE_OF_3 * n // 3)
    return True


def n6_hopeless(clips: list[dict], n: int = 3) -> bool:
    """Fail fast for the N6 rule: too many unusable clips to reach the usable share."""
    scored = [c for c in clips if not c["void"]][:n]
    return sum(not c.get("n6_usable") for c in scored) > n - (-(-N6_USABLE_OF_3 * n // 3))


def verdict(clips: list[dict], under: str, n: int) -> str | None:
    """'holds' | 'fails' | None (undecided: needs more clips), over every clip so far. One miss
    fails the setting (card §4), whichever run of the label scored it (QE 005 R4). Pure."""
    first = [c for c in clips if not c["void"]][:n]
    if any(c["misses"] for c in first) or (under == "openmv_n6" and n6_hopeless(clips, n)):
        return "fails"
    if len(first) >= n:
        return "holds" if setting_holds(clips, under, n) else "fails"
    return None


def score_clip(clip: Path, under: str, ae3_floor: float | None, seconds: float) -> dict:
    """One clip folder -> its record. Never raises: a clip the table can't read is a miss."""
    try:
        rows = _load("video_clip_table").clip_rows(clip) \
            if (clip / "video_sync.json").exists() else []
    except Exception as exc:  # noqa: BLE001 - a broken clip is a miss with its reason
        return {"clip": clip.name, "misses": [f"clip_table_error:{exc!r}"[:160]], "void": False,
                "cross_effect": [], "n6_usable": False, "rows": []}
    if not rows:
        return {"clip": clip.name, "misses": ["no_video_sync_json"], "void": False,
                "cross_effect": [], "n6_usable": False, "rows": []}
    return {"clip": clip.name, **clip_misses(rows, under, ae3_floor, seconds), "rows": rows}


def _clip_no(name: str) -> int:
    return int(name[4:]) if name[4:].isdigit() else 0


def load_setting(out: Path, a: argparse.Namespace) -> tuple[dict, str | None]:
    """The label's record, rebuilt over every clip folder (QE 005 R4). Returns (record,
    refusal). A setting.json written with other arguments can't be resumed."""
    path = out / "setting.json"
    new = {"label": a.label, **{k: getattr(a, k) for k in SAME_ARGS}, "clips": [],
           "runs": []}
    rec = json.loads(path.read_text()) if path.exists() else new
    if path.exists():
        diff = {k: (rec.get(k), getattr(a, k)) for k in SAME_ARGS if rec.get(k) != getattr(a, k)}
        if diff:
            return rec, f"{path} was written with other arguments {diff}: use a new label"
    known = {c["clip"]: c for c in rec.get("clips", [])}
    clips = []
    for d in sorted(out.glob("clip[0-9]*"), key=lambda p: _clip_no(p.name)):
        clips.append(known.get(d.name) or {**score_clip(d, a.under, a.ae3_floor, a.seconds),
                                           "orphan": True})
    rec["clips"] = clips
    return rec, None


def write_setting(out: Path, rec: dict, a: argparse.Namespace) -> None:
    """setting.json after every clip (a crash keeps the record so far), with the verdict."""
    scored = [c for c in rec["clips"] if not c["void"]]
    if a.probe:
        last = scored[-1] if scored else {}
        rec.update(probe_result(last.get("rows") or [], a.under, a.probe, a.seconds)
                   if last else {"probe_ok": False, "probe_why": ["no_clip"]})
        rec["holds"] = None  # N9: a probe never decides a setting
    else:
        rec["verdict"] = verdict(rec["clips"], a.under, a.n)
        rec["holds"] = rec["verdict"] == "holds"
    rec["n6_usable_share"] = (f"{sum(bool(c.get('n6_usable')) for c in scored[:a.n])}"
                              f"/{len(scored[:a.n])}")
    # rev 9: the IMX708 start check is its own verdict line, not part of "the setting holds"
    rec["imx_start_ok"] = f"{sum(bool(c.get('imx_start_ok')) for c in scored)}/{len(scored)}"
    tmp = out / "setting.json.tmp"
    tmp.write_text(json.dumps(rec, indent=1, default=str) + "\n")
    tmp.replace(out / "setting.json")


def prechecks() -> list[str]:
    """Card §4 step 0 / §8, before every clip."""
    gate = _load("gate_cycle")
    refuse = []
    if not DIVE_FLAG.exists():
        refuse.append("~/.no_dive_autostart missing (dive autostart would grab the cameras)")
    for f in ARM_FILES:
        if f.exists():
            refuse.append(f"{f} present (a gate or pool mode is armed)")
    procs = gate.recorder_procs()
    if procs is None:  # QE 005 R7: ps failed, so nothing was checked
        refuse.append("process check failed (ps gave no output)")
    elif procs:
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


def _summary(rec: dict, a: argparse.Namespace) -> str:
    scored = [c for c in rec["clips"] if not c["void"]]
    if a.probe:
        head = f"PROBE {'PASS' if rec.get('probe_ok') else 'FAIL'} {rec.get('probe_why')}"
    else:
        head = {"holds": "HOLDS", "fails": "FAILS"}.get(rec.get("verdict"), "UNDECIDED")
    return (f"pool2_bench {a.label}: {head} · {len(scored)} scored clip(s) · "
            f"N6 usable {rec['n6_usable_share']} · IMX708 start <= 34 ms {rec['imx_start_ok']}"
            f" · {rec.get('stopped', '')}")


def main(argv: list[str] | None = None) -> int:
    a = parse_args(sys.argv[1:] if argv is None else argv)
    # Before reading the label's folder: a recorder still running (e.g. a second bench run)
    # would be writing a clip folder that would then be scored as a crashed clip.
    refuse = prechecks()
    if refuse:
        print(f"pool2_bench {a.label}: REFUSED {refuse}", flush=True)
        return EXIT_REFUSED
    out = OUT_ROOT / a.label
    out.mkdir(parents=True, exist_ok=True)
    rec, refuse = load_setting(out, a)
    if refuse:
        print(f"pool2_bench {a.label}: REFUSED {refuse}", flush=True)
        return EXIT_REFUSED
    run = {"started_utc": datetime.now(timezone.utc).isoformat(), "clips": []}
    rec.setdefault("runs", []).append(run)
    rec.pop("stopped", None)
    decided = None if a.probe else verdict(rec["clips"], a.under, a.n)
    if decided or (a.probe and rec["clips"]):
        write_setting(out, rec, a)
        print(f"pool2_bench {a.label}: already decided by its {len(rec['clips'])} clip(s); "
              "no new clip", flush=True)
        print(_summary(rec, a), flush=True)
        return EXIT_HOLDS if (rec.get("probe_ok") if a.probe else rec["holds"]) else EXIT_FAILS
    voids_in_row = 0  # per run: a resumed run starts after the operator's cool-down
    code = None
    while True:
        scored_n = len([c for c in rec["clips"] if not c["void"]])
        if scored_n >= a.n:
            break
        refuse = prechecks()
        if refuse:
            rec["stopped"] = f"precheck: {refuse}"
            print(f"pool2_bench {a.label}: REFUSED {refuse}", flush=True)
            code = EXIT_REFUSED
            break
        k = max([_clip_no(c["clip"]) for c in rec["clips"]] + [0]) + 1
        clip = out / f"clip{k:02d}"
        cmd = [sys.executable, str(ROOT / "scripts" / "sync_video.py"), "--seconds",
               str(a.seconds), "--out", str(clip), *a.sync_args]
        print(f"pool2_bench {a.label}: clip {k}: {' '.join(cmd[2:])}", flush=True)
        try:
            subprocess.run(cmd, timeout=a.seconds + 180)
        except subprocess.TimeoutExpired:
            print(f"pool2_bench {a.label}: clip {k}: sync_video.py timed out", flush=True)
        res = score_clip(clip, a.under, a.ae3_floor, a.seconds)
        rec["clips"].append(res)
        run["clips"].append(res["clip"])
        write_setting(out, rec, a)
        print(f"pool2_bench {a.label}: clip {k}: misses {res['misses']} void {res['void']} "
              f"cross {res['cross_effect']} N6 usable {res.get('n6_usable')} "
              f"({res.get('n6_frames')}/{res.get('n6_expected')}, lost {res.get('n6_lost')})",
              flush=True)
        if res["void"]:
            voids_in_row += 1
            if voids_in_row >= 2:
                rec["stopped"] = "2 throttled clips in a row (card §8 stop rule 3)"
                code = EXIT_THROTTLED
                break
            time.sleep(COOL_DOWN_S)
            continue
        voids_in_row = 0
        if not a.no_fail_fast and verdict(rec["clips"], a.under, a.n) == "fails":
            break
    write_setting(out, rec, a)
    print(_summary(rec, a), flush=True)
    if code is not None:
        return code
    return EXIT_HOLDS if (rec.get("probe_ok") if a.probe else rec["holds"]) else EXIT_FAILS


if __name__ == "__main__":
    sys.exit(main())
