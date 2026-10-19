"""The pre-label volume rule (PREREG Labels, amended 2026-10-18; SPEC_e6_prelabel_rfdetr rev 3
must do 4). Study-only code; never imported from src/.

    python -m edge_ml.urchin.volume timed-sheets <prelabels.json> <stills_root> <out_dir>
    python -m edge_ml.urchin.volume project <prelabels.json> <seconds> <out.json> [--start ISO]
    python -m edge_ml.urchin.volume floor <prelabels.json> <0.1> <out.json>

PREREG text: "The review rate is timed on 96 crops: every k-th candidate scoring >= 0.1 in
candidate_ids order, k = floor(number of candidates >= 0.1 / 96) ... Projected review time =
total candidates / that rate ... If the 0.05 projection ends after the deadline, the floor rises
to 0.1 for ALL stills; if the 0.1 projection also ends after it, the session stops and tells the
PE ... Decided from counts and time only, never from label content."

`timed-sheets` draws the 96 (ids 0, k, 2k, ... of the >= 0.1 list, numbered by their full-set
candidate id) onto 2 sheets of 48. Their review is timed as wall clock from the start of the
first sheet to the end of the last, in the same set-up as the full review. `project` turns that
time into a rate and both projections, and names the floor that runs. `floor` writes the
pre-labels with every detection below the floor removed, for `review.py candidate-sheets`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from edge_ml.urchin.review import candidate_ids, make_sheets

PT = ZoneInfo("America/Los_Angeles")
DEADLINE = datetime(2026, 10, 21, 12, 0, tzinfo=PT)  # candidate review ends (PREREG)
N_TIMED = 96
TIMED_FLOOR = 0.1
FLOORS = (0.05, 0.1)


def timed_ids(prelabels: dict, n: int = N_TIMED, floor: float = TIMED_FLOOR) -> list[int]:
    """Every k-th candidate scoring >= floor, in candidate_ids order, k = floor(N / n); the first
    n of those (all of them when N < n)."""
    pool = [cid for cid, _, _, score in candidate_ids(prelabels) if score >= floor]
    k = max(len(pool) // n, 1)
    return pool[::k][:n]


def count_at(prelabels: dict, floor: float) -> int:
    return sum(d["score"] >= floor for dets in prelabels["detections"].values() for d in dets)


def project(prelabels: dict, n_timed: int, seconds: float, start: datetime) -> dict:
    """Rate from the timed crops, both projections, and the floor the rule picks."""
    rate = n_timed / seconds * 3600  # crops per hour
    out = {"timed_crops": n_timed, "timed_seconds": round(seconds, 1),
           "rate_crops_per_h": round(rate, 1), "start": start.isoformat(),
           "deadline": DEADLINE.isoformat(), "projections": {}}  # fmt: skip
    for floor in FLOORS:
        n = count_at(prelabels, floor)
        end = start + timedelta(hours=n / rate)
        out["projections"][f"{floor:g}"] = {
            "candidates": n, "hours": round(n / rate, 2), "ends": end.isoformat(),
            "fits": end <= DEADLINE}  # fmt: skip
    p = out["projections"]
    if p["0.05"]["fits"]:
        out["floor"], out["decision"] = 0.05, "0.05 projection fits: floor stays 0.05"
    elif p["0.1"]["fits"]:
        out["floor"], out["decision"] = 0.1, "0.05 overruns: floor 0.1 for ALL stills"
    else:
        out["floor"], out["decision"] = None, "0.1 also overruns: STOP and tell the PE"
    return out


def apply_floor(prelabels: dict, floor: float) -> dict:
    if floor < prelabels.get("min_score", 0):
        raise ValueError(f"floor {floor} is below the run's min_score {prelabels['min_score']}")
    dets = {s: [d for d in v if d["score"] >= floor] for s, v in prelabels["detections"].items()}
    return prelabels | {"min_score": floor, "floor_from": prelabels.get("min_score"),
                        "detections": dets}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("timed-sheets")
    t.add_argument("prelabels", type=Path)
    t.add_argument("stills_root", type=Path)
    t.add_argument("out_dir", type=Path)
    p = sub.add_parser("project")
    p.add_argument("prelabels", type=Path)
    p.add_argument("seconds", type=float, help="wall clock of the timed review")
    p.add_argument("out", type=Path)
    p.add_argument("--start", help="review start, ISO time (default: now, Pacific)")
    f = sub.add_parser("floor")
    f.add_argument("prelabels", type=Path)
    f.add_argument("floor", type=float)
    f.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    pre = json.loads(args.prelabels.read_text())
    if args.cmd == "timed-sheets":
        ids = timed_ids(pre)
        n = make_sheets(pre, args.stills_root, args.out_dir, only=ids)
        (args.out_dir / "timed_ids.json").write_text(json.dumps(ids))
        print(f"{len(ids)} timed crops on {n} sheets → {args.out_dir}")
        return 0
    if args.cmd == "project":
        start = datetime.fromisoformat(args.start) if args.start else datetime.now(PT)
        res = project(pre, len(timed_ids(pre)), args.seconds, start)
        args.out.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
        return 0 if res["floor"] is not None else 2
    args.out.write_text(json.dumps(apply_floor(pre, args.floor), indent=1))
    print(f"floor {args.floor:g}: {count_at(pre, args.floor)} candidates → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
