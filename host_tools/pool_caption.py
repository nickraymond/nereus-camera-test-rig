"""Caption bars for pool_render outputs (Mac side): a black bar APPENDED below a copy of an image.

Purpose
    Make every pool render readable on its own (phone, AirDrop, chat): capture time in Pacific,
    depth, water temperature, camera, image size and source file, in a black bar added BELOW
    the image so no image pixel is covered. Originals are never touched; callers write the
    captioned array as a separate ``<stem>_captioned.png|.jpg`` file.

Inputs (per run folder, read only; every value is "n/a" when its source is absent)
    run_manifest.json   ``mac_time_at_start`` + ``start_clock.pi_utc`` -> Mac anchor (optional)
    sets.jsonl          one JSON object per set; matched by ``experiment`` (set folder relative
                        to the run, e.g. "2026-10-13/exp_20261013T222549Z_pool"). Lines that do
                        not parse are skipped (the last line can be torn by a power cut).
    <set>/experiment.json  ``timestamp`` (time fallback 1); the ``exp_<UTC>`` folder name is
                        fallback 2.

Values
    time    ``clock.pi_utc`` of the set's line (Pi wall clock; no NTP in the pool). With a
            Mac anchor the run's offset (mac_time_at_start - start_clock.pi_utc) is added and
            the time is labelled "(Mac-anchored)", else "(Pi clock)". Shown in
            America/Los_Angeles with PDT/PST and AM/PM.
    depth   mean of the ``ok`` start/end Bar30 readings: ``depth_m`` (vs the arm-time surface)
            if present, else ``depth_m_run_start`` (labelled "vs run start"). Fresh-water
            pressure proxy (rho 997 kg/m3, computed on the Pi). No ``depth`` or no ok reading
            -> "n/a" (older runs, e.g. pool002, have no depth at all).
    water   mean ``temp_c`` of the same ok readings, else "n/a".

Bar layout
    Font = Pillow's built-in scalable font (Aileron) at width/32 px (min 14 px), shrunk to no
    less than width/36 when that keeps the text to two lines; so viewed fit-to-width on a
    ~400 px phone screen the text is ~11-12.5 px. Line 1 = time, depth, water; line 2 = camera,
    W x H px, file. A field wider than the bar on its own is word-wrapped; a single word (a
    file name) still too wide is middle-ellipsized ("…") so nothing is ever cut off.

Known limitations
    - 8-bit images only (16-bit TIFF renders are data products and stay uncaptioned).
    - Very narrow images (< ~450 px) get 3+ lines or ellipsized fields.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np

PACIFIC = ZoneInfo("America/Los_Angeles")
CAMERA_LABEL = {"imx708": "IMX708", "openmv_n6": "N6", "openmv_ae3": "AE3"}
FONT_DIV_MAX, FONT_DIV_MIN, MIN_FONT_PX = 32, 36, 14  # font px = width / div
SEP = " · "
ELLIPSIS = "…"
TARGET_LINES = 2

Warn = Callable[[str], None]


def _quiet(_msg: str) -> None:
    pass


# --- run + set metadata ---------------------------------------------------------------------

@dataclass
class RunMeta:
    """What one run folder says about time and depth (see the module docstring)."""
    run: Path
    sets: dict[str, dict] = field(default_factory=dict)  # experiment -> sets.jsonl line
    offset: Optional[timedelta] = None                   # mac - pi; None = no Mac anchor
    rtc_offset_s: Optional[float] = None                 # pi - UPS RTC at arm time (NTP up)
    bad_lines: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def basis(self) -> str:
        if self.offset is not None:
            return "Mac-anchored"
        return "UPS-RTC-anchored" if self.rtc_offset_s is not None else "Pi clock, unanchored"

    def summary(self) -> dict:
        return {"time_basis": self.basis,
                "mac_minus_pi_s": None if self.offset is None else self.offset.total_seconds(),
                "pi_minus_rtc_at_arm_s": self.rtc_offset_s,
                "sets_jsonl_lines": len(self.sets), "sets_jsonl_bad_lines": self.bad_lines,
                "sets_with_depth": sum(isinstance(v.get("depth"), dict)
                                       for v in self.sets.values()),
                "notes": self.notes}


@dataclass(frozen=True)
class SetCaption:
    time: str    # "2026-10-13 3:25:49 PM PDT (Pi clock)"
    depth: str   # "depth 1.23 m" | "depth 0.40 m vs run start" | "depth n/a"
    water: str   # "water 20.1 °C" | "water n/a"


def parse_utc(text) -> Optional[datetime]:
    """ISO 8601 with an offset or Z, or compact ``YYYYmmddTHHMMSSZ`` -> aware UTC; else None.
    A naive ISO time is ambiguous (which zone?) and returns None."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", s):
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def read_sets_jsonl(path: Path) -> tuple[dict[str, dict], int]:
    """({experiment: line}, count of lines that do not parse). Valid JSON without an
    ``experiment`` (not a set line) is ignored."""
    sets, bad = {}, 0
    for raw in path.read_text(errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            bad += 1
            continue
        if isinstance(line, dict) and isinstance(line.get("experiment"), str):
            sets[line["experiment"]] = line
    return sets, bad


def mac_offset(manifest: dict) -> tuple[Optional[timedelta], str]:
    """(mac - pi offset or None, note) from run_manifest.json."""
    mac_raw = manifest.get("mac_time_at_start")
    pi_raw = (manifest.get("start_clock") or {}).get("pi_utc")
    if not mac_raw:
        return None, "no mac_time_at_start: times are the Pi clock"
    mac, pi = parse_utc(mac_raw), parse_utc(pi_raw)
    if mac is None or pi is None:
        return None, (f"mac_time_at_start {mac_raw!r} / start_clock.pi_utc {pi_raw!r} not both "
                      "ISO with a UTC offset: times are the Pi clock")
    off = mac - pi
    return off, f"Mac-anchored: mac - pi = {off.total_seconds():+.0f} s"


def rtc_anchor(manifest: dict) -> tuple[Optional[float], str]:
    """(pi - UPS RTC seconds at arm time, note) from run_manifest.json ``arm_clock``. Valid only
    if NTP was synchronised when armed: then pi_epoch was real time and the offset turns the
    UPS RTC (which keeps running through power-offs) back into real time."""
    a = manifest.get("arm_clock")
    if not isinstance(a, dict):
        return None, "no arm_clock in the manifest: no UPS-RTC anchor"
    pi, rtc = a.get("pi_epoch"), a.get("ups_rtc_epoch")
    if a.get("ntp") != "yes" or not (_num(pi) and _num(rtc)):
        return None, f"arm_clock not usable (ntp={a.get('ntp')!r}, rtc={rtc!r}): no UPS-RTC anchor"
    return float(pi - rtc), f"UPS-RTC-anchored: pi - rtc at arm = {pi - rtc:+.0f} s"


def load_run(run: Path, warn: Warn = _quiet) -> RunMeta:
    """Read run_manifest.json + sets.jsonl (both optional) for captions."""
    meta = RunMeta(run)
    man_path = run / "run_manifest.json"
    manifest = {}
    if man_path.is_file():
        try:
            manifest = json.loads(man_path.read_text())
        except (OSError, ValueError) as exc:
            warn(f"{man_path}: unreadable ({exc}); caption times are the Pi clock")
    man = manifest if isinstance(manifest, dict) else {}
    meta.offset, note = mac_offset(man)
    meta.notes.append(note)
    meta.rtc_offset_s, note = rtc_anchor(man)
    meta.notes.append(note)
    sets_path = run / "sets.jsonl"
    if sets_path.is_file():
        meta.sets, meta.bad_lines = read_sets_jsonl(sets_path)
        if meta.bad_lines:
            warn(f"{sets_path}: skipped {meta.bad_lines} line(s) that do not parse "
                 "(torn last line after a power cut?)")
    else:
        meta.notes.append("no sets.jsonl: time from experiment.json / folder name, depth n/a")
        warn(f"{run.name}: no sets.jsonl; caption depth/water n/a, time from the set folder")
    return meta


def local_time_text(dt_utc: datetime) -> str:
    """'2026-10-13 3:25:49 PM PDT' in America/Los_Angeles."""
    d = dt_utc.astimezone(PACIFIC)
    return f"{d:%Y-%m-%d} {d.hour % 12 or 12}:{d:%M:%S %p %Z}"


def _experiment_timestamp(set_dir: Path) -> Optional[datetime]:
    try:
        return parse_utc(json.loads((set_dir / "experiment.json").read_text()).get("timestamp"))
    except (OSError, ValueError, AttributeError):
        return None


def set_time(meta: RunMeta, set_dir: Path, line: Optional[dict]) -> str:
    """Capture time text with its source label (see the module docstring)."""
    dt, via = parse_utc(((line or {}).get("clock") or {}).get("pi_utc")), ""
    if dt is None:
        dt, via = _experiment_timestamp(set_dir), " via experiment.json"
    if dt is None:
        m = re.match(r"^exp_(\d{8}T\d{6}Z)", set_dir.name)
        dt, via = (parse_utc(m.group(1)) if m else None), " via folder name"
    if dt is None:
        return "time n/a"
    if meta.offset is not None:
        dt += meta.offset
        return f"{local_time_text(dt)} (Mac-anchored{via})"
    rtc = ((line or {}).get("clock") or {}).get("ups_rtc_epoch")
    if meta.rtc_offset_s is not None and _num(rtc):
        real = datetime.fromtimestamp(rtc + meta.rtc_offset_s, tz=timezone.utc)
        return f"{local_time_text(real)} (UPS-RTC-anchored)"
    return f"{local_time_text(dt)} (Pi clock, unanchored{via})"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _mean2(vals: list[float]) -> str:
    """Mean to 2 decimals, never '-0.00'."""
    return f"{round(sum(vals) / len(vals), 2) or 0.0:.2f}"


def depth_fields(line: Optional[dict]) -> tuple[str, str]:
    """('depth 1.23 m' | 'depth 0.40 m vs run start' | 'depth n/a',
    'water 20.1 °C' | 'water n/a')."""
    depth = (line or {}).get("depth")
    if not isinstance(depth, dict):
        return "depth n/a", "water n/a"
    ok = [d for d in (depth.get("start"), depth.get("end")) if isinstance(d, dict) and d.get("ok")]
    above = [d["depth_m"] for d in ok if _num(d.get("depth_m"))]
    run0 = [d["depth_m_run_start"] for d in ok if _num(d.get("depth_m_run_start"))]
    if above:
        depth_txt = f"depth {_mean2(above)} m"
    elif run0:
        depth_txt = f"depth {_mean2(run0)} m vs run start"
    else:
        depth_txt = "depth n/a"
    temps = [d["temp_c"] for d in ok if _num(d.get("temp_c"))]
    water = f"water {sum(temps) / len(temps):.1f} °C" if temps else "water n/a"
    return depth_txt, water


def set_caption(meta: RunMeta, set_dir: Path, warn: Warn = _quiet) -> SetCaption:
    """Caption values for one set folder of ``meta.run``."""
    key = set_dir.relative_to(meta.run).as_posix()
    line = meta.sets.get(key)
    if line is None and meta.sets:
        warn(f"{set_dir.name}: no sets.jsonl line for {key} (torn last line?); caption time "
             "from experiment.json / folder name, depth n/a")
    depth, water = depth_fields(line)
    return SetCaption(set_time(meta, set_dir, line), depth, water)


def unknown_caption(why: str) -> SetCaption:
    """For images that do not come from this run (e.g. a Spotter unit's JPEG)."""
    return SetCaption(f"time n/a ({why})", "depth n/a", "water n/a")


# --- bar layout + drawing ----------------------------------------------------------------------

@lru_cache(maxsize=64)
def font(size: int):
    """Pillow's built-in scalable font. Fails loudly on a Pillow without one (< 10.1): the
    fixed 11 px bitmap fallback would not be readable on a phone."""
    import PIL
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError as exc:
        raise RuntimeError(f"caption font needs Pillow >= 10.1 (ImageFont.load_default(size=)); "
                           f"have Pillow {PIL.__version__}") from exc


def text_width(text: str, fnt) -> float:
    """Right edge of the drawn text: max of the advance and the ink box (italic overhang)."""
    return max(fnt.getlength(text), float(fnt.getbbox(text)[2]))


def middle_ellipsize(text: str, fits: Callable[[str], bool]) -> str:
    """Longest 'head…tail' of ``text`` that fits (keeps the extension); '…' if nothing fits."""
    lo, hi = 0, len(text) - 1  # number of original characters kept
    best = ELLIPSIS
    while lo <= hi:
        n = (lo + hi) // 2
        head, tail = text[:(n + 1) // 2], text[len(text) - n // 2:]
        cand = f"{head}{ELLIPSIS}{tail}"
        if fits(cand):
            best, lo = cand, n + 1
        else:
            hi = n - 1
    return best


def _wrap_group(fields: list[str], fits: Callable[[str], bool]) -> tuple[list[str], bool]:
    """Greedy pack fields joined by SEP; split a too-wide field at spaces; middle-ellipsize a
    too-wide word. Returns (lines, ellipsized)."""
    lines, cur, cut = [], "", False
    tokens: list[tuple[str, str]] = []  # (text, joiner before it)
    for f in fields:
        if fits(f):
            tokens.append((f, SEP))
            continue
        for i, word in enumerate(f.split(" ")):
            if not fits(word):
                word, cut = middle_ellipsize(word, fits), True
            tokens.append((word, SEP if i == 0 else " "))
    for text, joiner in tokens:
        cand = f"{cur}{joiner}{text}" if cur else text
        if fits(cand):
            cur = cand
        else:
            lines.append(cur)
            cur = text
    lines.append(cur)
    return [ln for ln in lines if ln], cut


def layout(groups: list[list[str]], width: int) -> tuple[int, int, list[str]]:
    """(font px, horizontal pad px, lines) so every line fits inside ``width``."""
    fs_max = max(MIN_FONT_PX, width // FONT_DIV_MAX)
    fs_min = min(fs_max, max(MIN_FONT_PX, width // FONT_DIV_MIN))

    def attempt(fs: int) -> tuple[int, list[str], bool]:
        fnt, pad = font(fs), max(2, round(0.35 * fs))
        room = width - 2 * pad

        def fits(s: str) -> bool:
            return text_width(s, fnt) <= room

        lines, cut = [], False
        for g in groups:
            g_lines, g_cut = _wrap_group([f for f in g if f], fits)
            lines += g_lines
            cut = cut or g_cut
        return pad, lines, cut

    def score(lines: list[str], cut: bool) -> tuple[bool, int]:
        return cut, max(len(lines), TARGET_LINES)  # lower is better; <= 2 lines all equal

    # A smaller font never needs more lines, so fs_min gives the best achievable score (2 uncut
    # lines when possible); return the BIGGEST font that reaches it (readability first).
    pad_min, lines_min, cut_min = attempt(fs_min)
    best = score(lines_min, cut_min)
    for fs in range(fs_max, fs_min, -1):
        pad, lines, cut = attempt(fs)
        if score(lines, cut) <= best:
            return fs, pad, lines
    return fs_min, pad_min, lines_min


def caption_fields(info: SetCaption, camera: str, w: int, h: int,
                   file_text: str) -> list[list[str]]:
    """Line 1 = time, depth, water; line 2 = camera, W x H, file (wrapped as needed)."""
    cam = CAMERA_LABEL.get(camera, camera)
    # plain "x": the built-in font (Aileron) has no U+00D7 glyph (renders as a box)
    return [[info.time, info.depth, info.water], [cam, f"{w}x{h} px", file_text]]


def caption_bar(width: int, groups: list[list[str]]) -> np.ndarray:
    """Black bar (H x width x 3, uint8, BGR) with the fields in white."""
    from PIL import Image, ImageDraw

    fs, pad, lines = layout(groups, width)
    fnt = font(fs)
    ascent, descent = fnt.getmetrics()
    line_h, pad_y = ascent + descent, max(2, round(0.3 * fs))
    bar = Image.new("RGB", (width, 2 * pad_y + line_h * len(lines)), (0, 0, 0))
    draw = ImageDraw.Draw(bar)
    for i, text in enumerate(lines):
        draw.text((pad, pad_y + i * line_h), text, fill=(255, 255, 255), font=fnt)
    return np.ascontiguousarray(np.asarray(bar)[..., ::-1])


def add_caption(img: np.ndarray, info: SetCaption, camera: str, file_text: str) -> np.ndarray:
    """New array = ``img`` (unchanged, full resolution) on top of a caption bar. The W x H shown
    is ``img``'s own size. 8-bit gray or BGR only."""
    if img.dtype != np.uint8 or img.ndim not in (2, 3):
        raise ValueError(f"caption needs an 8-bit gray/BGR image, got {img.dtype} {img.shape}")
    h, w = img.shape[:2]
    bar = caption_bar(w, caption_fields(info, camera, w, h, file_text))
    if img.ndim == 2:
        bar = bar[..., 1]
    elif img.shape[2] != 3:
        raise ValueError(f"caption needs 1 or 3 channels, got {img.shape}")
    return np.vstack([img, bar])
