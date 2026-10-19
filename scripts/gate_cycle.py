#!/usr/bin/env python3
"""gate_cycle.py — nereus002 start/stop reliability gate: one verified 3-camera cycle per boot.

Purpose: Nick's bar (2026-10-14): the rig must START and STOP 25 times IN A ROW with all three
  cameras (IMX708, N6, AE3) waking up, each taking a still that verifies, and the rig shutting
  down cleanly; any failure resets the count to 0. Bars + hypotheses:
  docs/rig_gate_20261014/PREREG.md. This script runs ONE cycle per boot and, in auto mode,
  power-cycles the rig through the LiFePO4wered UPS (scripts/ups_power_cycle.sh) for the next.

One cycle (``boot``, run from a marked @reboot crontab line, or ``once`` by hand):
  1. boot facts: boot id, uptime, UPS VIN/VBAT/VOUT/IOUT, throttled, how the previous boot ended
     (journal: clean poweroff? requested by this gate's timer or by the switch/UPS button?);
  2. kernel enumeration time of each board (journal) and when its /dev/serial/by-id tty appears;
  3. first-contact probe of each board + the recovery ladder if it does not answer
     (scripts/board_probe.py — the AE3 cold-boot wedge diagnostics);
  4. one capture set through the production path (the same CLI call as pool_capture.py);
  5. verification of each still: sha256 recomputed == recorded, plausible size, decodes at the
     expected size (PIL); RAWs: sha256 + size;
  6. one CSV row + one JSON record in results/gate/<run>/, PASS/FAIL and the streak;
  7. auto mode: UPS power cycle (WAKE_TIME 1, PI_RUNNING 0) unless the target is reached, STOP
     exists, 3 FAILs in a row, or a guard refuses. switch mode: wait for Nick's switch.

Usage (on the Pi, repo root ~/nereus-rig-poolcam):
  python scripts/gate_cycle.py arm --target 25 --mode auto     # boot-start ON (cron + dive flag)
  python scripts/gate_cycle.py once                           # one cycle now, no power cycle
  python scripts/gate_cycle.py status                         # streak, last rows
  python scripts/gate_cycle.py stop                           # no further power cycles
  python scripts/gate_cycle.py disarm                         # cron + dive flag restored
  python scripts/gate_cycle.py mode switch|auto               # Nick's switch cycles vs UPS timer
  python scripts/gate_cycle.py seal [RUN_ID]                  # G-b verdict + run SHA256SUMS
  python scripts/gate_cycle.py sd RUN_ID --wed-mb MB          # after the clear: rev 4 item 5

Pool 2 ready gate (docs/pool2_20261021/SPEC.md rev 5+, TEST_CARD_B.md; Nick 10/17, d52):
  python scripts/gate_cycle.py arm --video-only --video-s 30 --until-streak 25 --target 60 \
      --cycle-mb <per-cycle MB from part B> --video-args="--n6-framesize HD ..."
  (--video-args needs the `=`: a value starting with "--" is otherwise read as an option.)
  One cycle = boot -> board first contact -> one synced 30 s clip on all 3 cameras -> 30 s ->
  checks -> clean shutdown. No stills. G-a (the 25-in-a-row line): every camera recorded, 0
  recorder processes 30 s after the clip, clean previous shutdown, boards power-cycled. Video
  completeness (lost frames, fps, skew) is scored per cycle and never gates (d52). G-b (SD clean,
  its own verdict line): df/du readings at gate start, after each boot and 30 s after each clip.
  RAM (mem_avail, CMA free vs this boot's idle CMA) is report-only. Auto mode stops power-cycling
  when a reading has SD free < 2 GB or none (F9, card §8 stop rule 4). Each cycle attempt records
  into video/cycleNNN_<boot id>/, so a hard reset mid-cycle never lets the retry overwrite it.
Outputs: results/gate/<run>/{cycles.csv, cycles.jsonl, gate.log, run_manifest.json, rig.yaml,
  profiles/, <date>/exp_*/ (one experiment per cycle, with SHA256SUMS), video/, gb_verdict.json
  + SHA256SUMS (seal)}; ~/.gate_sd_start_<run>.json (the gate-start SD reading, kept outside
  the run folder so `sd` works after the folder is cleared).

Assumptions: nereus002 (LiFePO4wered/Pi+, boards on the USB hub); no root (cron + the UPS CLI
  as pi). Never touches Nick's services: dive-autostart is stood down only through his own flag
  ~/.no_dive_autostart, exactly as pool_mode.sh does (pre-arm state recorded and restored).
Limits: the IMX708 has no board uptime, so "the camera lost power" is shown for the boards only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import board_probe  # noqa: E402
import pool_capture as pc  # noqa: E402  (reuse: run config, one set, UPS readers)

GATE_ROOT = ROOT / "results" / "gate"
STATE = Path.home() / ".gate_armed.json"
SD_START_FMT = ".gate_sd_start_{run}.json"  # in ~: survives the post-gate clear (QE 005 R5)
DIVE_FLAG = Path.home() / ".no_dive_autostart"
POOL_ARMED = Path.home() / ".pool_armed"
FLAG_MARK = "created by nereus-camera-test-rig scripts/gate_cycle.py"
CRON_MARK = "# GATE_MODE nereus-camera-test-rig"
BOARDS = {"openmv_ae3": "0829c14000000000", "openmv_n6": "10003500025043364d343000"}
CAMERAS = ("imx708", "openmv_n6", "openmv_ae3")
# Verification bars (PREREG §4): min bytes and decoded size per camera's still.
STILL_BARS = {"imx708": (300_000, (4608, 2592)), "openmv_n6": (30_000, (1280, 800)),
              "openmv_ae3": (30_000, (1280, 800))}
RAW_MIN_BYTES = {"imx708": 10_000_000, "openmv_n6": 1_000_000, "openmv_ae3": 1_000_000}
MAX_LAST_STILL_UPTIME_S = 180.0   # last verified still within this of kernel start
MAX_VIDEO_SKEW_MS = 100.0         # synchronized video: start skew bar (PREREG §5)
# PREREG A5 (darkness): a cycle is VOID, not FAIL, only if its ONLY failures are board stills
# that are intact (sha + decode OK) but under the size bar because the scene is dark: the
# board frame itself is near-black AND the IMX708 (auto exposure, most sensitive) sees a dim
# scene. A black board frame while the IMX708 sees a lit scene is still a FAIL.
DARK_BOARD_MEAN = 10.0            # grey 0-255: a near-black board frame
DARK_IMX_MEAN = 40.0              # grey 0-255: the IMX708 still of a dim scene
MAX_FAILS_IN_ROW = 3              # auto mode stops power-cycling after this many FAILs
                                  # (arm --stop-after-fails; 0 = never, for baseline runs)
BYID_TIMEOUT_S = 60.0
# Pool 2 ready gate (spec rev 5+, QE 005 N1-N7). Bytes are decimal (1 MB = 1e6 B).
POST_CLIP_WAIT_S = 30.0           # G-a process check + G-b / RAM readings this long after the clip
SD_BAR_CYCLE_B = 100_000_000      # G-b 2: outside use grows <= 100 MB per cycle (post-clip pairs)
SD_BAR_RUN_B = 250_000_000        # G-b 3: last reading <= 250 MB above the gate-start reading
SD_MIN_FREE_B = 2_000_000_000     # G-b 4: SD free >= 2 GB at every reading
RAM_FLAG_CMA_DROP_MB = 20         # report-only (rev 7): CmaFree > 20 MB below that boot's idle
RAM_FLAG_AVAIL_MB = 150           # reading (idle is ~42 MB: page cache borrows CMA); avail < 150 MB
# Recorder processes that must be gone 30 s after the clip (G-a), by the basename of the program
# a process runs (``program_name``): never any other argv token, so `vim sync_video.py` is not one.
RECORDER_EXACT = ("sync_video.py", "pool_capture.py", "nereus-rig", "nereus_camera_test_rig.cli",
                  "capture_dark.py", "hil_soak.py", "pool_selfcheck.py", "pool2_bench.py")
RECORDER_PREFIX = ("ci-record", "ci_record", "rpicam-", "libcamera-", "capture_raw_",
                   "selfcheck_")
# Launchers whose program is a later argv token: `python3 -u x.py`, `env A=1 x`, `timeout 60 x`.
LAUNCHER_RE = re.compile(r"(python[\d.]*|bash|sh|dash|env|nice|nohup|sudo|timeout|stdbuf|ionice)")
# Launcher options that take a value, per launcher: `python3 -X dev`, `timeout -s INT 60`,
# `sudo -u pi`, ... (python's -u takes none).
LAUNCHER_OPT_VALUE = {"python": ("-X", "-W"), "timeout": ("-s", "-k"), "sudo": ("-u", "-g", "-C"),
                      "nice": ("-n",), "ionice": ("-c", "-n"), "stdbuf": ("-i", "-o", "-e")}
SHELLS = ("bash", "sh", "dash")
CSV_FIELDS = [
    "cycle", "started_by", "utc", "boot_id", "uptime_start_s", "prev_end", "prev_by",
    "vin_mv", "vbat_mv", "on_battery", "throttled", "msc_quirk", "ae3_enum_s", "n6_enum_s",
    "ae3_tty_s", "n6_tty_s", "ae3_contact", "ae3_board_ticks_s", "ae3_import_ms",
    "n6_contact", "n6_board_ticks_s", "boards_power_cycled", "set_s",
    "imx_still", "n6_still", "ae3_still", "raws_ok", "still_skew_s", "sync_still_ms",
    "sync_raw_ms", "video", "video_skew_ms", "video_fps", "video_lost",
    "last_still_uptime_s", "result", "fail_reasons", "streak",
]
# pool 2 ready gate (video-only cycles, spec rev 5+); empty on still cycles
VIDEO_ONLY_FIELDS = [
    "video_only", "imx_video", "n6_video", "ae3_video", "video_complete", "first_frame_ms",
    "procs_left", "sd_free_boot_b", "sd_free_post_b", "sd_outside_post_b", "sd_cycle_growth_b",
    "sd_run_growth_b", "gb_ok", "gb_fails", "journal_b", "home_b", "var_log_b",
    "mem_avail_mb_post", "cma_free_mb_post", "cma_free_mb_idle", "ram_flag",
]
CSV_FIELDS += VIDEO_ONLY_FIELDS


# -- small helpers ---------------------------------------------------------------------------
def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uptime_s() -> float:
    return float(Path("/proc/uptime").read_text().split()[0])


def sh(cmd: list[str], timeout: float = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    pc.atomic_json(STATE, state)


class Log:
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, msg: str) -> None:
        line = f"[{now_utc()} up {uptime_s():7.1f}s] {msg}"
        print(line, flush=True)
        with self.path.open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())


# -- boot facts --------------------------------------------------------------------------------
def kernel_events() -> dict:
    """Kernel-time (s since kernel start) of each board's enumeration and tty bind, this boot."""
    out = sh(["journalctl", "-b", "-k", "-o", "json", "--no-pager"], timeout=30)
    ports: dict = {}
    res: dict = {}
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        t = int(e.get("_SOURCE_MONOTONIC_TIMESTAMP") or e.get("__MONOTONIC_TIMESTAMP") or 0) / 1e6
        m = re.search(r"usb (\S+): SerialNumber: (\S+)", msg)
        if m:
            for cam, sn in BOARDS.items():
                if m.group(2) == sn and cam not in res:
                    ports[m.group(1)] = cam
                    res[cam] = {"port": m.group(1), "enum_s": round(t, 2)}
        m = re.search(r"cdc_acm (\S+):1\.0: (ttyACM\d+)", msg)
        if m and m.group(1) in ports and "tty_bind_s" not in res[ports[m.group(1)]]:
            res[ports[m.group(1)]].update(tty_bind_s=round(t, 2), tty=m.group(2))
        if re.search(r"error -71|error -110|over-current|Under-voltage", msg):
            res.setdefault("usb_errors", []).append(f"{t:.1f} {msg[:120]}")
    return res


def previous_boot_end() -> dict:
    """How the previous boot ended: clean poweroff (and who asked) or not (crash / power cut)."""
    tail = sh(["journalctl", "-b", "-1", "-n", "1500", "-o", "short-monotonic", "--no-pager"],
              timeout=30)
    clean = bool(re.search(r"(systemd-shutdown|Reached target poweroff|System Power Off|"
                           r"Journal stopped)", tail, re.I))
    if "gate_cycle: requesting UPS power cycle" in tail or "ups_power_cycle" in tail:
        by = "gate_timer"
    elif re.search(r"lifepo4wered.*shut ?down|Signal from LiFePO4wered", tail, re.I):
        by = "ups_button_or_switch"
    elif clean:
        by = "other_clean"
    else:
        by = "unknown"
    return {"clean": clean, "by": by}


def wait_ttys(timeout_s: float) -> dict:
    """Uptime at which each board's by-id tty first exists (None = not within the timeout)."""
    seen: dict = {cam: None for cam in BOARDS}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and None in seen.values():
        for cam, sn in BOARDS.items():
            if seen[cam] is None and board_probe.by_id_port(sn):
                seen[cam] = round(uptime_s(), 1)
        time.sleep(0.5)
    return seen


# -- verification ------------------------------------------------------------------------------
def verify_still(cam: str, path: Path, recorded_sha: str | None) -> dict:
    from PIL import Image

    min_bytes, size = STILL_BARS[cam]
    rec: dict = {"file": path.name}
    if not path.is_file():
        return {**rec, "ok": False, "why": "missing"}
    rec["bytes"] = path.stat().st_size
    rec["sha_ok"] = bool(recorded_sha) and sha256(path) == recorded_sha
    rec["size_ok"] = rec["bytes"] >= min_bytes
    try:
        with Image.open(path) as im:
            im.load()  # full decode, not just the header
            rec["dims"] = list(im.size)
            # mean grey level 0-255 of a thumbnail: a black frame (dark bench, dead sensor) is
            # visible in the CSV instead of hiding behind a "decoded OK"
            thumb = im.convert("L").resize((64, 40))
            rec["mean"] = round(sum(thumb.histogram()[i] * i for i in range(256)) / (64 * 40), 1)
        rec["decode_ok"] = tuple(rec["dims"]) == size
    except Exception as exc:  # noqa: BLE001 - any decode failure is a FAIL with its reason
        rec.update(decode_ok=False, why=f"decode: {exc}")
    rec["mtime"] = path.stat().st_mtime
    rec["ok"] = rec["sha_ok"] and rec["size_ok"] and rec["decode_ok"]
    if not rec["ok"] and "why" not in rec:
        rec["why"] = ",".join(k for k in ("sha_ok", "size_ok", "decode_ok") if not rec[k])
    return rec


def verify_set(exp_dir: Path) -> dict:
    """Per camera: the still verified (sha, size, decode) and the RAW (sha, size)."""
    rec = json.loads((exp_dir / "experiment.json").read_text())
    out: dict = {cam: {"still": {"ok": False, "why": "no record"}} for cam in CAMERAS}
    for key, kind in (("captures", "still"), ("raw_captures", "raw")):
        for c in rec.get(key, []):
            camd = c.get("camera") or {}
            cam = {"imx708": "imx708", "n6": "openmv_n6", "ae3": "openmv_ae3"}.get(
                camd.get("board") or camd.get("sensor") or "", "?")
            if cam not in out:
                continue
            if c.get("status") != "completed" or not c.get("output_path"):
                out[cam][kind] = {"ok": False, "why": (c.get("error") or {}).get("code")
                                  or c.get("status"),
                                  "seconds": round(c.get("duration_seconds") or 0, 1)}
                continue
            # experiment.json holds absolute Pi paths; fall back to the set's own folder
            path = Path(c["output_path"])
            if not path.is_file():
                path = exp_dir / "captures" / path.parent.name / path.name
            if kind == "still":
                v = verify_still(cam, path, c.get("sha256"))
            else:
                ok = path.is_file() and sha256(path) == c.get("sha256") \
                    and path.stat().st_size >= RAW_MIN_BYTES[cam]
                v = {"ok": ok, "file": path.name, "bytes": path.stat().st_size
                     if path.is_file() else None}
            v["seconds"] = round(c.get("duration_seconds") or 0, 1)
            out[cam][kind] = v
    return out


# -- one cycle ---------------------------------------------------------------------------------
def board_contact(cam: str, log: Log) -> dict:
    rec = board_probe.recover(BOARDS[cam])
    log(f"{cam}: first contact {board_probe.summary(rec)}")
    return rec


def contact_cols(rec: dict) -> tuple[str, float | None, int | None]:
    """(CSV text, board uptime s at contact, import ms) from a probe/ladder record."""
    first = rec.get("first") or {}
    info = first.get("info") or {}
    if not info:
        for step in rec.get("steps", []):
            info = (step.get("retry") or {}).get("info") or info
    if info and "pi_uptime_s" not in info:
        info = {**info, "pi_uptime_s": first.get("pi_uptime_s")}
    ticks = info.get("ticks_ms")
    boot = info.get("boot_ms") or {}
    imp = (boot.get("imported") - boot.get("main")) if {"imported", "main"} <= set(boot) else None
    text = "ok" if first.get("ok") else (
        f"{first.get('stage')}->{rec.get('recovered_by', 'NOT_RECOVERED')}")
    return text, (round(ticks / 1000, 1) if isinstance(ticks, int) else None), imp


# -- pool 2 ready gate: SD, process and RAM readings ------------------------------------------
def _du(path: Path, exclude: Path | None = None) -> int | None:
    """Allocated bytes under ``path`` (``du -s -B1``, QE N3); unreadable files are skipped."""
    cmd = ["du", "-s", "-B1"] + ([f"--exclude={exclude}"] if exclude else []) + [str(path)]
    out = sh(cmd, timeout=120).split()
    return int(out[0]) if out and out[0].isdigit() else None


def _journal_bytes() -> int | None:
    m = re.search(r"take up ([\d.]+)([KMGT]?)", sh(["journalctl", "--disk-usage"]))
    if not m:
        return None
    return int(float(m.group(1)) * 1024 ** " KMGT".index(m.group(2) or " "))


def sd_reading(run_dir: Path) -> dict:
    """One G-b reading: ``df -B1`` of the filesystem holding results/, the run folder's own size,
    and 'outside use' = used - run folder (the gate's clips are evidence, not leaks). Plus where
    outside use could grow (QE N1): the journal, /var/log, apt, and ~ without results/."""
    r: dict = {"uptime_s": round(uptime_s(), 1)}
    on = run_dir if run_dir.exists() else GATE_ROOT if GATE_ROOT.exists() else ROOT
    df = sh(["df", "-B1", "--output=used,avail", str(on)]).split()
    if len(df) >= 2 and df[-1].isdigit() and df[-2].isdigit():
        r["used_b"], r["free_b"] = int(df[-2]), int(df[-1])
    r["run_b"] = _du(run_dir) if run_dir.exists() else 0  # cleared after the pull: nothing left
    if "used_b" in r and r["run_b"] is not None:
        r["outside_b"] = r["used_b"] - r["run_b"]
    r["journal_b"] = _journal_bytes()
    for key, path in (("var_log_b", "/var/log"), ("var_lib_apt_b", "/var/lib/apt"),
                      ("var_cache_apt_b", "/var/cache/apt")):
        r[key] = _du(Path(path))
    r["home_b"] = _du(Path.home(), exclude=GATE_ROOT.parent)
    return r


def _ancestors(pid: int) -> set[int]:
    out = set()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return out


def program_name(args: str) -> str:
    """Basename of the program a process runs: argv[0], or after a launcher (python, bash, env,
    timeout, ...) the first token that isn't an option, VAR=value or a number; `python -m mod`
    gives mod. Pure."""
    toks = args.split()
    i = 0
    while i < len(toks):
        name = toks[i].rsplit("/", 1)[-1]
        if not LAUNCHER_RE.fullmatch(name):
            return name
        kind = "python" if name.startswith("python") else name
        takes_value = LAUNCHER_OPT_VALUE.get(kind, ())
        i += 1
        while i < len(toks) and (toks[i].startswith("-") or "=" in toks[i]
                                 or re.fullmatch(r"[\d.]+[smhd]?", toks[i])):
            if kind == "python" and toks[i] == "-m" and i + 1 < len(toks):
                return toks[i + 1]
            if kind == "python" and toks[i] == "-c":  # python -c CODE runs no program file
                return "-c"
            if kind in SHELLS and toks[i] == "-c":    # bash -c CMD runs CMD
                return toks[i + 1].rsplit("/", 1)[-1] if i + 1 < len(toks) else "-c"
            i += 2 if toks[i] in takes_value else 1
    return ""


def is_recorder(args: str) -> bool:
    """A recorder process, judged by the program it runs (QE 005 nit: not any argv token)."""
    name = program_name(args)
    return name in RECORDER_EXACT or name.startswith(RECORDER_PREFIX)


def recorder_procs() -> list[str] | None:
    """Recorder processes still running (G-a), excluding this gate and its ancestors (QE N5).
    None when ps gave no output (it failed or timed out): nothing was checked (QE 005 R7). A
    working ps always lists at least this process."""
    mine = _ancestors(os.getpid())
    out = sh(["ps", "-eo", "pid=,args="])
    if not out.strip():
        return None
    left = []
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and int(pid) not in mine and is_recorder(args):
            left.append(f"{pid} {args.strip()[:160]}")
    return left


def ram_reading(idle: dict | None = None) -> dict:
    """Report-only (spec rev 4, rev 7): MemAvailable and CmaFree, MB. With ``idle`` (this boot's
    reading before recording), flagged when CmaFree is > 20 MB below it or MemAvailable < 150 MB."""
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if k in ("MemAvailable", "CmaFree"):
            info[k] = int(v.split()[0]) // 1024
    out = {"mem_avail_mb": info.get("MemAvailable"), "cma_free_mb": info.get("CmaFree")}
    if idle is not None:
        out["flag"] = ram_flag(out, idle)
    return out


def ram_flag(post: dict, idle: dict) -> bool:
    """Pure: the rev 7 report-only RAM flag."""
    cma, cma0 = post.get("cma_free_mb"), idle.get("cma_free_mb")
    avail = post.get("mem_avail_mb")
    return bool((cma is not None and cma0 is not None and cma < cma0 - RAM_FLAG_CMA_DROP_MB)
                or (avail is not None and avail < RAM_FLAG_AVAIL_MB))


def evaluate_sd(rec: dict, state: dict) -> dict:
    """G-b per cycle. Pure. Cycle growth = outside use post-clip(k) - post-clip(k-1), cycle 1 from
    the gate-start reading (QE N2); SD free >= 2 GB at both of the cycle's readings. The run-level
    250 MB bar is judged on the last reading (``sd_verdict``); here it is only recorded."""
    start = state.get("sd_start") or {}
    prev = state.get("sd_prev_post") or start
    boot, post = rec.get("sd_boot") or {}, rec.get("sd_post") or {}
    fails = []
    for name, r in (("boot", boot), ("post", post)):
        if r.get("free_b") is None or r.get("outside_b") is None:
            fails.append(f"sd_{name}:no_reading")
        elif r["free_b"] < SD_MIN_FREE_B:
            fails.append(f"sd_{name}_free:{r['free_b']}")
    growth = run_growth = None
    if post.get("outside_b") is not None and prev.get("outside_b") is not None:
        growth = post["outside_b"] - prev["outside_b"]
        if growth > SD_BAR_CYCLE_B:
            fails.append(f"sd_cycle_growth:{growth}")
    if post.get("outside_b") is not None and start.get("outside_b") is not None:
        run_growth = post["outside_b"] - start["outside_b"]
    return {"gb_ok": not fails, "gb_fails": fails, "sd_cycle_growth_b": growth,
            "sd_run_growth_b": run_growth}


def sd_verdict(rows: list[dict], start: dict) -> dict:
    """G-b over the whole gate (bars 2-4): every cycle's G-b passed and the last reading's outside
    use is <= 250 MB above the gate start. Pure; rows are cycles.jsonl records in order."""
    fails = [f"cycle {r.get('cycle')}: {';'.join(r.get('gb_fails') or [])}" for r in rows
             if r.get("video_only") and not r.get("gb_ok")]
    if start.get("free_b") is None or start["free_b"] < SD_MIN_FREE_B:
        fails.append(f"start_free:{start.get('free_b')}")
    last = next((r["sd_run_growth_b"] for r in reversed(rows)
                 if r.get("sd_run_growth_b") is not None), None)
    if last is None:
        fails.append("no_post_clip_reading")
    elif last > SD_BAR_RUN_B:
        fails.append(f"run_growth:{last}")
    return {"ok": not fails, "fails": fails, "last_run_growth_b": last}


def sd_stop(rec: dict) -> bool:
    """F9 / card §8 stop rule 4 (QE 005 R6): a cycle reading with SD free < 2 GB, or none, stops
    the power cycling; clips stay on the Pi until the pull (N6), so they would keep filling it.
    Pure."""
    return any(f.startswith(("sd_boot_free", "sd_post_free", "sd_boot:no_reading",
                             "sd_post:no_reading")) for f in rec.get("gb_fails") or [])


def sd_clean(start: dict, now: dict, wed_mb: float | None) -> dict:
    """Rev 4 item 5, after the run folder is pulled, verified and cleared (card §6 step 6): SD
    free within 250 MB of the gate-start reading, >= 2 GB, and >= 2x Wed's planned SD use (the
    Wed card's MB). A missing reading or --wed-mb is a fail. Pure."""
    free, free0 = now.get("free_b"), start.get("free_b")
    checks = {
        "free_within_250MB_of_start": free is not None and free0 is not None
        and free0 - free <= SD_BAR_RUN_B,
        "free_ge_2GB": free is not None and free >= SD_MIN_FREE_B,
        "free_ge_2x_wed": free is not None and wed_mb is not None and wed_mb > 0
        and free >= 2 * wed_mb * 1e6,
    }
    return {"ok": all(checks.values()), "checks": checks,
            "free_drop_b": (free0 - free) if free is not None and free0 is not None else None}


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Records of a JSON-lines file and how many lines didn't parse (a hard reset mid-write
    leaves a torn last line; QE 006 nit)."""
    if not path.exists():
        return [], 0
    rows, torn = [], 0
    for ln in path.read_text(errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            torn += 1
    return rows, torn


def write_run_sums(run_dir: Path) -> int:
    """One SHA256SUMS over every file in the run folder (QE N4), ``shasum -a 256 -c`` format."""
    tmp = run_dir / "SHA256SUMS.tmp"
    files = sorted(p for p in run_dir.rglob("*")
                   if p.is_file() and p not in (run_dir / "SHA256SUMS", tmp))
    with tmp.open("w") as f:
        for p in files:
            f.write(f"{sha256(p)}  {p.relative_to(run_dir)}\n")
    tmp.replace(run_dir / "SHA256SUMS")
    return len(files)


def run_cycle(state: dict, run_dir: Path, started_by: str, log: Log) -> dict:
    n = state.get("cycles_done", 0) + 1
    t_start = uptime_s()
    rec: dict = {"cycle": n, "started_by": started_by, "utc": now_utc(),
                 "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 "uptime_start_s": round(t_start, 1), "mode": state.get("mode")}
    log(f"=== cycle {n} ({started_by}) boot {rec['boot_id'][:8]} uptime {t_start:.1f}s")
    rec["prev"] = previous_boot_end()
    for _ in range(4):  # {} when one register read fails (I2C busy at boot): retry briefly
        rec["power"] = pc.ups_power()
        if rec["power"]:
            break
        time.sleep(1.5)
    rec["throttled"] = sh(["vcgencmd", "get_throttled"]).strip().split("=")[-1]
    # The AE3 cold-boot fix (RESULTS.md): usb-storage never claims the OpenMV boards. Recorded per
    # boot so runs before/after Nick applies it stay separable.
    rec["msc_quirk"] = "usb-storage.quirks=" in Path("/proc/cmdline").read_text()
    rec["tty_uptime_s"] = wait_ttys(BYID_TIMEOUT_S)
    rec["kernel"] = kernel_events()
    log(f"prev boot end {rec['prev']} · power {rec['power']} · throttled {rec['throttled']} · "
        f"ttys {rec['tty_uptime_s']} · kernel {json.dumps(rec['kernel'])[:300]}")

    # First contact BEFORE any capture: the AE3 first (it is the board under test), then the N6.
    rec["contact"] = {cam: board_contact(cam, log) for cam in ("openmv_ae3", "openmv_n6")}

    if state.get("video_only"):  # pool 2 ready gate: boot -> 30 s clip -> readings -> shutdown
        rec["video_only"] = True
        rec["ram_boot"] = ram_reading()  # this boot's idle baseline (rev 7), before the slow du
        rec["sd_boot"] = sd_reading(run_dir)
        rec["video"] = cycle_clip(rec, run_dir, n, state, log)
        time.sleep(POST_CLIP_WAIT_S)  # then the G-a process check and the G-b / RAM readings
        rec["procs_after"] = recorder_procs()
        rec["ram_post"] = ram_reading(rec["ram_boot"])
        rec["sd_post"] = sd_reading(run_dir)
        rec.update(evaluate_sd(rec, state))
        log(f"post-clip +{POST_CLIP_WAIT_S:.0f}s: recorders left {rec['procs_after']} · SD free "
            f"{rec['sd_post'].get('free_b')} outside {rec['sd_post'].get('outside_b')} growth "
            f"{rec.get('sd_cycle_growth_b')} · G-b {'ok' if rec['gb_ok'] else rec['gb_fails']} · "
            f"RAM {rec['ram_post']}")
        rec["uptime_end_s"] = round(uptime_s(), 1)
        return evaluate(rec, state)

    # One set through the production path (pool_capture's own CLI call + run config).
    t_set = time.monotonic()
    line = pc.run_one_set(n, run_dir, Path(state["run_config"]), state["run_id"], list(CAMERAS),
                          ("--sync",) if state.get("sync") else ())
    rec["set_s"] = round(time.monotonic() - t_set, 1)
    # Upper bound for the last still's time on the uptime clock (monotonic; the wall clock can
    # step when NTP syncs mid-boot, QE #115): every still is written before the set returns.
    rec["uptime_set_end_s"] = round(uptime_s(), 1)
    rec["set_exit"] = line.get("exit_code")
    rec["set_tail"] = line.get("stdout_tail") or []
    exp = line.get("experiment")
    rec["experiment"] = exp
    if exp:
        rec["verify"] = verify_set(run_dir / exp)
        sync_path = run_dir / exp / "sync.json"
        if sync_path.is_file():  # synchronized mode: per-shot skew from the frame timestamps
            shots = json.loads(sync_path.read_text()).get("shots", [])
            rec["sync"] = {s["shot"]: {"skew_ms": s.get("skew_ms"),
                                       "offsets_ms": s.get("frame_offset_ms")} for s in shots}
        try:
            pc.write_sha256sums(run_dir / exp)
        except OSError as exc:
            rec["sha256sums_error"] = str(exc)
    else:
        rec["verify"] = {}
    if state.get("video_s"):
        rec["video"] = run_video(run_dir, n, float(state["video_s"]), log, None, rec["boot_id"])
    rec["uptime_end_s"] = round(uptime_s(), 1)
    return evaluate(rec, state)


def cycle_clip(rec: dict, run_dir: Path, n: int, state: dict, log: Log) -> dict:
    """A video-only cycle's clip, unless SD free was < 2 GB (or unread) at this boot's reading:
    then no clip is added (F9; a hard reset or a switch-mode boot after the SD stop would
    otherwise keep adding clips, QE 006 nit). The cycle then fails G-a (nothing recorded)."""
    free = (rec.get("sd_boot") or {}).get("free_b")
    if free is None or free < SD_MIN_FREE_B:
        log(f"SD free {free} B < 2 GB at boot: no clip this cycle")
        return {"ok": False, "error": f"skipped: SD free {free} B at boot"}
    return run_video(run_dir, n, float(state.get("video_s") or 30), log,
                     state.get("video_args") or [], rec.get("boot_id", ""))


def video_dir(run_dir: Path, n: int, boot_id: str) -> Path:
    """A new folder for this cycle attempt: video/cycleNNN_<boot id[:8]>, plus _2, _3, ... if it
    exists. A hard reset mid-cycle re-runs cycle n on the next boot; its clips must not overwrite
    the failed attempt's evidence (QE 005 R8)."""
    base = run_dir / "video" / f"cycle{n:03d}_{(boot_id or 'noboot')[:8]}"
    out, k = base, 1
    while out.exists():
        k += 1
        out = base.with_name(f"{base.name}_{k}")
    return out


def run_video(run_dir: Path, n: int, seconds: float, log: Log,
              extra_args: list[str] | None = None, boot_id: str = "") -> dict:
    """One synchronized video clip on all three cameras (scripts/sync_video.py); its summary.
    ``extra_args`` are passed through (pool 2: each board's settings, e.g. --n6-framesize HD)."""
    out = video_dir(run_dir, n, boot_id)
    out.mkdir(parents=True)
    cmd = [sys.executable, str(ROOT / "scripts" / "sync_video.py"), "--seconds", str(seconds),
           "--out", str(out), *(extra_args or [])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:]
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "dir": str(out.relative_to(run_dir))}
    try:
        summary = json.loads((out / "video_sync.json").read_text())
    except (OSError, ValueError):
        return {"ok": False, "error": f"no video_sync.json: {tail}",
                "dir": str(out.relative_to(run_dir))}
    cams = summary.get("cameras") or {}
    v = {"ok": bool(summary.get("ok")), "dir": str(out.relative_to(run_dir)),
         "skew_ms": summary.get("start_skew_ms"),
         "fps": {c: r.get("fps") for c, r in cams.items()},
         "lost": sum(r.get("frames_lost") or 0 for r in cams.values()),
         "host": summary.get("host"), "cams": video_cams(summary, seconds)}
    log(f"video {seconds:.0f}s: {'ok' if v['ok'] else 'FAIL'} skew {v['skew_ms']} ms fps "
        f"{v['fps']} lost {v['lost']} host {v['host']}")
    return v


def video_cams(summary: dict, seconds: float) -> dict:
    """Per camera, from video_sync.json. ``recorded`` (G-a): the camera started (no arm/fire
    error) and its file holds >= 1 frame. Everything else is the completeness score (d52: never
    gates): frames, frames of nominal, lost, fps, its own fire -> first-frame delay. Pure."""
    cams = summary.get("cameras") or {}
    delays = summary.get("first_frame_delay_ms") or {}
    out = {}
    for cam in CAMERAS:
        r = cams.get(cam)
        if r is None:
            out[cam] = {"recorded": False, "why": "missing"}
            continue
        err = str(r.get("error") or "")
        started = not err.startswith(("arm:", "fire:"))
        frames = r.get("frames") or 0
        recorded = started and (r.get("bytes") or 0) > 0 and frames >= 1
        asked = (r.get("settings") or {}).get("fps") or (summary.get("args") or {}).get(
            "imx_fps" if cam == "imx708" else "board_fps")
        lost = r.get("frames_lost") if cam != "imx708" else None
        out[cam] = {"recorded": recorded, "why": None if recorded else (err[:80] or "no_frames"),
                    "frames": frames, "fps": r.get("fps"), "fps_clip": r.get("fps_clip"),
                    "lost": lost,
                    "of_nominal": round(frames / (float(asked) * seconds), 3)
                    if asked and seconds else None,
                    "complete": bool(recorded and not err and not lost),
                    "first_frame_ms": delays.get(cam), "bytes": r.get("bytes")}
    return out


def evaluate(rec: dict, state: dict) -> dict:
    """PASS/FAIL per PREREG §4 and the streak. Pure: unit-tested on recorded dicts.
    Video-only cycles (pool 2 ready gate) are judged by G-a instead (``_video_only_fails``)."""
    if rec.get("video_only"):
        return _streak(rec, state, _video_only_fails(rec))
    fails: list[str] = []
    v = rec.get("verify") or {}
    for cam in CAMERAS:
        still = (v.get(cam) or {}).get("still") or {}
        if not still.get("ok"):
            fails.append(f"{cam}_still:{still.get('why', 'missing')}")
    prev = rec.get("prev") or {}
    if rec.get("cycle", 1) > 1 and not prev.get("clean"):
        fails.append("prev_shutdown_not_clean")
    # Boards really lost power: their uptime at first contact is below the Pi's uptime.
    cycled = []
    for cam in ("openmv_ae3", "openmv_n6"):
        c = (rec.get("contact") or {}).get(cam) or {}
        _, ticks_s, _ = contact_cols(c)
        # the board powers up with the Pi, ~5 s before the Pi kernel starts counting uptime
        pi_up = (c.get("first") or {}).get("pi_uptime_s") or rec.get("uptime_start_s", 0)
        if ticks_s is not None:
            cycled.append(ticks_s <= pi_up + 30)
    rec["boards_power_cycled"] = all(cycled) if cycled else None
    if rec["boards_power_cycled"] is False and rec.get("started_by") == "boot":
        fails.append("boards_not_power_cycled")
    mt = [((v.get(c) or {}).get("still") or {}).get("mtime") for c in CAMERAS]
    mt = [m for m in mt if m]
    rec["still_skew_s"] = round(max(mt) - min(mt), 1) if len(mt) >= 2 else None
    if mt and rec.get("uptime_set_end_s") is not None:
        last = rec["uptime_set_end_s"]
        rec["last_still_uptime_s"] = round(last, 1)
        if rec.get("started_by") == "boot" and last > MAX_LAST_STILL_UPTIME_S:
            fails.append(f"slow:{last:.0f}s")
    vid = rec.get("video")
    if vid is not None:
        if not vid.get("ok"):
            fails.append(f"video:{vid.get('error', 'camera_failed')}")
        elif vid.get("skew_ms") is None or vid["skew_ms"] > MAX_VIDEO_SKEW_MS:
            fails.append(f"video_skew:{vid.get('skew_ms')}")
        elif vid.get("lost"):
            fails.append(f"video_lost:{vid['lost']}")
    rec["fail_reasons"] = fails
    if fails and _only_dark(fails, v):
        rec["result"] = "VOID"   # too dark to verify: neither a PASS nor a reset (PREREG A5)
        rec["streak"] = state.get("streak", 0)
    else:
        rec["result"] = "FAIL" if fails else "PASS"
        rec["streak"] = 0 if fails else state.get("streak", 0) + 1
    return rec


def _boards_cycled(rec: dict) -> bool | None:
    cycled = []
    for cam in ("openmv_ae3", "openmv_n6"):
        c = (rec.get("contact") or {}).get(cam) or {}
        _, ticks_s, _ = contact_cols(c)
        pi_up = (c.get("first") or {}).get("pi_uptime_s") or rec.get("uptime_start_s", 0)
        if ticks_s is not None:
            cycled.append(ticks_s <= pi_up + 30)
    return all(cycled) if cycled else None


def _video_only_fails(rec: dict) -> list[str]:
    """G-a for one pool 2 cycle: booted (this ran), every camera recorded, 0 recorder processes
    30 s after the clip, the previous shutdown was clean, the boards really lost power. Video
    completeness, fps and skew are scored in rec["video"]["cams"] and never fail a cycle (d52)."""
    fails = []
    if rec.get("cycle", 1) > 1 and not (rec.get("prev") or {}).get("clean"):
        fails.append("prev_shutdown_not_clean")
    rec["boards_power_cycled"] = _boards_cycled(rec)
    if rec["boards_power_cycled"] is False and rec.get("started_by") == "boot":
        fails.append("boards_not_power_cycled")
    vid = rec.get("video") or {}
    cams = vid.get("cams") or {}
    if not cams:
        fails.append(f"video:{vid.get('error', 'missing')}")
    for cam in CAMERAS:
        c = cams.get(cam) or {}
        if cams and not c.get("recorded"):
            fails.append(f"{cam}_video:{c.get('why') or 'missing'}")
    procs = rec.get("procs_after")
    if procs is None:
        fails.append("procs:not_checked")
    elif procs:
        fails.append(f"procs_left:{len(procs)}")
    return fails


def _streak(rec: dict, state: dict, fails: list[str]) -> dict:
    rec["fail_reasons"] = fails
    rec["result"] = "FAIL" if fails else "PASS"
    rec["streak"] = 0 if fails else state.get("streak", 0) + 1
    return rec


def _only_dark(fails: list[str], v: dict) -> bool:
    """PREREG A5: every failure is an intact-but-small board still of a dark scene."""
    imx = (v.get("imx708") or {}).get("still") or {}
    if not imx.get("ok") or imx.get("mean") is None or imx["mean"] >= DARK_IMX_MEAN:
        return False
    for f in fails:
        cam, _, why = f.partition("_still:")
        still = (v.get(cam) or {}).get("still") or {}
        if why != "size_ok" or cam == "imx708" or not (still.get("sha_ok") and
                                                         still.get("decode_ok")):
            return False
        if still.get("mean") is None or still["mean"] >= DARK_BOARD_MEAN:
            return False
    return True


def csv_row(rec: dict) -> dict:
    pw = rec.get("power") or {}
    k = rec.get("kernel") or {}
    v = rec.get("verify") or {}
    c_ae3, t_ae3, imp_ae3 = contact_cols((rec.get("contact") or {}).get("openmv_ae3") or {})
    c_n6, t_n6, _ = contact_cols((rec.get("contact") or {}).get("openmv_n6") or {})

    def still(cam):
        s = (v.get(cam) or {}).get("still") or {}
        return f"ok {s.get('bytes')}B {s.get('seconds')}s m{s.get('mean')}" if s.get("ok") \
            else f"FAIL {s.get('why')} {s.get('bytes')}B m{s.get('mean')}"

    raws = sum(1 for c in CAMERAS if ((v.get(c) or {}).get("raw") or {}).get("ok"))
    return {
        "cycle": rec.get("cycle"), "started_by": rec.get("started_by"), "utc": rec.get("utc"),
        "boot_id": (rec.get("boot_id") or "")[:8], "uptime_start_s": rec.get("uptime_start_s"),
        "prev_end": "clean" if (rec.get("prev") or {}).get("clean") else "NOT_CLEAN",
        "prev_by": (rec.get("prev") or {}).get("by"),
        "vin_mv": pw.get("vin_mv"), "vbat_mv": pw.get("vbat_mv"),
        "on_battery": pw.get("on_battery"), "throttled": rec.get("throttled"),
        "msc_quirk": rec.get("msc_quirk"),
        "ae3_enum_s": (k.get("openmv_ae3") or {}).get("enum_s"),
        "n6_enum_s": (k.get("openmv_n6") or {}).get("enum_s"),
        "ae3_tty_s": (rec.get("tty_uptime_s") or {}).get("openmv_ae3"),
        "n6_tty_s": (rec.get("tty_uptime_s") or {}).get("openmv_n6"),
        "ae3_contact": c_ae3, "ae3_board_ticks_s": t_ae3, "ae3_import_ms": imp_ae3,
        "n6_contact": c_n6, "n6_board_ticks_s": t_n6,
        "boards_power_cycled": rec.get("boards_power_cycled"), "set_s": rec.get("set_s"),
        "imx_still": still("imx708"), "n6_still": still("openmv_n6"),
        "ae3_still": still("openmv_ae3"), "raws_ok": f"{raws}/3",
        "still_skew_s": rec.get("still_skew_s"),
        "sync_still_ms": ((rec.get("sync") or {}).get("still") or {}).get("skew_ms"),
        "sync_raw_ms": ((rec.get("sync") or {}).get("raw") or {}).get("skew_ms"),
        "video": None if rec.get("video") is None else ("ok" if rec["video"].get("ok")
                                                        else "FAIL"),
        "video_skew_ms": (rec.get("video") or {}).get("skew_ms"),
        "video_fps": "/".join(str(v) for v in ((rec.get("video") or {}).get("fps") or {})
                              .values()) or None,
        "video_lost": (rec.get("video") or {}).get("lost"),
        "last_still_uptime_s": rec.get("last_still_uptime_s"), "result": rec.get("result"),
        "fail_reasons": ";".join(rec.get("fail_reasons") or []), "streak": rec.get("streak"),
        **_video_only_cols(rec),
    }


def _video_only_cols(rec: dict) -> dict:
    if not rec.get("video_only"):
        return dict.fromkeys(VIDEO_ONLY_FIELDS)
    cams = (rec.get("video") or {}).get("cams") or {}

    def cam(c):
        v = cams.get(c) or {}
        if not v.get("recorded"):
            return f"NO {v.get('why')}"
        return (f"{v.get('frames')}fr {v.get('fps_clip')}fps/clip lost {v.get('lost')} "
                f"{v.get('of_nominal')} {'complete' if v.get('complete') else 'INCOMPLETE'}")

    boot, post = rec.get("sd_boot") or {}, rec.get("sd_post") or {}
    ram = rec.get("ram_post") or {}
    return {
        "video_only": True, "imx_video": cam("imx708"), "n6_video": cam("openmv_n6"),
        "ae3_video": cam("openmv_ae3"),
        "video_complete": all((cams.get(c) or {}).get("complete") for c in CAMERAS),
        "first_frame_ms": "/".join(str((cams.get(c) or {}).get("first_frame_ms"))
                                   for c in CAMERAS),
        "procs_left": len(rec["procs_after"]) if rec.get("procs_after") is not None else None,
        "sd_free_boot_b": boot.get("free_b"), "sd_free_post_b": post.get("free_b"),
        "sd_outside_post_b": post.get("outside_b"),
        "sd_cycle_growth_b": rec.get("sd_cycle_growth_b"),
        "sd_run_growth_b": rec.get("sd_run_growth_b"), "gb_ok": rec.get("gb_ok"),
        "gb_fails": ";".join(rec.get("gb_fails") or []), "journal_b": post.get("journal_b"),
        "home_b": post.get("home_b"), "var_log_b": post.get("var_log_b"),
        "mem_avail_mb_post": ram.get("mem_avail_mb"), "cma_free_mb_post": ram.get("cma_free_mb"),
        "cma_free_mb_idle": (rec.get("ram_boot") or {}).get("cma_free_mb"),
        "ram_flag": ram.get("flag"),
    }


def record(run_dir: Path, rec: dict) -> None:
    with (run_dir / "cycles.jsonl").open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    path = run_dir / "cycles.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(csv_row(rec))
        f.flush()
        os.fsync(f.fileno())


# -- arming ------------------------------------------------------------------------------------
def crontab_lines() -> list[str]:
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return out.stdout.splitlines() if out.returncode == 0 else []


def set_crontab(lines: list[str]) -> None:
    if lines:
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
    else:
        subprocess.run(["crontab", "-r"], capture_output=True)


def cmd_arm(a) -> int:
    if STATE.exists():
        print(f"gate: already armed ({STATE}); disarm first")
        return 1
    if POOL_ARMED.exists():
        print("gate: pool mode is armed (~/.pool_armed) — disarm it first")
        return 1
    GATE_ROOT.mkdir(parents=True, exist_ok=True)
    n = len(list(GATE_ROOT.glob("gate[0-9]*"))) + 1
    run_id = f"gate{n:03d}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}" + (
        f"_{a.label}" if a.label else "")
    run_dir = GATE_ROOT / run_id
    run_dir.mkdir()
    video_only = bool(getattr(a, "video_only", False))
    sd_start = sd_reading(run_dir) if video_only else None
    if video_only:
        # G-b 4: before arming, SD free >= 2 GB + target x the per-cycle run size (part B).
        need = SD_MIN_FREE_B + int(a.target * float(getattr(a, "cycle_mb", 0) or 0) * 1e6)
        if (sd_start.get("free_b") or 0) < need:
            run_dir.rmdir()
            print(f"gate: SD free {sd_start.get('free_b')} B < {need} B (2 GB + {a.target} x "
                  f"{getattr(a, 'cycle_mb', 0)} MB): pull and clear first (spec rev 4, G-b 4)")
            return 1
    before = crontab_lines()
    (run_dir / "crontab_before.txt").write_text("\n".join(before) + ("\n" if before else ""))
    flag = "absent"
    if DIVE_FLAG.exists():
        flag = "ours" if FLAG_MARK in DIVE_FLAG.read_text() else "nick"
    if flag == "absent":
        DIVE_FLAG.write_text(f"{FLAG_MARK} {now_utc()}\n")
    py = sys.executable
    set_crontab([ln for ln in before if CRON_MARK not in ln] + [
        f"@reboot {py} {ROOT / 'scripts' / 'gate_cycle.py'} boot >>{run_dir / 'cron.log'} "
        f"2>&1 {CRON_MARK}"])
    args = SimpleNamespace(config=a.config, lens_position=a.lens_position)
    run_config = pc.write_run_config(run_dir, args)
    if video_only:
        pc.atomic_json(Path.home() / SD_START_FMT.format(run=run_id),
                       {"run_id": run_id, "sd_start": sd_start, "utc": now_utc()})
    state = {"run_id": run_id, "run_dir": str(run_dir), "run_config": str(run_config),
             "target": a.target, "mode": a.mode, "cycles_done": 0, "streak": 0,
             "fails_in_row": 0, "dive_flag_before": flag, "armed_utc": now_utc(),
             "sha": pc.git_sha(), "wake_min": a.wake_min,
             "stop_after_fails": a.stop_after_fails, "label": a.label, "sync": a.sync,
             "video_s": a.video_s, "until_streak": a.until_streak,
             "repeat": getattr(a, "repeat", 1), "dark_wake_min": getattr(a, "dark_wake_min", 0),
             "lens": a.lens_position, "config": a.config,
             "previous_run": getattr(a, "previous_run", None),
             "video_only": video_only, "video_args": list(getattr(a, "video_args", None) or []),
             "cycle_mb": getattr(a, "cycle_mb", 0), "sd_start": sd_start}
    save_state(state)
    prereg = ("docs/pool2_20261021/TEST_CARD_B.md" if video_only
              else "docs/rig_gate_20261014/PREREG.md")
    pc.atomic_json(run_dir / "run_manifest.json", {**state, "prereg": prereg,
                                                   "host": os.uname().nodename})
    print(f"gate: ARMED {run_id} target {a.target} mode {a.mode}; dive flag pre-arm: {flag}")
    print("gate: next boot runs a cycle; `gate_cycle.py once` runs one now")
    return 0


def cmd_disarm(_a) -> int:
    state = load_state()
    set_crontab([ln for ln in crontab_lines() if CRON_MARK not in ln])
    if state.get("dive_flag_before") in ("absent", "ours") and DIVE_FLAG.exists() \
            and FLAG_MARK in DIVE_FLAG.read_text():
        DIVE_FLAG.unlink()
        print("gate: removed ~/.no_dive_autostart (dive-autostart runs ci-record at next boot)")
    if STATE.exists():
        STATE.unlink()
    print(f"gate: DISARMED {state.get('run_id', '')}; crontab: "
          f"{sum(CRON_MARK in ln for ln in crontab_lines())} gate line(s)")
    return 0


def cmd_status(_a) -> int:
    state = load_state()
    if not state:
        print("gate: not armed")
        return 0
    keys = ("run_id", "mode", "target", "until_streak", "repeat", "cycles_done", "streak",
            "best_streak", "fails_in_row", "voids", "previous_run")
    print(json.dumps({k: state[k] for k in keys if k in state}))
    path = Path(state["run_dir"]) / "cycles.csv"
    if path.exists():
        rows = list(csv.DictReader(path.open()))
        for r in rows[-5:]:
            print(f"  #{r['cycle']} {r['result']} streak {r['streak']} ae3 {r['ae3_contact']} "
                  f"imx {r['imx_still']} | n6 {r['n6_still']} | ae3 {r['ae3_still']} "
                  f"{r['fail_reasons']}")
    return 0


def cmd_stop(_a) -> int:
    state = load_state()
    if state:
        (Path(state["run_dir"]) / "STOP").touch()
    print("gate: STOP placed — the current/next cycle will not power-cycle")
    return 0


def cmd_mode(a) -> int:
    state = load_state()
    state["mode"] = a.mode
    save_state(state)
    print(f"gate: mode {a.mode}")
    return 0


def next_step(state: dict, rec: dict, run_dir: Path, log: Log) -> None:
    """Auto mode: power-cycle through the UPS for the next cycle, unless a stop condition holds."""
    if state.get("until_streak") and state.get("streak", 0) >= state["until_streak"]:
        left = int(state.get("repeat", 1)) - 1
        if left <= 0:
            log(f"GATE PASSED: {state['streak']} PASS in a row — disarming, rig stays up")
            cmd_disarm(None)
            return
        # Repeatability (Nick: "then run it once more"): a fresh run, streak from 0.
        log(f"GATE PASSED: {state['streak']} PASS in a row — arming the repeat run "
            f"({left} to go)")
        prev = dict(state)
        cmd_disarm(None)
        cmd_arm(SimpleNamespace(
            target=prev["target"], mode=prev["mode"], wake_min=prev.get("wake_min", 1),
            stop_after_fails=prev.get("stop_after_fails", MAX_FAILS_IN_ROW),
            label=f"{prev.get('label', '')}-repeat", sync=prev.get("sync", False),
            video_s=prev.get("video_s", 0), until_streak=prev["until_streak"], repeat=left,
            dark_wake_min=prev.get("dark_wake_min", 0), lens_position=prev.get("lens", "0.8"),
            config=prev.get("config", "configs/rig.example.yaml"),
            previous_run=prev["run_id"], video_only=prev.get("video_only", False),
            video_args=prev.get("video_args") or [], cycle_mb=prev.get("cycle_mb", 0)))
        state = load_state()
        run_dir = Path(state["run_dir"])
        log = Log(run_dir / "gate.log")
        log(f"repeat run armed after {prev['run_id']} passed")
    if state.get("cycles_done", 0) >= state.get("target", 0):
        log(f"cycle cap {state['target']} reached (streak {state.get('streak', 0)}) — "
            "disarming, rig stays up")
        cmd_disarm(None)
        return
    if (run_dir / "STOP").exists():
        log("STOP present — not power-cycling; rig stays up")
        return
    if rec.get("video_only") and sd_stop(rec):
        (run_dir / "STOP").write_text(f"SD stop {now_utc()}: {rec.get('gb_fails')}\n")
        log(f"SD stop (F9, card §8 rule 4): {rec.get('gb_fails')} — STOP written, not "
            "power-cycling; rig stays up; pull + clear, then re-check")
        return
    stop_after = state.get("stop_after_fails", MAX_FAILS_IN_ROW)
    if stop_after and state.get("fails_in_row", 0) >= stop_after:
        log(f"{stop_after} FAILs in a row — not power-cycling; rig stays up for inspection")
        return
    if state.get("mode") != "auto":
        log("mode switch: READY — Nick: switch OFF, wait for the LED to go dark, switch ON")
        return
    wake = state.get("wake_min", 1)
    if rec.get("result") == "VOID" and state.get("dark_wake_min"):
        wake = state["dark_wake_min"]  # dark: wait for light instead of burning cycles
    log(f"gate_cycle: requesting UPS power cycle (wake {wake} min)")
    env = {**os.environ, "MAX_PER_DAY": "400", "MIN_UPTIME_S": "0"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "ups_power_cycle.sh"), "--yes",
                        "--force", "--tag", "gate", "--wake-min", str(wake),
                        "--reason", f"gate {state['run_id']} cycle {rec['cycle']}"],
                       env=env, capture_output=True, text=True, timeout=60)
    log(f"ups_power_cycle rc={r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}")


def _run_dir(a) -> Path | None:
    if getattr(a, "run", None):
        return GATE_ROOT / a.run
    state = load_state()
    return Path(state["run_dir"]) if state else None


def cmd_seal(a) -> int:
    """QE N4: one SHA256SUMS over the whole run folder (clips, cycles.csv/jsonl, gate.log, ...),
    written on the Pi after the gate; verified on the Mac with `shasum -a 256 -c` before any
    clear. Refuses while the gate is armed on that run."""
    run_dir = _run_dir(a)
    if run_dir is None or not run_dir.is_dir():
        print("gate seal: name the run (gate_cycle.py seal <run_id>)")
        return 1
    if load_state().get("run_dir") == str(run_dir):
        print("gate seal: that run is still armed; disarm first")
        return 1
    manifest = run_dir / "run_manifest.json"
    gb = None
    if manifest.is_file() and json.loads(manifest.read_text()).get("video_only"):
        # G-b over the whole gate (bars 2-4), written before the sums so it is pulled and sealed
        rows, torn = read_jsonl(run_dir / "cycles.jsonl")
        gb = sd_verdict(rows, json.loads(manifest.read_text()).get("sd_start") or {})
        pc.atomic_json(run_dir / "gb_verdict.json", {**gb, "cycles": len(rows), "torn_lines": torn,
                                                     "utc": now_utc()})
        print(f"gate seal: G-b {'PASS' if gb['ok'] else 'FAIL'} {gb['fails']} "
              f"(last run growth {gb['last_run_growth_b']} B; {torn} torn line(s) skipped) "
              "-> gb_verdict.json")
    n = write_run_sums(run_dir)
    print(f"gate seal: {run_dir / 'SHA256SUMS'} covers {n} files; on the Mac, in the pulled "
          f"folder: shasum -a 256 -c SHA256SUMS")
    return 0


def load_sd_start(run: str) -> dict:
    """The gate-start SD reading: from the run's manifest while the folder exists, else from
    ~/.gate_sd_start_<run>.json (written at arm, so it survives the clear; QE 005 R5)."""
    manifest = GATE_ROOT / run / "run_manifest.json"
    if manifest.is_file():
        start = json.loads(manifest.read_text()).get("sd_start")
        if start:
            return start
    kept = Path.home() / SD_START_FMT.format(run=run)
    return (json.loads(kept.read_text()).get("sd_start") or {}) if kept.is_file() else {}


def cmd_sd(a) -> int:
    """Rev 4 item 5 / card §6 step 6, after the pull, the Mac verify and the clear: SD free within
    250 MB of the gate start, >= 2 GB and >= 2x Wed's planned use. Exit 1 on any miss."""
    run = a.run or load_state().get("run_id")
    if not run:
        print("gate sd: name the run (gate_cycle.py sd <run_id> --wed-mb MB)")
        return 1
    start = load_sd_start(run)
    if not start:
        print(f"gate sd: no gate-start reading for {run} (no manifest, no "
              f"~/{SD_START_FMT.format(run=run)})")
        return 1
    run_dir = GATE_ROOT / run
    now = sd_reading(run_dir)
    v = sd_clean(start, now, a.wed_mb)
    print(json.dumps({"run": run, "run_folder_still_on_pi": run_dir.exists(),
                      "wed_mb": a.wed_mb, "start": start, "now": now, **v}, indent=1))
    still = run_dir.exists()
    if still:
        print("gate sd: the run folder is still on the Pi; clear it only after the Mac verify "
              "(card §6 steps 3-5), then re-run")
    ok = v["ok"] and not still  # step 6 is the after-clear check (QE 006 nit)
    misses = [k for k, c in v["checks"].items() if not c] + (["run_folder_on_pi"] if still else [])
    print(f"gate sd: {'CLEAN' if ok else 'NOT CLEAN'} {misses}")
    return 0 if ok else 1


def cmd_cycle(a) -> int:
    state = load_state()
    if not state:
        print("gate: not armed (gate_cycle.py arm ...)")
        return 1
    run_dir = Path(state["run_dir"])
    log = Log(run_dir / "gate.log")
    if a.cmd == "boot":
        time.sleep(max(0.0, a.settle_s - uptime_s()))  # let the boards enumerate
    try:
        rec = run_cycle(state, run_dir, "boot" if a.cmd == "boot" else "manual", log)
    except Exception as exc:  # noqa: BLE001 - a harness crash is a FAIL row, never silence
        import traceback
        rec = {"cycle": state.get("cycles_done", 0) + 1, "utc": now_utc(), "result": "FAIL",
               "fail_reasons": [f"harness:{exc!r}"], "streak": 0,
               "traceback": traceback.format_exc()[-1500:], "started_by": a.cmd}
        log(f"HARNESS ERROR {exc!r}")
    record(run_dir, rec)
    state["cycles_done"] = rec["cycle"]
    state["streak"] = rec["streak"]
    if rec["result"] == "VOID":
        state["voids"] = state.get("voids", 0) + 1  # dark: fails_in_row unchanged
    else:
        state["fails_in_row"] = (0 if rec["result"] == "PASS"
                                 else state.get("fails_in_row", 0) + 1)
    state["best_streak"] = max(state.get("best_streak", 0), rec["streak"])
    if (rec.get("sd_post") or {}).get("outside_b") is not None:
        state["sd_prev_post"] = rec["sd_post"]  # QE N2: growth is post-clip to post-clip
    save_state(state)
    log(f"cycle {rec['cycle']} {rec['result']} {';'.join(rec.get('fail_reasons') or [])} "
        f"streak {rec['streak']} (best {state['best_streak']}) skew {rec.get('still_skew_s')}s "
        f"last still at uptime {rec.get('last_still_uptime_s')}s")
    if a.cmd == "boot":
        next_step(state, rec, run_dir, log)
    return 0 if rec["result"] == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    arm = sub.add_parser("arm")
    arm.add_argument("--target", type=int, default=25,
                     help="cycle cap: stop after this many cycles in total")
    arm.add_argument("--repeat", type=int, default=1,
                     help="after --until-streak passes, arm a fresh run this many times in all "
                          "(2 = the gate, then once more for repeatability)")
    arm.add_argument("--dark-wake-min", type=int, default=0,
                     help="after a VOID (dark) cycle, wake after N min instead of --wake-min")
    arm.add_argument("--until-streak", type=int, default=0,
                     help="stop as soon as this many PASS in a row (Nick's bar: 25); "
                          "0 = run to --target")
    arm.add_argument("--mode", choices=("auto", "switch"), default="auto")
    arm.add_argument("--wake-min", type=int, default=1)
    arm.add_argument("--stop-after-fails", type=int, default=MAX_FAILS_IN_ROW,
                     help="auto mode stops power-cycling after N FAILs in a row (0 = never)")
    arm.add_argument("--label", default="", help="appended to the run id (e.g. baseline)")
    arm.add_argument("--sync", action="store_true",
                     help="synchronized capture (experiment --sync): all cameras one moment")
    arm.add_argument("--video-s", type=float, default=0,
                     help="after the stills, a synchronized video clip of N s (0 = none)")
    arm.add_argument("--lens-position", default="0.8")
    arm.add_argument("--config", default="configs/rig.example.yaml")
    arm.add_argument("--video-only", action="store_true",
                     help="pool 2 ready gate: no stills; boot -> one --video-s clip -> 30 s -> "
                          "G-a process check + G-b SD / RAM readings")
    arm.add_argument("--video-args", type=shlex.split, default=[],
                     help='passed to sync_video.py, e.g. "--n6-framesize HD --ae3-subsampling 420"')
    arm.add_argument("--cycle-mb", type=float, default=0,
                     help="per-cycle run-folder MB at Wed's settings (part B): pre-arm SD check")
    for name in ("boot", "once"):
        p = sub.add_parser(name)
        p.add_argument("--settle-s", type=float, default=20.0,
                       help="boot: start no earlier than this uptime")
    sub.add_parser("status")
    sub.add_parser("seal").add_argument("run", nargs="?", default="")
    sd = sub.add_parser("sd")
    sd.add_argument("run", nargs="?", default="")
    sd.add_argument("--wed-mb", type=float, default=None,
                    help="the Wed card's planned SD use, MB (free must be >= 2x this)")
    sub.add_parser("stop")
    sub.add_parser("disarm")
    m = sub.add_parser("mode")
    m.add_argument("mode", choices=("auto", "switch"))
    a = ap.parse_args()
    if a.cmd == "arm" and a.video_args:  # checked before arming, not at the first boot's clip
        spec = importlib.util.spec_from_file_location("sync_video",
                                                      ROOT / "scripts" / "sync_video.py")
        sv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sv)
        try:
            sv.check_extra_args(a.video_args)
        except ValueError as exc:
            ap.error(f"--video-args: {exc}")
    return {"arm": cmd_arm, "disarm": cmd_disarm, "status": cmd_status, "stop": cmd_stop,
            "mode": cmd_mode, "boot": cmd_cycle, "once": cmd_cycle, "seal": cmd_seal,
            "sd": cmd_sd}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
