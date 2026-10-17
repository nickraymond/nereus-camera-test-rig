"""Summarise the B3a encode ladder (s28_b3a_ladder.sh output) into ladder.csv, ON THE PI.

    python scripts/s28_b3a_ladder_summary.py <sweep_dir> <ladder_dir>

Per frame: rc, attempts, distance, bytes, messages, encode s (rc_raw_jxl's own), DNG read +
RGB prep s, cjxl VmPeak / VmHWM max (KiB, /proc samples every 0.1 s), CmaFree min (kB), the
capture time from the sweep log, the frame's Lux, and the predicted wake:
    9 s (process start -> capture, Sprint26 assumption) + capture + DNG read + prep + encode
    + (messages + 2 + 40 heal chunks) x 1.3 s       [wake_no_tail_s]
    + 150 s fixed listen tail                       [wake_with_tail_s]
Real units trim the tail to the budget (rc_command_hooks.py:389-396), so both are reported.
"""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
from pathlib import Path

PRE_S, PACE_S, HEAL, TAIL_S = 9.0, 1.3, 40, 150.0


def proc_stats(path: Path) -> tuple[int, int, int]:
    peak = hwm = 0
    cma = None
    for line in path.read_text().splitlines():
        for m in re.finditer(r"VmPeak: (\d+)", line):
            peak = max(peak, int(m.group(1)))
        for m in re.finditer(r"VmHWM: (\d+)", line):
            hwm = max(hwm, int(m.group(1)))
        m = re.search(r"CmaFree:\s+(\d+)", line)
        if m:
            cma = int(m.group(1)) if cma is None else min(cma, int(m.group(1)))
    return peak, hwm, cma if cma is not None else -1


def main() -> int:
    sweep, ladder = Path(sys.argv[1]), Path(sys.argv[2])
    cap_s = {}
    for line in (sweep / "log.txt").read_text().splitlines():
        m = re.search(r"slot (\d{4}) (\w+) OK (\d+) s", line)
        if m:
            cap_s[f"s{m.group(1)}_{m.group(2)}"] = float(m.group(3))
    rcs = {r["frame"]: int(r["rc"]) for r in csv.DictReader(open(ladder / "runs.csv"))}
    rows = []
    for stem in sorted(rcs):
        d = ladder / stem
        meta = json.loads((sweep / f"{stem}.json").read_text())
        peak, hwm, cma = proc_stats(d / "proc_samples.txt")
        row = {"frame": stem, "slot": stem[1:5], "profile": stem.split("_")[1],
               "lux": round(meta["Lux"], 2), "rc": rcs[stem], "attempts": "", "distance": "",
               "bytes": "", "messages": "", "encode_s": "", "dng_read_s": "", "rgb_prep_s": "",
               "vmpeak_kib": peak, "vmhwm_kib": hwm, "cmafree_min_kb": cma,
               "capture_s": cap_s.get(stem, ""), "rfb": "", "wake_no_tail_s": "",
               "wake_with_tail_s": ""}
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
        if rcs[stem] == 0 and res:
            log = res["attempt_log"]
            log = ast.literal_eval(log) if isinstance(log, str) else log
            tim = res["timings"]
            tim = ast.literal_eval(tim) if isinstance(tim, str) else tim
            # the attempt the search KEPT (the last one can be an over-cap probe)
            kept = [x for x in log if abs(x["distance"] - res["distance"]) < 1e-6
                    and not x.get("over_cap")] or [log[-1]]
            row.update(attempts=res["attempts"], distance=res["distance"], bytes=kept[-1]["bytes"],
                       messages=res["message_count"], encode_s=res["encode_s"],
                       dng_read_s=tim.get("dng_read_s"), rgb_prep_s=tim.get("rgb_prep_s"))
            if row["capture_s"] != "":
                w = (PRE_S + row["capture_s"] + tim.get("dng_read_s", 0) + tim.get("rgb_prep_s", 0)
                     + res["encode_s"] + (res["message_count"] + 2 + HEAL) * PACE_S)
                row.update(wake_no_tail_s=round(w, 1), wake_with_tail_s=round(w + TAIL_S, 1))
        else:
            row["rfb"] = res.get("rfb", "no result.json")
        rows.append(row)
    with open(ladder / "ladder.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print("wrote", ladder / "ladder.csv", len(rows), "rows;",
          sum(r["rc"] != 0 for r in rows), "failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
