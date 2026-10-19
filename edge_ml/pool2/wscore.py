"""Pool test 2 W1 / W2 / W5 verdict logic (docs/pool2_20261021/SPEC.md rev 12), pure functions.

Inputs are per-camera frame records built from the raw clips (tag pose, blind reference boxes,
detector output, sharpness); nothing here reads files. One frame record:

  {"t": capture time s (rev 5 clock), "range_m": tag-pose range or None (no tag decoded),
   "tags": tags decoded, "board_whole": the whole board inside this camera's frame (F11),
   "f10": flagged by the cross-camera range check, "mtf50_lpmm": slanted-edge MTF50 at the board,
   "samples": {"u80a" | "u80b" | "u50a" | "u50b": {"px": px across (reference box), "hit": the
               detector's IoU >= 0.5 hit, "human": the blind yes/no (fallback only)}}}

Spec rev 13 (PE 004, Sun 10/18 8:04 PM, sha256 2e5525246709; QE 007's rev 12 fixes):
  - the W1 chain starts at each camera's first bin (1.0 m unless the Wed card says a later one,
    where the whole board first fits); the 0.5 m bin is report-only;
  - frame pick: over the N candidates in time order, indices round(i (N - 1) / 4), i = 0..4, with
    round = half up (floor(x + 0.5)), not Python's half-to-even;
  - the tie-break MTF50 is the median over the 1.0 m bin's 5 W1 frames;
  - the detector fallback looks at each camera's first bin.
Readings pinned since rev 12 (PE 003):
  - "both 80 mm samples are >= 30 px across" holds in each of the bin's 5 frames;
  - hold frames = speed < 0.1 m/s from the tag pose; a bin with >= 5 of them picks its 5 evenly in
    time from them, otherwise from all its whole-board frames, in time order (F17);
  - each camera's chain end is reported with its reason: px / hits / frames (< 5 scorable) /
    tag-limited, the last only when the chain held through the farthest bin that has tag-decoded
    frames (F16).
"""

from __future__ import annotations

import math
import statistics

BIN_CENTRES = tuple(round(0.5 * k, 1) for k in range(1, 9))  # 0.5 ... 4.0 m
FIRST_BIN = 1.0        # rev 13 R2: the chain's first bin unless the Wed card names a later one
BIN_HALF_M = 0.25
N_PER_BIN = 5
PX_MIN = 30.0          # Nick, 10:45 AM: "30px for the 8cm target"; can only tighten after data
HITS_MIN = 4           # of N_PER_BIN
HOLD_SPEED_MS = 0.1
SIZES = {"80": ("u80a", "u80b"), "50": ("u50a", "u50b")}
F10_DISAGREE = 0.10    # cross-camera tag ranges at one instant
F10_PASS_SHARE = 0.25  # > this share flagged -> the pass's W1 range is "not measured"
F12_CHANGE = 0.20      # W2 check pass vs pass 1


def bin_of(range_m: float | None) -> float | None:
    """The 0.5 m bin centre a range falls in ([c - 0.25, c + 0.25)), or None."""
    if range_m is None:
        return None
    for c in BIN_CENTRES:
        if c - BIN_HALF_M <= range_m < c + BIN_HALF_M:
            return c
    return None


SPEED_MAX_DT_S = 1.0   # neighbours further apart in time (e.g. across a clip gap) aren't used


def _where(f: dict) -> list[float] | None:
    """A frame's board position for speed: the 3-D board centre (pos_m, m) when measured, else
    the range alone as a 1-D position."""
    if f.get("pos_m") is not None:
        return list(f["pos_m"])
    return [f["range_m"]] if f.get("range_m") is not None else None


def speeds(frames: list[dict]) -> list[float | None]:
    """Board speed per frame (m/s) from the pose change to its time neighbours (rev 13: "speed
    comes from the pose change between frames on the capture clock"): |d pos / dt| with pos the
    3-D board centre (measure.py pos_m), else |d range / dt|. A neighbour more than
    SPEED_MAX_DT_S away (a gap between clips) is not used; nor is one measured the other way."""
    pts = sorted(((f["t"], i, _where(f)) for i, f in enumerate(frames)
                  if _where(f) is not None), key=lambda p: (p[0], p[1]))
    out: list[float | None] = [None] * len(frames)
    for k, (t, i, p) in enumerate(pts):
        a = pts[k - 1] if k > 0 and t - pts[k - 1][0] <= SPEED_MAX_DT_S else pts[k]
        b = pts[k + 1] if k + 1 < len(pts) and pts[k + 1][0] - t <= SPEED_MAX_DT_S else pts[k]
        if b[0] > a[0] and len(a[2]) == len(b[2]):
            out[i] = math.dist(a[2], b[2]) / (b[0] - a[0])
    return out


def select_bin(frames: list[dict], centre: float, n: int = N_PER_BIN) -> list[dict] | None:
    """n frames evenly spaced in time among the bin's scorable frames (a range in the bin, the
    whole board in view, not F10-flagged); hold frames only when there are >= n of them (F17).
    None when fewer than n are scorable (the chain break)."""
    sp = speeds(frames)
    cand = [(f, s) for f, s in zip(frames, sp)
            if bin_of(f.get("range_m")) == centre and f.get("board_whole") and not f.get("f10")]
    held = [(f, s) for f, s in cand if s is not None and s < HOLD_SPEED_MS]
    pool = sorted((held if len(held) >= n else cand), key=lambda fs: fs[0]["t"])
    if len(pool) < n:
        return None
    idx = [math.floor(k * (len(pool) - 1) / (n - 1) + 0.5) for k in range(n)] if n > 1 else [0]
    return [pool[i][0] for i in idx]


def bin_sees(sel: list[dict], size: str = "80", key: str = "hit") -> dict:
    """W1 in one bin: both samples of ``size`` >= PX_MIN across in every frame, and each one hit
    (``key``: "hit" = detector, "human" = the blind fallback) in >= HITS_MIN of n."""
    why = []
    for name in SIZES[size]:
        px = [((f.get("samples") or {}).get(name) or {}).get("px") for f in sel]
        px_ok = all(p is not None and p >= PX_MIN for p in px)
        hits = sum(bool(((f.get("samples") or {}).get(name) or {}).get(key)) for f in sel)
        if not px_ok:
            why.append(f"{name}:px")
        if hits < HITS_MIN:
            why.append(f"{name}:hits {hits}/{len(sel)}")
    return {"ok": not why, "why": why}


def detectable_range(frames: list[dict], size: str = "80", key: str = "hit",
                     first_bin: float = FIRST_BIN, select=None) -> dict:
    """The largest bin d where bin_sees holds and holds in every closer bin from ``first_bin``
    (rev 13: 1.0 m, or the camera's first whole-board bin on the Wed card). Bins before it are
    scored and reported only. Reports where and why the chain ended: px / hits / frames /
    tag-limited (F16: the chain held through the farthest bin with tag-decoded frames).
    ``select(frames, centre)`` picks a bin's frames (default select_bin); the scoring driver
    passes the picks it committed in the box file, with the detector's hits on them."""
    select = select or select_bin
    bins, last, end = {}, None, None
    for c in [b for b in BIN_CENTRES if b < first_bin]:  # report-only
        sel = select(frames, c)
        bins[c] = ({**bin_sees(sel, size, key), "report_only": True,
                    "mtf50_lpmm": _median([f.get("mtf50_lpmm") for f in sel])} if sel
                   else {"ok": False, "why": ["frames<5"], "report_only": True})
    for c in [b for b in BIN_CENTRES if b >= first_bin]:
        sel = select(frames, c)
        if sel is None:
            bins[c] = {"ok": False, "why": ["frames<5"]}
            end = (c, "frames")
            break
        res = bin_sees(sel, size, key)
        res["mtf50_lpmm"] = _median([f.get("mtf50_lpmm") for f in sel])
        bins[c] = res
        if not res["ok"]:
            end = (c, "px" if any(w.endswith(":px") for w in res["why"]) else "hits")
            break
        last = c
    tagged = [b for b in (bin_of(f.get("range_m")) for f in frames) if b is not None]
    tag_limited = bool(end and end[1] == "frames" and last is not None and tagged
                       and last >= max(tagged))
    if tag_limited:
        end = (end[0], "tag-limited")
    return {"range_m": last, "tag_limited": tag_limited, "end_bin": end[0] if end else None,
            "end_reason": end[1] if end else None, "first_bin": first_bin, "bins": bins}


def detector_fallback(per_camera: dict[str, dict]) -> bool:
    """Rev 13: the detector is replaced by the blind yes/no when it hits < 4 of 5 in the chain's
    first bin on every camera (per_camera: detectable_range results). Reading (successor 3): a
    camera whose first bin has < 5 scorable frames can't show "hits in < 4 of 5", so it is left
    out; the fallback needs >= 1 camera with 5 first-bin frames and every such camera weak."""
    judged = []
    for res in per_camera.values():
        why = (res["bins"].get(res.get("first_bin", FIRST_BIN)) or {}).get("why", ["frames<5"])
        if "frames<5" not in why:
            judged.append(any(":hits" in w for w in why))
    return bool(judged) and all(judged)


def n6_vs_ae3(n6: dict, ae3: dict) -> str:
    """The longer detectable range wins; a tie goes to the higher MTF50 (lp/mm) in the 1.0 m bin."""
    r6, r3 = n6["range_m"] or 0.0, ae3["range_m"] or 0.0
    if r6 != r3:
        return "openmv_n6" if r6 > r3 else "openmv_ae3"
    m6 = (n6["bins"].get(1.0) or {}).get("mtf50_lpmm")
    m3 = (ae3["bins"].get(1.0) or {}).get("mtf50_lpmm")
    if m6 is None or m3 is None or m6 == m3:
        return "tie"
    return "openmv_n6" if m6 > m3 else "openmv_ae3"


def w2_secchi(frames: list[dict]) -> float | None:
    """W2 'camera Secchi': the range at the first frame (in time) where >= 1 tag decodes."""
    for f in sorted(frames, key=lambda f: f["t"]):
        if (f.get("tags") or 0) >= 1 and f.get("range_m") is not None:
            return f["range_m"]
    return None


def f12_water_changed(pass1: float | None, check: float | None) -> bool | None:
    """F12: the check pass's W2 range differs from pass 1's by > 20 %."""
    if not pass1 or check is None:
        return None
    return abs(check - pass1) / pass1 > F12_CHANGE


def f10_flags(ranges: dict[str, float | None]) -> bool:
    """F10 for one instant: the cameras' tag ranges disagree by > 10 % (max vs min, of the min)."""
    vals = [r for r in ranges.values() if r]
    return len(vals) >= 2 and (max(vals) - min(vals)) / min(vals) > F10_DISAGREE


def f10_pass_unmeasured(flags: list[bool]) -> bool:
    """F10: > 25 % of a pass's matched instants flagged -> its W1 range is 'not measured'."""
    return bool(flags) and sum(flags) / len(flags) > F10_PASS_SHARE


def _median(vals):
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None
