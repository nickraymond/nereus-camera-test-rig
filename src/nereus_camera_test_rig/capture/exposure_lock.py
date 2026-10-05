"""Card-metered exposure lock for ``nereus-rig experiment --raw`` — pool spec §5.2.

``experiment --raw`` lets each camera choose its own exposure. For the pool test the card must be
bright and unclipped at every depth, so one metering step per depth locks exposure / gain for
all three cameras, and every capture set at that depth reuses the lock.

Exposure rule (Nick, 2026-10-05, "ISO 100"): the lowest analogue gain first, the shutter
lengthened up to a motion cap (default 1/60 s), and only then more gain — so the red channel
under water is not lifted with gain noise. Metering reuses the two proven recipes (CLAUDE.md
§3), each in its own process, with that cap passed in:

* IMX708: ``scripts/capture_raw_imx708.py --card … --stops 0`` — meter, probe RAW, card on the
  DNG, exposure scaled so the card's brightest channel lands on ``target``; locks shutter,
  analogue gain, AWB gains and lens position.
* OpenMV N6 / AE3: ``scripts/capture_raw_openmv.py --board … --serial … --card …`` — meter RAW,
  card, exposure at the gain floor, frame-time shortfall moved into gain; locks exposure_us and
  gain_db (the read-back values the sensor applied).

The result is one lock file (``exposure_lock.json``) with per-camera settings overrides that
``run_experiment(settings_overrides=…)`` deep-merges into each camera profile. A camera whose
metering failed (card not found, board error) gets no override and keeps auto exposure; the
lock file says why, and the experiment record carries the lock it ran with.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CARD = ROOT / "configs" / "cards" / "nereus_v1.yaml"
DEFAULT_TARGET = 0.80
DEFAULT_MAX_SHUTTER_US = 16667  # 1/60 s: Nick's motion limit example for the gain-priority rule
METER_TIMEOUT_S = 300


def merge_settings(base: dict[str, Any], override: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Deep-merge ``override`` into a copy of ``base`` (nested dicts merged, others replaced)."""
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_settings(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def imx708_override(summary: dict[str, Any]) -> dict[str, Any]:
    """Profile override from ``capture_raw_imx708.py``'s ``capture_raw.json``."""
    meter = summary.get("meter") or {}
    red, blue = (float(v) for v in meter["ColourGains"])
    controls: dict[str, Any] = {
        "exposure": {"shutter_us": int(summary["locked_exposure_us_at_stop0"]),
                     "analogue_gain": float(summary.get("locked_gain")
                                            or summary["gain_applied"])},
        "white_balance": {"red_gain": red, "blue_gain": blue},
    }
    if meter.get("LensPosition") is not None:
        controls["focus"] = {"mode": "manual", "lens_position": float(meter["LensPosition"])}
    return {"camera_controls": controls}


def openmv_override(summary: dict[str, Any]) -> dict[str, Any]:
    """Profile override from ``capture_raw_openmv.py``'s ``capture_raw.json`` (read-back values)."""
    locked = summary["locked"]
    return {"exposure_us": int(locked["exposure_us"]), "gain_db": float(locked["gain_db"])}


def _card_level(summary: dict[str, Any]) -> Optional[float]:
    if "locked" in summary:  # OpenMV
        return (summary["locked"].get("reference") or {}).get("level")
    for shot in summary.get("shots", []):  # IMX708: the stop-0 shot carries the card check
        if shot.get("card"):
            return shot["card"].get("level")
    return None


def _meter_command(name: str, camera_cfg: dict[str, Any], out: Path, card: Path,
                   target: float, python: str, max_shutter_us: int = 0
                   ) -> tuple[list[str], Path]:
    """(command, folder whose capture_raw.json to read) for one camera. ``max_shutter_us``
    turns on the gain-priority rule (lowest gain, shutter up to the cap, then gain)."""
    driver = camera_cfg.get("driver")
    if driver == "imx708":
        return ([python, str(ROOT / "scripts" / "capture_raw_imx708.py"), "--card", str(card),
                 "--target", str(target), "--stops", "0", "--out", str(out),
                 "--max-shutter-us", str(int(max_shutter_us or 0))], out)
    if driver == "openmv_usb":
        cmd = [python, str(ROOT / "scripts" / "capture_raw_openmv.py"),
               "--board", str(camera_cfg["board"]), "--card", str(card),
               "--target", str(target), "--out", str(out),
               "--max-exposure-us", str(int(max_shutter_us or 0))]
        if camera_cfg.get("serial_number"):
            cmd += ["--serial", str(camera_cfg["serial_number"])]
        return cmd, out
    raise ValueError(f"camera {name}: no metering recipe for driver {driver!r}")


def _find_summary(folder: Path) -> Optional[Path]:
    hits = sorted(folder.rglob("capture_raw.json"))
    return hits[-1] if hits else None


def meter_cameras(cameras_cfg: dict[str, dict[str, Any]], out_dir: Path, *,
                  card: Path = DEFAULT_CARD, target: float = DEFAULT_TARGET,
                  max_shutter_us: int = DEFAULT_MAX_SHUTTER_US,
                  python: str = sys.executable, timeout_s: float = METER_TIMEOUT_S,
                  log=print) -> dict[str, Any]:
    """Meter every camera in ``cameras_cfg`` (name → camera config) one after the other and
    return the lock dict (also written to ``out_dir/exposure_lock.json``). Never raises for one
    camera's failure: that camera is recorded as not locked and keeps auto exposure."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)  # never overwrite a prior metering
    lock: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card": str(card), "target": target, "folder": str(out_dir),
        "rule": ("gain priority: lowest analogue gain, shutter up to max_shutter_us, then gain"
                 if max_shutter_us else "gain floor, shutter only (no cap)"),
        "max_shutter_us": max_shutter_us or None, "cameras": {}}
    for name, cfg in cameras_cfg.items():
        entry: dict[str, Any] = {"locked": False}
        t0 = time.monotonic()
        try:
            cmd, folder = _meter_command(name, cfg, out_dir / name, card, target, python,
                                         max_shutter_us)
            shown = " ".join(Path(c).name if c.endswith(".py") else c for c in cmd[1:])
            log(f"[meter] {name}: {shown}")
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  timeout=timeout_s)
            entry["exit_code"] = proc.returncode
            entry["stdout_tail"] = proc.stdout.strip().splitlines()[-4:]
            summary_path = _find_summary(folder)
            if summary_path is None:
                entry["reason"] = "no capture_raw.json (metering did not finish)"
                entry["stderr_tail"] = proc.stderr.strip().splitlines()[-6:]
            else:
                summary = json.loads(summary_path.read_text())
                entry["summary"] = str(summary_path.relative_to(out_dir))
                entry["passed"] = bool(summary.get("passed"))
                entry["card_level"] = _card_level(summary)
                entry["gain_priority"] = summary.get("gain_priority")
                if summary.get("error"):
                    entry["reason"] = summary["error"]
                else:
                    entry["settings"] = (imx708_override(summary) if cfg.get("driver") == "imx708"
                                         else openmv_override(summary))
                    entry["locked"] = True
                    if not entry["passed"]:
                        entry["reason"] = "locked, but a metering check failed (see summary)"
        except subprocess.TimeoutExpired:
            entry["reason"] = f"metering timed out after {timeout_s:.0f} s"
        except (KeyError, ValueError, OSError) as exc:
            entry["reason"] = f"{type(exc).__name__}: {exc}"
        entry["seconds"] = round(time.monotonic() - t0, 1)
        lock["cameras"][name] = entry
        state = "locked" if entry["locked"] else "NOT locked (auto exposure)"
        level = entry.get("card_level")
        log(f"[meter] {name}: {state}" + (f", card {level:.3f}" if level is not None else "")
            + (f" — {entry['reason']}" if entry.get("reason") else "") + f" ({entry['seconds']} s)")
    (out_dir / "exposure_lock.json").write_text(json.dumps(lock, indent=1) + "\n")
    return lock


def load_lock(path: str | Path) -> dict[str, Any]:
    lock = json.loads(Path(path).read_text())
    if "cameras" not in lock:
        raise ValueError(f"{path}: not an exposure lock (no 'cameras')")
    return lock


def overrides_from_lock(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Camera name → settings override, for the cameras that locked."""
    return {name: e["settings"] for name, e in lock.get("cameras", {}).items()
            if e.get("locked") and e.get("settings")}
