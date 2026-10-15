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
Outputs: results/gate/<run>/{cycles.csv, cycles.jsonl, gate.log, run_manifest.json, rig.yaml,
  profiles/, <date>/exp_*/ (one experiment per cycle, with SHA256SUMS)}

Assumptions: nereus002 (LiFePO4wered/Pi+, boards on the USB hub); no root (cron + the UPS CLI
  as pi). Never touches Nick's services: dive-autostart is stood down only through his own flag
  ~/.no_dive_autostart, exactly as pool_mode.sh does (pre-arm state recorded and restored).
Limits: the IMX708 has no board uptime, so "the camera lost power" is shown for the boards only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
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
MAX_FAILS_IN_ROW = 3              # auto mode stops power-cycling after this many FAILs
                                  # (arm --stop-after-fails; 0 = never, for baseline runs)
BYID_TIMEOUT_S = 60.0
CSV_FIELDS = [
    "cycle", "started_by", "utc", "boot_id", "uptime_start_s", "prev_end", "prev_by",
    "vin_mv", "vbat_mv", "on_battery", "throttled", "ae3_enum_s", "n6_enum_s",
    "ae3_tty_s", "n6_tty_s", "ae3_contact", "ae3_board_ticks_s", "ae3_import_ms",
    "n6_contact", "n6_board_ticks_s", "boards_power_cycled", "set_s",
    "imx_still", "n6_still", "ae3_still", "raws_ok", "still_skew_s", "sync_still_ms",
    "sync_raw_ms",
    "last_still_uptime_s", "result", "fail_reasons", "streak",
]


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
    ticks = info.get("ticks_ms")
    boot = info.get("boot_ms") or {}
    imp = (boot.get("imported") - boot.get("main")) if {"imported", "main"} <= set(boot) else None
    text = "ok" if first.get("ok") else (
        f"{first.get('stage')}->{rec.get('recovered_by', 'NOT_RECOVERED')}")
    return text, (round(ticks / 1000, 1) if isinstance(ticks, int) else None), imp


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
    rec["tty_uptime_s"] = wait_ttys(BYID_TIMEOUT_S)
    rec["kernel"] = kernel_events()
    log(f"prev boot end {rec['prev']} · power {rec['power']} · throttled {rec['throttled']} · "
        f"ttys {rec['tty_uptime_s']} · kernel {json.dumps(rec['kernel'])[:300]}")

    # First contact BEFORE any capture: the AE3 first (it is the board under test), then the N6.
    rec["contact"] = {cam: board_contact(cam, log) for cam in ("openmv_ae3", "openmv_n6")}

    # One set through the production path (pool_capture's own CLI call + run config).
    t_set = time.monotonic()
    line = pc.run_one_set(n, run_dir, Path(state["run_config"]), state["run_id"], list(CAMERAS),
                          ("--sync",) if state.get("sync") else ())
    rec["set_s"] = round(time.monotonic() - t_set, 1)
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
    rec["uptime_end_s"] = round(uptime_s(), 1)
    return evaluate(rec, state)


def evaluate(rec: dict, state: dict) -> dict:
    """PASS/FAIL per PREREG §4 and the streak. Pure: unit-tested on recorded dicts."""
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
        _, ticks_s, _ = contact_cols((rec.get("contact") or {}).get(cam) or {})
        if ticks_s is not None:
            cycled.append(ticks_s <= rec.get("uptime_start_s", 0) + 60)
    rec["boards_power_cycled"] = all(cycled) if cycled else None
    if rec["boards_power_cycled"] is False and rec.get("started_by") == "boot":
        fails.append("boards_not_power_cycled")
    mt = [((v.get(c) or {}).get("still") or {}).get("mtime") for c in CAMERAS]
    mt = [m for m in mt if m]
    rec["still_skew_s"] = round(max(mt) - min(mt), 1) if len(mt) >= 2 else None
    if mt and rec.get("uptime_end_s") is not None:
        last = rec["uptime_end_s"] - (time.time() - max(mt))
        rec["last_still_uptime_s"] = round(last, 1)
        if rec.get("started_by") == "boot" and last > MAX_LAST_STILL_UPTIME_S:
            fails.append(f"slow:{last:.0f}s")
    rec["result"] = "FAIL" if fails else "PASS"
    rec["fail_reasons"] = fails
    rec["streak"] = 0 if fails else state.get("streak", 0) + 1
    return rec


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
        "last_still_uptime_s": rec.get("last_still_uptime_s"), "result": rec.get("result"),
        "fail_reasons": ";".join(rec.get("fail_reasons") or []), "streak": rec.get("streak"),
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
    state = {"run_id": run_id, "run_dir": str(run_dir), "run_config": str(run_config),
             "target": a.target, "mode": a.mode, "cycles_done": 0, "streak": 0,
             "fails_in_row": 0, "dive_flag_before": flag, "armed_utc": now_utc(),
             "sha": pc.git_sha(), "wake_min": a.wake_min,
             "stop_after_fails": a.stop_after_fails, "label": a.label, "sync": a.sync}
    save_state(state)
    pc.atomic_json(run_dir / "run_manifest.json", {**state, "prereg":
                   "docs/rig_gate_20261014/PREREG.md", "host": os.uname().nodename})
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
    print(json.dumps({k: state[k] for k in ("run_id", "mode", "target", "cycles_done",
                                            "streak", "fails_in_row") if k in state}))
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
    if state.get("cycles_done", 0) >= state.get("target", 0):
        log(f"target {state['target']} cycles reached — disarming, rig stays up")
        cmd_disarm(None)
        return
    if (run_dir / "STOP").exists():
        log("STOP present — not power-cycling; rig stays up")
        return
    stop_after = state.get("stop_after_fails", MAX_FAILS_IN_ROW)
    if stop_after and state.get("fails_in_row", 0) >= stop_after:
        log(f"{stop_after} FAILs in a row — not power-cycling; rig stays up for inspection")
        return
    if state.get("mode") != "auto":
        log("mode switch: READY — Nick: switch OFF, wait for the LED to go dark, switch ON")
        return
    log(f"gate_cycle: requesting UPS power cycle (wake {state.get('wake_min', 1)} min)")
    env = {**os.environ, "MAX_PER_DAY": "200", "MIN_UPTIME_S": "0"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "ups_power_cycle.sh"), "--yes",
                        "--force", "--tag", "gate", "--wake-min", str(state.get("wake_min", 1)),
                        "--reason", f"gate {state['run_id']} cycle {rec['cycle']}"],
                       env=env, capture_output=True, text=True, timeout=60)
    log(f"ups_power_cycle rc={r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}")


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
    state["fails_in_row"] = 0 if rec["result"] == "PASS" else state.get("fails_in_row", 0) + 1
    state["best_streak"] = max(state.get("best_streak", 0), rec["streak"])
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
    arm.add_argument("--target", type=int, default=25)
    arm.add_argument("--mode", choices=("auto", "switch"), default="auto")
    arm.add_argument("--wake-min", type=int, default=1)
    arm.add_argument("--stop-after-fails", type=int, default=MAX_FAILS_IN_ROW,
                     help="auto mode stops power-cycling after N FAILs in a row (0 = never)")
    arm.add_argument("--label", default="", help="appended to the run id (e.g. baseline)")
    arm.add_argument("--sync", action="store_true",
                     help="synchronized capture (experiment --sync): all cameras one moment")
    arm.add_argument("--lens-position", default="0.8")
    arm.add_argument("--config", default="configs/rig.example.yaml")
    for name in ("boot", "once"):
        p = sub.add_parser(name)
        p.add_argument("--settle-s", type=float, default=20.0,
                       help="boot: start no earlier than this uptime")
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("disarm")
    m = sub.add_parser("mode")
    m.add_argument("mode", choices=("auto", "switch"))
    a = ap.parse_args()
    return {"arm": cmd_arm, "disarm": cmd_disarm, "status": cmd_status, "stop": cmd_stop,
            "mode": cmd_mode, "boot": cmd_cycle, "once": cmd_cycle}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
