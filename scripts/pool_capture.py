#!/usr/bin/env python3
"""pool_capture.py — unattended pool capture loop for the 3-camera rig (nereus002).

Purpose
    Nick's pool test (2026-10-14): while the Spotter cameras run their own pool sequence, this
    rig loops capture sets from all three cameras until power-off, the duration limit, a full
    disk, a low battery or a STOP file. No network is needed once it is running.

What one set is (reused, not new): ``nereus-rig experiment --raw --no-analysis`` in a fresh
    process — the path the HIL soak ran 135 times (``scripts/hil_soak.py``):
      IMX708  auto still JPEG + one ``rpicam-still --raw`` exposure → 4608x2592 DNG + JPEG
      N6/AE3  board ISP JPEG (HD 1280x800, auto) + 8-bit BGGR Bayer RAW (HD, metered then locked)
    Auto exposure / auto WB everywhere (Nick: "nothing fancy"). The IMX708 focus comes from the
    pool profile (default manual lens 0.8 = the Spotter units' pool setting, Nick 10/13 9:20 AM
    "flat port, hyperfocal 0.8"; ``--lens-position auto`` = rpicam AF per capture). The 1600x900
    production crop is made on the Mac from the full-res JPEG (exact, no Pi memory risk).

Run (ON the Pi, from the repo root; normally via ``scripts/pool_mode.sh``):
    .venv/bin/python scripts/pool_capture.py run [--hours 8] [--lens-position 0.8]
        [--mac-time 2026-10-14T10:02:00-07:00] [--interval-s 0]
    .venv/bin/python scripts/pool_capture.py summary results/pool/<run_id>

Outputs (one self-contained run folder, never deleted or edited afterwards)
    results/pool/<run_id>/             run_id = pool<NNN>_<Pi UTC stamp>; NNN counts up per run
      run_manifest.json   run facts + per-camera counts + stop reason (rewritten after every set)
      sets.jsonl          one JSON line per set: per camera files/bytes/sha256/applied settings,
                          set seconds, CPU temp, throttle, RAM, CMA, disk, UPS power, both clocks
      pool.log            the human health line per set (stdout of this script)
      power.csv           LiFePO4wered UPS readings every --power-period s
      rig.yaml, profiles/ the exact config + camera profiles this run used
      <date>/exp_<UTC>_pool/   one set: experiment.json, captures/<camera>/..., SHA256SUMS
    Stop: ``touch <run>/STOP`` (or pool_mode.sh stop); the set in flight finishes first.

Hard power cuts (the rig is switched off by a physical switch): every completed set is fsynced
    (files + directories) before its SHA256SUMS is written, and sets.jsonl / run_manifest.json /
    the run counter are written durably, so a cut loses at most the set in flight — which then
    has no SHA256SUMS and pulls as UNVERIFIED (expected).

Depth (SPEC_pool_codec_test §5.1): a fail-soft MS5837 (Bar30, I2C 0x76) reading at the start and
    end of every set (scripts/depth_ms5837.py in a subprocess, 5 s timeout): pressure mbar, water
    temperature, depth vs the surface pressure `pool_mode.sh arm` recorded and vs the run's first
    reading. Errors are logged in the set line only; after 3 failed sets in a row depth is
    disabled for the rest of the run (the bus is shared with the UPS). An unreadable UPS =
    battery UNKNOWN: logged loudly every set, capture continues (see the loop).

Timestamps: the pool has no NTP. Every set records a monotonic set number, CLOCK_MONOTONIC and
    seconds since boot, the Pi wall clock (restored from disk at boot — wrong after a power-off),
    the LiFePO4wered RTC (keeps running on the battery; measured ~97 s behind the Pi clock on
    2026-10-13) and the boot id. The Mac time given at start (``--mac-time``) pins the run.

Guards: refuse to start without disk for the planned duration (+ reserve) or on a battery below
    --start-vbat-mv (Nick's dive-autostart lesson: a 4 W load on a 3.23 V cell reset the Pi);
    stop cleanly before the disk reserve or at --min-vbat-mv on battery; pause when the CPU is at
    or over --max-cpu-c. One camera failing never stops the others (the coordinator guards each).

Known limitations
    - No root on nereus002: a board that drops off USB cannot be power-cycled from here; the
      next set re-resolves it by USB serial and the AE3/N6 adapters hard-reset over serial.
    - Set time is dominated by the board resets + 2 s metering; ~41 s median in the overnight
      soak (night, 135 cycles). Measured again in T1.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pool_led  # noqa: E402  (Nick's LED codes + the shared I2C lock)
from hil_soak import SERIALS, PowerLog, pi_health  # noqa: E402  (reuse, not a copy)


class LockedPowerLog(PowerLog):
    """hil_soak's UPS logger, reading only VIN/VBAT/VOUT/IOUT under the shared I2C lock (not the
    full register dump): the I2C budget in pool_led.py."""

    @staticmethod
    def read() -> dict | None:
        return pool_led.ups_read() or None

POOL_ROOT = ROOT / "results" / "pool"
SET_TIMEOUT_S = 300          # a healthy set is ~40-60 s; a hung one is killed and logged
DEPTH_PY = os.environ.get("POOL_DEPTH_PYTHON", "/usr/bin/python3")  # has smbus2; the venv not
# A depth read is ~0.5-1 s, plus up to 2 s of I2C-lock wait and the >= 3 s Bar30 gap (both
# inside the subprocess): 8 s so a slow lock can never count as a Bar30 strike (QE #101).
DEPTH_TIMEOUT_S = 8
# A Bar30 that fails this many sets in a row is switched off for the rest of the run: the bus is
# shared with the UPS (0x43), and a wet/damaged sensor holding SDA low would otherwise keep
# blinding the low-battery guard (QE #100).
DEPTH_DISABLE_AFTER = 3
BENCH_AFTER = 2              # consecutive failed sets before a board is benched (backoff)
SET_MB_PLAN = 32             # planning size per set (DNG 24 MB + JPEGs ~3-5 MB + 2x1 MB Bayer)
CAMERAS = ("imx708", "openmv_n6", "openmv_ae3")
_stop_signal = {"name": None}


# -- small readers -----------------------------------------------------------------------------
def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def clocks() -> dict:
    """Every time reference we have. Monotonic + uptime are trustworthy; the others are not."""
    up = _read("/proc/uptime").split()
    return {"mono_s": round(time.monotonic(), 3),
            "uptime_s": float(up[0]) if up else None,
            "pi_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ups_rtc_epoch": ups_get("RTC_TIME")}


def ups_get(key: str):
    """One LiFePO4wered register (int) or None when the UPS/CLI is absent (under the I2C lock)."""
    out = pool_led.ups_cli("get", key).strip()
    try:
        return int(out.split("=")[-1]) if out else None
    except ValueError:
        return None


_VIN_THRESHOLD = None


def ups_power() -> dict:
    """VIN/VBAT/VOUT (mV) and IOUT (mA) → watts and whether we run on the battery."""
    v = LockedPowerLog.read() or {}
    if not v:
        return {}
    global _VIN_THRESHOLD
    if _VIN_THRESHOLD is None:  # a config register: read once per run, not every set
        _VIN_THRESHOLD = ups_get("VIN_THRESHOLD") or 4498
    thr = _VIN_THRESHOLD
    return {"vin_mv": v["VIN"], "vbat_mv": v["VBAT"], "iout_ma": v["IOUT"],
            "w_out": round(v["VOUT"] * v["IOUT"] / 1e6, 3), "on_battery": v["VIN"] < thr}


def cma_free_mb() -> int | None:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("CmaFree:"):
            return int(line.split()[1]) // 1024
    return None


def git_sha() -> str:
    """The deployed revision: git if this is a checkout, else the REVISION file the rsync
    deploy writes (``git rev-parse --short HEAD`` on the Mac + ``-dirty`` if uncommitted)."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return sha or _read(str(ROOT / "REVISION"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fsync_path(path: Path) -> None:
    """Flush one file's or directory's data and metadata to the SD card. The rig is switched off
    by a physical switch (a hard power cut, Nick 2026-10-13): without this a "completed" set can
    come back as zero-length files after ext4 replays its journal."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_durable(path: Path, text: str) -> None:
    """Write via a temp file + fsync + rename + directory fsync: after a hard cut the file is
    either the old or the new content, never empty or half-written."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    fsync_path(path.parent)


def atomic_json(path: Path, data: dict) -> None:
    write_durable(path, json.dumps(data, indent=1) + "\n")


# -- run setup ---------------------------------------------------------------------------------
def arm_clock() -> dict | None:
    """The newest arm-time clock anchor (results/pool/arm_clock_*.json, written by `pool_mode.sh
    arm` while NTP is up): Pi epoch vs the UPS RTC. In the pool (no NTP) the Mac caption tool
    uses it to turn each set's clock.ups_rtc_epoch into real time. None if absent/unreadable."""
    for f in sorted(POOL_ROOT.glob("arm_clock_*.json"), reverse=True):
        try:
            d = json.loads(f.read_text())
            return {**d, "file": f.name} if isinstance(d, dict) else None
        except (OSError, ValueError):
            continue
    return None


def next_run_id() -> str:
    """pool<NNN>_<Pi UTC>: NNN is a counter on disk, so runs order correctly even when the Pi
    clock is wrong after a power-off."""
    POOL_ROOT.mkdir(parents=True, exist_ok=True)
    counter = POOL_ROOT / ".run_counter"
    try:
        n = int(_read(str(counter)) or 0)
    except ValueError:  # garbled by a hard cut: fall back to the folders on disk
        n = 0
    # Never reuse a number, even if the counter was lost: start past every existing run folder.
    for d in POOL_ROOT.glob("pool[0-9][0-9][0-9]*"):
        try:
            n = max(n, int(d.name[4:7]))
        except ValueError:
            pass
    n += 1
    write_durable(counter, f"{n}\n")
    return f"pool{n:03d}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def write_run_config(run_dir: Path, args) -> Path:
    """This run's own rig.yaml + profile copies (what was applied, for the manifest)."""
    import yaml

    sys.path.insert(0, str(ROOT / "src"))
    from nereus_camera_test_rig import config as config_mod

    cfg = config_mod.load_rig_config(ROOT / args.config)
    cfg.setdefault("rig", {})["results_directory"] = str(run_dir)
    prof_dir = run_dir / "profiles"
    prof_dir.mkdir()
    for name, cam in cfg.get("cameras", {}).items():
        src = ROOT / cam["profile"]
        profile = yaml.safe_load(src.read_text()) or {}
        if name == "imx708":
            focus = profile.setdefault("camera_controls", {}).setdefault("focus", {})
            if args.lens_position == "auto":
                focus.update({"mode": "auto", "lens_position": None})
            else:
                focus.update({"mode": "manual", "lens_position": float(args.lens_position)})
        dst = prof_dir / f"{name}.yaml"
        dst.write_text(f"# copied from {cam['profile']} by pool_capture.py; focus per "
                       f"--lens-position {args.lens_position}\n"
                       + yaml.safe_dump(profile, sort_keys=False))
        cam["profile"] = str(dst)
    run_config = run_dir / "rig.yaml"
    run_config.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return run_config


def storage_check(run_dir: Path, args) -> dict:
    planned_sets = int(args.hours * 3600 / max(args.plan_set_s, args.interval_s, 1)) + 1
    if args.max_sets:
        planned_sets = min(planned_sets, args.max_sets)
    need_gb = planned_sets * SET_MB_PLAN / 1024 + args.reserve_gb
    free_gb = shutil.disk_usage(run_dir).free / 1e9
    return {"planned_sets": planned_sets, "plan_set_s": args.plan_set_s,
            "set_mb_plan": SET_MB_PLAN, "need_gb": round(need_gb, 2),
            "free_gb": round(free_gb, 2), "reserve_gb": args.reserve_gb,
            "ok": free_gb >= need_gb}


# -- one set -------------------------------------------------------------------------------------
_APPLIED_KEYS = ("ExposureTime", "AnalogueGain", "DigitalGain", "ColourGains",
                 "ColourTemperature", "LensPosition", "AfState", "Lux", "SensorTemperature",
                 "exposure_us", "gain_db", "framesize", "pixel_format", "jpeg_quality",
                 "cfa", "bits", "metered", "isp_rgb_gain_db", "mount_rotation_deg")


def summarize_set(exp_dir: Path) -> dict:
    """Per camera: still + RAW status, files, bytes, sha256 and the settings the camera reported
    (experiment.json is the record; paths in it are absolute Pi paths)."""
    rec = json.loads((exp_dir / "experiment.json").read_text())
    out: dict = {}
    for key, kind in (("captures", "still"), ("raw_captures", "raw")):
        for c in rec.get(key, []):
            cam = c.get("camera") or {}
            name = {"imx708": "imx708", "n6": "openmv_n6", "ae3": "openmv_ae3"}.get(
                cam.get("board") or cam.get("sensor") or "", cam.get("board") or "?")
            meta = c.get("sensor_metadata") or {}
            entry = {"status": c.get("status"), "error": (c.get("error") or {}).get("code"),
                     "seconds": round(c.get("duration_seconds") or 0, 1),
                     "file": Path(c["output_path"]).name if c.get("output_path") else None,
                     "bytes": c.get("size_bytes"), "sha256": c.get("sha256"),
                     "applied": {k: meta[k] for k in _APPLIED_KEYS if k in meta}}
            if meta.get("jpeg_path"):
                entry["jpeg"] = Path(meta["jpeg_path"]).name
            if cam.get("firmware"):
                entry["firmware"] = cam["firmware"]
            out.setdefault(name, {})[kind] = entry
    return out


def write_sha256sums(exp_dir: Path) -> int:
    """sha256 of every file in the set (incl. the IMX708 JPEG sibling the record has no hash
    for) → SHA256SUMS in ``sha256sum -c`` format, relative paths. Returns the byte total."""
    lines, total = [], 0
    for p in sorted(exp_dir.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            lines.append(f"{sha256(p)}  {p.relative_to(exp_dir)}")
            total += p.stat().st_size
            fsync_path(p)  # hard-cut safe: the bytes we just hashed are on the card
    for d in sorted({p.parent for p in exp_dir.rglob("*")} | {exp_dir, exp_dir.parent}):
        fsync_path(d)
    write_durable(exp_dir / "SHA256SUMS", "\n".join(lines) + "\n")
    return total


def wait_for_boards(timeout_s: float) -> dict:
    """Give a board that is re-enumerating (after its own reset) a moment to come back."""
    deadline = time.monotonic() + timeout_s
    while True:
        by_id = Path("/dev/serial/by-id")
        names = [p.name for p in by_id.iterdir()] if by_id.is_dir() else []
        present = {cam: any(s in n for n in names) for cam, s in SERIALS.items()}
        if all(present.values()) or time.monotonic() > deadline:
            return present
        time.sleep(2)


def read_depth(ref_mbar: float | None) -> dict:
    """One fail-soft MS5837 reading (scripts/depth_ms5837.py in a subprocess with a timeout): a
    missing sensor, bus error or hang becomes {"ok": false, "error": ...} and nothing else."""
    # 1 sample: one short session (reset, PROM, 2 conversions); the >= 3 s gap between sessions
    # is enforced inside the script (pool_led.bar30_session).
    cmd = [DEPTH_PY, str(ROOT / "scripts" / "depth_ms5837.py"), "--samples", "1"]
    if ref_mbar:
        cmd += ["--ref-mbar", f"{ref_mbar:.2f}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=DEPTH_TIMEOUT_S)
        d = json.loads(out.stdout.strip().splitlines()[-1])
        if not isinstance(d, dict):
            raise ValueError(f"not a JSON object: {d!r}"[:80])
        if d.get("ok") and not isinstance(d.get("pressure_mbar"), (int, float)):
            raise ValueError("ok reading without a numeric pressure_mbar")
        return d
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def surface_ref() -> dict:
    """The surface pressure recorded by ``pool_mode.sh arm`` (results/pool/surface_ref.json)."""
    try:
        ref = json.loads((POOL_ROOT / "surface_ref.json").read_text())
        return ref if ref.get("ok") else {}
    except (OSError, ValueError):
        return {}


def depth_text(d: dict) -> str:
    """Health-line field: 'depth 1.23m 18.5C' (vs the arm-time surface), else the run-start ref."""
    if not d:
        return ""
    if not d.get("ok"):
        return " · depth ERR"
    m = d.get("depth_m", d.get("depth_m_run_start"))
    return (f" · depth {m:+.2f}m" if m is not None else f" · {d['pressure_mbar']:.0f}mbar") \
        + f" {d['temp_c']:.1f}C"


def cameras_for_set(n: int, fails_in_row: dict, retry_every: int) -> tuple[list, list]:
    """Backoff: a board that failed ``BENCH_AFTER`` sets in a row is only retried every
    ``retry_every`` sets, so a dead board (each failure costs ~60-130 s of USB timeouts) cannot
    stall the IMX708's cadence. The IMX708 is always captured. Returns (cameras, benched)."""
    benched = [c for c in CAMERAS[1:] if fails_in_row.get(c, 0) >= BENCH_AFTER
               and n % retry_every != 0]
    return [c for c in CAMERAS if c not in benched], benched


def should_power_cycle(fails_in_row: dict, after: int, uptime_s: float,
                       min_uptime_s: float = 600) -> list:
    """The boards that justify a UPS power cycle now: an N6/AE3 that failed ``after`` attempted
    sets in a row (a wedged board — e.g. an AE3 that stops reading USB — cannot be recovered
    in software, but a power cycle clears it: verified 2026-10-13). Never in the first
    ``min_uptime_s`` of a boot, so a board that is simply absent cannot cause a cycle loop."""
    if after <= 0 or uptime_s < min_uptime_s:
        return []
    return [c for c in CAMERAS[1:] if fails_in_row.get(c, 0) >= after]


def ups_power_cycle(reason: str) -> int:
    """scripts/ups_power_cycle.sh --yes (clean poweroff, UPS wakes the rig after 1 min; the armed
    @reboot start then begins a new run). The loop's own daily cap is 2 (the script's is 3)."""
    env = {**os.environ, "MAX_PER_DAY": "2"}
    return subprocess.run(["bash", str(ROOT / "scripts" / "ups_power_cycle.sh"), "--yes",
                           "--wake-min", "1", "--tag", os.environ.get("POOL_UPS_TAG", "pool_auto"),
                           "--reason", reason], env=env,
                          capture_output=True, text=True, timeout=60).returncode


def run_one_set(n: int, run_dir: Path, run_config: Path, run_id: str,
                cameras: list) -> dict:
    before = set(run_dir.glob("*/exp_*"))
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}"}
    cmd = [sys.executable, "-m", "nereus_camera_test_rig.cli", "--config", str(run_config),
           "experiment", "--type", "pool", "--env", "pool", "--raw", "--no-analysis",
           "--cameras", ",".join(cameras), "--notes", f"pool_capture {run_id} set {n}"]
    line: dict = {}
    # Own process group: on a timeout the whole group is killed, so an rpicam-still or serial
    # grandchild cannot outlive the set and hold a camera into the next one (QE #97).
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=SET_TIMEOUT_S)
        line["exit_code"] = proc.returncode
        if proc.returncode != 0:  # 1 = a camera failed; a traceback lands here too
            line["stderr_tail"] = err.strip().splitlines()[-8:]
            line["stdout_tail"] = out.strip().splitlines()[-8:]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        line["exit_code"] = "timeout"
    new = sorted(set(run_dir.glob("*/exp_*")) - before)
    if new:
        exp = new[-1]
        line["experiment"] = str(exp.relative_to(run_dir))
        try:
            line["cameras"] = summarize_set(exp)
        except (OSError, ValueError, KeyError) as exc:
            line["record_error"] = repr(exc)
        line["set_bytes"] = write_sha256sums(exp)
    return line


def health_text(line: dict) -> str:
    """The one human line per set (pool.log). OK n/3 counts a camera only if still AND raw."""
    cams = line.get("cameras", {})
    ok = [c for c in CAMERAS if all((cams.get(c, {}).get(k) or {}).get("status") == "completed"
                                    for k in ("still", "raw"))]
    parts = []
    for c in CAMERAS:
        raw = (cams.get(c, {}).get("raw") or {})
        still = (cams.get(c, {}).get("still") or {})
        short = {"imx708": "imx", "openmv_n6": "n6", "openmv_ae3": "ae3"}[c]
        if c in line.get("benched", []):
            parts.append(f"{short} benched")
        elif c in ok:
            parts.append(f"{short} raw {raw['bytes'] / 1e6:.1f}MB jpg {still['bytes'] / 1e6:.2f}MB")
        else:
            st = [("ok" if r.get("status") == "completed" else r.get("error") or
                   r.get("status") or "missing") for r in (still, raw)]
            parts.append(f"{short} FAIL(still {st[0]}/raw {st[1]})")
    h, p = line["pi_after"], line.get("power", {})
    pw = (f" · ups {p['vin_mv'] / 1000:.2f}Vin {p['vbat_mv'] / 1000:.2f}Vbat {p['w_out']:.1f}W"
          f"{' BATT' if p.get('on_battery') else ''}" if p else " · ups -")
    up = int(line["clock"]["uptime_s"] or 0)
    return (f"[set {line['set']:03d} | run+{int(line['run_s']) // 60:3d}m | boot+{up // 60}m] "
            f"{'OK' if len(ok) == 3 else 'PARTIAL' if ok else 'FAIL'} {len(ok)}/3 · "
            + " · ".join(parts)
            + depth_text((line.get("depth") or {}).get("end") or {})
            + f" · {line['set_seconds']:.0f}s · cpu {h['cpu_temp_c']}C thr {h['throttled']} · "
              f"mem {h['mem_available_mb']}MB cma {line.get('cma_free_mb')}MB · "
              f"disk {h['disk_free_gb']}GB{pw}")


# -- the loop ------------------------------------------------------------------------------------
def _on_signal(signum, _frame):
    _stop_signal["name"] = signal.Signals(signum).name


def run(args) -> int:
    run_id = next_run_id()
    run_dir = POOL_ROOT / run_id
    run_dir.mkdir(parents=True)
    t_start = time.monotonic()
    run_config = write_run_config(run_dir, args)
    store = storage_check(run_dir, args)
    power0 = ups_power()
    manifest = {
        "run_id": run_id, "host": socket.gethostname(), "repo_sha": git_sha(),
        "boot_id": _read("/proc/sys/kernel/random/boot_id"), "start_clock": clocks(),
        "mac_time_at_start": args.mac_time or None, "started_by": args.started_by,
        "args": vars(args), "storage_check": store, "power_at_start": power0,
        "set_command": "nereus-rig experiment --type pool --env pool --raw --no-analysis",
        "camera_note": ("IMX708: auto still + rpicam-still --raw (DNG+JPEG, one exposure), "
                        "AE/AWB auto, focus per profiles/imx708.yaml. N6/AE3: board ISP JPEG HD "
                        "auto + 8-bit BGGR Bayer HD metered-then-locked; hard reset before each"),
        "sets": 0, "ok_counts": {c: 0 for c in CAMERAS}, "stop_reason": None,
        "arm_clock": arm_clock()}
    atomic_json(run_dir / "run_manifest.json", manifest)
    log_fh = (run_dir / "pool.log").open("a")

    def say(msg: str) -> None:
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    say(f"POOL run {run_id} -> {run_dir}")
    say(f"  host {manifest['host']} sha {manifest['repo_sha']} boot {manifest['boot_id'][:8]} "
        f"pi_utc {manifest['start_clock']['pi_utc']} mac {args.mac_time or '-'} "
        f"by {args.started_by}")
    say(f"  lens {args.lens_position} · hours {args.hours} · interval {args.interval_s}s · "
        f"storage need {store['need_gb']} GB of {store['free_gb']} GB free "
        f"({store['planned_sets']} sets planned)")
    if not store["ok"]:
        manifest["stop_reason"] = "refused_storage"
        atomic_json(run_dir / "run_manifest.json", manifest)
        say(f"REFUSED: not enough disk for {args.hours} h — free space or lower --hours")
        return 3
    if power0.get("on_battery") and power0.get("vbat_mv", 9999) < args.start_vbat_mv:
        manifest["stop_reason"] = "refused_battery"
        atomic_json(run_dir / "run_manifest.json", manifest)
        say(f"REFUSED: on battery at {power0['vbat_mv']} mV < {args.start_vbat_mv} — charge first")
        return 4

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    power = None
    if args.power_period > 0 and power0:
        power = LockedPowerLog(run_dir / "power.csv", args.power_period)
        power.start()
    led = pool_led.LedDriver()  # C (steady) until the first set says otherwise
    led.start()
    # A crash mid-pattern must not leave the LED dark (reads as "Pi off"): restore the firmware
    # default at interpreter exit if the normal stop below never ran (QE #101).
    atexit.register(lambda: None if led.stopped else led.stop())
    vbat_hist: list[int] = []
    end = t_start + args.hours * 3600
    n, fails_in_row, cycle_tried = 0, {c: 0 for c in CAMERAS}, False
    ref = surface_ref() if args.depth else {}
    ref_mbar, run0_mbar, depth_errors, ups_unknown = ref.get("pressure_mbar"), None, 0, 0
    manifest["depth"] = {"enabled": args.depth, "sensor": "MS5837-30BA (Bar30) at 0x76",
                         "surface_ref": ref or None, "rho_kg_m3": 997.0}
    stop = None
    while stop is None:
        if _stop_signal["name"]:
            stop = f"signal_{_stop_signal['name']}"
            break
        if (run_dir / "STOP").exists():
            stop = "stop_file"
            break
        if time.monotonic() >= end:
            stop = "duration"
            break
        if args.max_sets and n >= args.max_sets:
            stop = "max_sets"
            break
        if shutil.disk_usage(run_dir).free / 1e9 < args.reserve_gb + SET_MB_PLAN / 1024:
            stop = "disk_reserve"
            break
        p = ups_power()
        if not p:
            # Battery UNKNOWN (UPS unreadable: I2C bus held, e.g. by a wet Bar30, or the CLI
            # failed). Decision: keep capturing. A blind guard adds no new failure mode, since
            # the end of a session is already a hard cut (Nick's switch), and #98 makes that
            # lose at most the set in flight. Stopping would throw away the session.
            ups_unknown += 1
            say(f"  WARN: battery UNKNOWN — UPS unreadable ({ups_unknown} set(s) in a row); "
                "low-battery stop is blind, capture continues")
        else:
            ups_unknown = 0
        if p.get("on_battery") and p.get("vbat_mv", 9999) < args.min_vbat_mv:
            stop = "battery_low"
            break
        temp = pi_health(run_dir)["cpu_temp_c"] or 0
        if temp >= args.max_cpu_c:
            say(f"  thermal pause: cpu {temp} C >= {args.max_cpu_c} C, waiting 60 s")
            time.sleep(60)
            continue
        present = wait_for_boards(20)
        n += 1
        t0 = time.monotonic()
        cams_now, benched = cameras_for_set(n, fails_in_row, args.retry_every)
        line = {"set": n, "run_id": run_id, "clock": clocks(), "run_s": round(t0 - t_start, 1),
                "usb_before": present, "benched": benched, "pi_before": pi_health(run_dir)}
        read_now = args.depth and depth_errors < DEPTH_DISABLE_AFTER
        depth = {"start": read_depth(ref_mbar)} if read_now else {}
        line.update(run_one_set(n, run_dir, run_config, run_id, cams_now))
        if read_now:
            depth["end"] = read_depth(ref_mbar)
            for d in depth.values():
                if d.get("ok"):
                    run0_mbar = run0_mbar or d["pressure_mbar"]
                    d["depth_m_run_start"] = round((d["pressure_mbar"] - run0_mbar) * 100.0
                                                   / (997.0 * 9.80665), 3)
            depth_errors = 0 if all(d.get("ok") for d in depth.values()) else depth_errors + 1
            line["depth"] = depth
            manifest["depth"]["run_start_mbar"] = run0_mbar
            if depth_errors >= DEPTH_DISABLE_AFTER:
                manifest["depth"]["disabled_at_set"] = n
                say(f"  WARN: depth DISABLED for this run after {depth_errors} failed sets "
                    f"({(depth.get('end') or {}).get('error')}); the Bar30 shares the UPS bus")
        line["set_seconds"] = round(time.monotonic() - t0, 1)
        line["pi_after"] = pi_health(run_dir)
        line["cma_free_mb"] = cma_free_mb()
        line["power"] = ups_power()
        if power:
            line["power_window"] = power.window(t0, time.monotonic())
        with (run_dir / "sets.jsonl").open("a") as fh:
            fh.write(json.dumps(line) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        cams = line.get("cameras", {})
        good = [c for c in CAMERAS if all((cams.get(c, {}).get(k) or {}).get("status")
                                          == "completed" for k in ("still", "raw"))]
        for c in good:
            manifest["ok_counts"][c] += 1
        for c in cams_now:
            fails_in_row[c] = 0 if c in good else fails_in_row[c] + 1
        manifest["fails_in_row"] = dict(fails_in_row)
        manifest.update({"sets": n, "last_set": {"set": n, "clock": line["clock"],
                                                 "ok": good, "seconds": line["set_seconds"]}})
        atomic_json(run_dir / "run_manifest.json", manifest)
        p_now = line["power"]
        if p_now.get("on_battery"):
            vbat_hist.append(p_now["vbat_mv"])
        else:
            vbat_hist.clear()
        led.set_mode(pool_led.decide(led.mode, vbat_hist, bool(p_now.get("on_battery")),
                                     len(good) == 3, args.low_vbat_mv))
        line["led"] = led.mode
        say(health_text(line))
        if line.get("exit_code") not in (0,) and line.get("stderr_tail"):
            say("    stderr: " + " | ".join(line["stderr_tail"][-3:])[:400])
        wedged = [] if cycle_tried else should_power_cycle(
            fails_in_row, args.ups_recover_after, line["clock"]["uptime_s"] or 0)
        if wedged:
            cycle_tried = True
            reason = f"pool_capture auto: {','.join(wedged)} failed {args.ups_recover_after}+ sets"
            say(f"UPS POWER CYCLE requested ({reason}); the armed boot-start resumes capture")
            manifest.update({"stop_reason": "ups_power_cycle", "end_clock": clocks()})
            atomic_json(run_dir / "run_manifest.json", manifest)
            os.sync()
            rc = ups_power_cycle(reason)
            if rc == 0:
                stop = "ups_power_cycle"
                break
            manifest["stop_reason"] = None
            say(f"  UPS power cycle refused (rc={rc}, see ~/ups_power_cycle.log); continuing")
        wait = max(args.min_gap_s, args.interval_s - (time.monotonic() - t0))
        while wait > 0 and not (run_dir / "STOP").exists() and not _stop_signal["name"]:
            time.sleep(min(wait, 1))
            wait -= 1
    if power:
        power.stop.set()
        power.join(timeout=10)
    manifest.update({"stop_reason": stop, "end_clock": clocks(),
                     "run_seconds": round(time.monotonic() - t_start, 1)})
    atomic_json(run_dir / "run_manifest.json", manifest)
    say(f"POOL run {run_id} stopped: {stop} after {n} sets "
        f"({manifest['ok_counts']} OK) in {manifest['run_seconds']:.0f} s")
    # Back to the firmware's steady-on (Nick's spec), except a battery stop keeps B showing:
    # the truest signal, and the firmware takes over the LED at shutdown anyway.
    led.stop(final=pool_led.LED_FLASHING if stop == "battery_low" else pool_led.FIRMWARE_DEFAULT)
    os.sync()
    return 0


def read_sets(run_dir: Path) -> tuple[list, list]:
    """sets.jsonl → (parsed lines, raw lines that did not parse). A switch-off (hard cut) can
    leave the last line torn; it is reported, never fatal."""
    good, torn = [], []
    path = run_dir / "sets.jsonl"
    for raw in (path.read_text(errors="replace").splitlines() if path.is_file() else []):
        if not raw.strip():
            continue
        try:
            good.append(json.loads(raw))
        except ValueError:
            torn.append(raw[:80])
    return good, torn


def summary(args) -> int:
    run_dir = Path(args.run_dir)
    lines, torn = read_sets(run_dir)
    try:
        man = json.loads((run_dir / "run_manifest.json").read_text())
    except (OSError, ValueError):
        man = {"run_id": run_dir.name}
    print(f"{man.get('run_id', run_dir.name)}: {len(lines)} sets, stop "
          f"{man.get('stop_reason') or 'none recorded (switched off / cut?)'}"
          + (f"; {len(torn)} torn line(s) skipped (hard cut mid-write)" if torn else ""))
    if not lines:
        return 0
    secs = sorted(ln["set_seconds"] for ln in lines)
    if secs:
        print(f"  set seconds: median {secs[len(secs) // 2]}, min {secs[0]}, max {secs[-1]}")
    for cam in CAMERAS:
        for kind in ("still", "raw"):
            rows = [(ln["set"], (ln.get("cameras", {}).get(cam) or {}).get(kind)) for ln in lines]
            bad = [(s, (r or {}).get("error") or "missing") for s, r in rows
                   if not r or r["status"] != "completed"]
            sizes = sorted(r["bytes"] for _, r in rows if r and r["status"] == "completed")
            print(f"  {cam:10s} {kind:5s} {len(rows) - len(bad)}/{len(rows)} ok"
                  + (f", median {sizes[len(sizes) // 2] / 1e6:.2f} MB" if sizes else "")
                  + (f"; failed {bad[:6]}" if bad else ""))
    temps = [ln["pi_after"]["cpu_temp_c"] for ln in lines if ln["pi_after"]["cpu_temp_c"]]
    if temps:
        print(f"  cpu {min(temps)}-{max(temps)} C; throttled "
              f"{sorted({ln['pi_after']['throttled'] for ln in lines})}; mem min "
              f"{min(ln['pi_after']['mem_available_mb'] for ln in lines)} MB; cma min "
              f"{min((ln.get('cma_free_mb') or 0) for ln in lines)} MB; disk free "
              f"{lines[-1]['pi_after']['disk_free_gb']} GB")
    ws = [ln["power_window"]["mean_w"] for ln in lines if ln.get("power_window", {}).get("n")]
    if ws:
        print(f"  power during sets: median {sorted(ws)[len(ws) // 2]:.2f} W, "
              f"max {max(ws):.2f} W; vbat last {lines[-1].get('power', {}).get('vbat_mv')} mV")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--hours", type=float, default=8.0, help="duration limit (default 8)")
    r.add_argument("--interval-s", type=float, default=0.0,
                   help="seconds from one set start to the next (0 = back to back)")
    r.add_argument("--min-gap-s", type=float, default=3.0, help="pause between sets")
    r.add_argument("--max-sets", type=int, default=0, help="stop after N sets (0 = no limit)")
    r.add_argument("--retry-every", type=int, default=5,
                   help="a board benched after 2 failed sets in a row is retried every N sets")
    r.add_argument("--lens-position", default="0.8",
                   help="IMX708 manual lens position in dioptres, or 'auto' (AF per capture)")
    r.add_argument("--mac-time", default="", help="the Mac's clock when the run was started")
    r.add_argument("--started-by", default="manual", help="manual | boot (pool_mode.sh)")
    r.add_argument("--config", default="configs/rig.example.yaml")
    r.add_argument("--reserve-gb", type=float, default=2.0, help="disk never used")
    r.add_argument("--plan-set-s", type=float, default=40.0,
                   help="planning set time for the storage check (overnight soak median 41 s)")
    r.add_argument("--start-vbat-mv", type=int, default=3250,
                   help="refuse to start on battery below this (dive-autostart's guard)")
    r.add_argument("--min-vbat-mv", type=int, default=3050,
                   help="stop cleanly on battery below this (UPS shuts down at 2950)")
    r.add_argument("--max-cpu-c", type=float, default=80.0, help="pause sets at/above this")
    r.add_argument("--low-vbat-mv", type=int, default=pool_led.LOW_VBAT_MV,
                   help="LED B (low battery): on battery and the median of the last 3 set "
                        "readings below this (default 3200: ~1 h left at ~2.85 W, Nick's "
                        "LiFePO4 curve)")
    r.add_argument("--ups-recover-after", type=int, default=3,
                   help="UPS power cycle when an N6/AE3 failed this many attempted sets in a "
                        "row (0 = off; max one per run, two per day, never in the first 10 min)")
    r.add_argument("--power-period", type=float, default=10.0,
                   help="UPS log period s (0 = off); 4 registers per sample")
    r.add_argument("--no-depth", dest="depth", action="store_false",
                   help="skip the MS5837 depth/temperature read at the start and end of each set")
    s = sub.add_parser("summary")
    s.add_argument("run_dir")
    args = ap.parse_args()
    if args.cmd == "run" and args.lens_position != "auto":
        lp = float(args.lens_position)
        if not 0.0 <= lp <= 15.0:
            ap.error("--lens-position must be 0..15 dioptres or 'auto'")
    return run(args) if args.cmd == "run" else summary(args)


if __name__ == "__main__":
    sys.exit(main())
