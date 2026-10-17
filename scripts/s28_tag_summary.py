# ruff: noqa: E501
"""Summarise tag_detect.csv (scripts/s28_tag_detect.py): tags-vs-Lux per arm and input,
detect time and peak RSS P50/P90, the lowest Lux with 4/4 tags, and the wake-time cost.

    python scripts/s28_tag_summary.py <run_dir>/tag_detect.csv
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

BANDS = [(0, 1), (1, 5), (5, 20), (20, 100), (100, 450), (450, 1e9)]


def pct(v, q):
    v = sorted(v)
    if not v:
        return float("nan")
    k = (len(v) - 1) * q
    f = int(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def main() -> int:
    rows = list(csv.DictReader(open(sys.argv[1])))
    for r in rows:  # one table per detector mode x input
        if r.get("mode"):
            r["input"] = f"{r['mode']} / {r['input']}"
    ok = [r for r in rows if r.get("tags") not in ("", None)]
    fails = [r for r in rows if r.get("tags") in ("", None)]
    print(f"{len(rows)} detections, {len(fails)} child failures"
          + (f" (e.g. {fails[0]['frame']} {fails[0]['input']}: {fails[0].get('error', '')})" if fails else ""))
    for inp in sorted({r["input"] for r in rows}):
        print(f"\n== input {inp}: tags found (mean of 0-4) / frames with 4/4, per arm x Lux band")
        arms = sorted({r["arm"] for r in rows})
        print("Lux band".ljust(12) + "".join(a.rjust(16) for a in arms))
        for lo, hi in BANDS:
            line = (f"{lo:g}-{hi:g}" if hi < 1e9 else f">={lo:g}").ljust(12)
            for a in arms:
                rs = [r for r in ok if r["input"] == inp and r["arm"] == a and lo <= float(r["lux"]) < hi]
                line += (f"{sum(int(r['tags']) for r in rs) / len(rs):.1f} ({sum(int(r['tags']) == 4 for r in rs)}/{len(rs)})"
                         if rs else "—").rjust(16)
            print(line)
        rs = [r for r in ok if r["input"] == inp]
        d = [float(r["detect_s"]) for r in rs]
        p = [float(r["prep_s"]) for r in rs]
        h = [int(r["vmhwm_kib"]) / 1024 for r in rs]
        imp = [float(r["import_s"]) for r in rs]
        full = [r for r in rs if int(r["tags"]) == 4]
        low = min(full, key=lambda r: float(r["lux"])) if full else None
        print(f"detect s P50/P90/max {pct(d, .5):.2f}/{pct(d, .9):.2f}/{max(d):.2f} · prep s P50/P90 "
              f"{pct(p, .5):.2f}/{pct(p, .9):.2f} · import s P50 {pct(imp, .5):.2f} · VmHWM MiB P50/P90/max "
              f"{pct(h, .5):.0f}/{pct(h, .9):.0f}/{max(h):.0f}")
        if low:
            print(f"lowest Lux with 4/4: {float(low['lux']):g} ({low['frame']}, {low['arm']})")
        never = sorted({r["frame"] for r in rs if int(r["tags"]) < 4})
        print(f"frames below 4/4: {len(never)} of {len(rs)}")
        per_frame = defaultdict(float)
        for r in rs:
            per_frame[r["frame"]] = float(r["prep_s"]) + float(r["detect_s"])
        print(f"wake-time cost estimate (prep + detect, in an already-running process): "
              f"P50 {pct(list(per_frame.values()), .5):.1f} s, P90 {pct(list(per_frame.values()), .9):.1f} s; "
              f"+ {pct(imp, .5):.1f} s once if numpy/OpenCV are imported only for this")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
