#!/usr/bin/env python3
"""selfcheck_bench.py — BENCH ONLY: the pre-registered --self-check fault-injection test
(docs/selfcheck_20261015/PREREG.md). Runs on the Pi; one CSV row per case.

  case <name> [--rep N]     inject (selfcheck_inject.py), start the loop with the boot path's
                            launcher (`pool_mode.sh start --self-check --max-sets 1`), wait for
                            it to end, score the run, clear the fault, re-probe both boards.
  inspect <run_dir> --case NAME --expect MODE
                            score an existing run (the cold-boot cases are driven from the Mac:
                            the Pi cannot survive its own power cycle).
Cases: ae3_unplugged ae3_wedged n6_unplugged n6_wedged imx_in_use n6_ae3_unplugged all_ok.

Scoring (PREREG bars 1-5): the verdict (run_manifest self_check -> LED mode) equals the expected
mode; pool.log has SELF-CHECK before "[set 001"; led_trace.csv bursts (writes split at gaps
>= 3.5 s) each hold exactly 2N alternating ON/OFF writes in code order (fault), or ON/OFF
writes ~2 s apart (OK); after clearing, both boards answer board_probe.
Output: results/selfcheck_bench/<bench_id>/cases.csv (+ bench.log).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
POOL = ROOT / "results" / "pool"
PIDFILE = Path.home() / ".pool_capture.pid"
OUT = ROOT / "results" / "selfcheck_bench"
INJ = [PY, str(ROOT / "scripts" / "selfcheck_inject.py")]
BOARDS = {"openmv_n6": "10003500025043364d343000", "openmv_ae3": "0829c14000000000"}

# name: (inject commands, clear commands, expected LED mode)
CASES = {
    "ae3_unplugged": ([["detach", "ae3"]], [["attach", "ae3"]], "fault:3"),
    "ae3_wedged": ([["repl", "ae3"]], [["reset", "ae3"]], "fault:3"),
    "n6_unplugged": ([["detach", "n6"]], [["attach", "n6"]], "fault:2"),
    "n6_wedged": ([["repl", "n6"]], [["reset", "n6"]], "fault:2"),
    "imx_in_use": ([["hold-imx"]], [["release-imx"]], "fault:1"),
    "n6_ae3_unplugged": ([["detach", "n6"], ["detach", "ae3"]],
                         [["attach", "n6"], ["attach", "ae3"]], "fault:2,3"),
    "all_ok": ([], [], "verdict_ok"),
}
FIELDS = ["utc", "case", "rep", "run", "expect", "got", "verdict_ok", "check_s", "whys",
          "order_ok", "trace_ok", "trace_note", "bursts", "writes", "cleared_ok", "result"]


def log(msg: str, fh=None) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def newest_run() -> Path | None:
    runs = sorted(POOL.glob("pool[0-9]*"))
    return runs[-1] if runs else None


def score_trace(trace: Path, expect: str) -> tuple[bool, str, int, int]:
    """(ok, note, bursts, writes) from led_trace.csv; the final restore write is ignored."""
    rows = []
    if trace.exists():
        for r in csv.DictReader(trace.open()):
            rows.append((float(r["t_mono_s"]), int(r["led_state"])))
    if len(rows) < 3:
        return False, f"only {len(rows)} writes", 0, len(rows)
    rows = rows[:-1]  # the stop's restore write (LED_STATE 1) ends the trace
    bursts, cur = [], [rows[0]]
    for prev, row in zip(rows, rows[1:]):
        if row[0] - prev[0] >= 3.5:
            bursts.append(cur)
            cur = []
        cur.append(row)
    bursts.append(cur)
    if expect == "verdict_ok":
        states = [s for _, s in rows]
        gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        alt = all(s == (1 if i % 2 == 0 else 0) for i, s in enumerate(states))
        ok = alt and len(rows) >= 6 and all(1.7 <= g <= 2.8 for g in gaps)
        return ok, f"OK blink, gaps {min(gaps):.2f}-{max(gaps):.2f} s", len(bursts), len(rows)
    codes = [int(c) for c in expect.split(":")[1].split(",")]
    full = bursts[:-1]  # the last burst may be cut by the stop
    if len(full) < 2:
        return False, f"{len(full)} complete bursts", len(bursts), len(rows)
    bad = []
    for i, b in enumerate(full):
        n = codes[i % len(codes)]
        states = [s for _, s in b]
        if states != [1, 0] * n:
            bad.append(f"burst{i}:{len(states)}w")
    note = f"{len(full)} bursts, codes {codes}" + (f", BAD {bad[:3]}" if bad else "")
    return not bad, note, len(bursts), len(rows)


def score_run(run: Path, expect: str) -> dict:
    man = json.loads((run / "run_manifest.json").read_text())
    sc = man.get("self_check") or {}
    got = ("verdict_ok" if sc.get("ok") else
           "fault:" + ",".join(map(str, sc.get("codes", [])))) if sc else "none"
    text = (run / "pool.log").read_text() if (run / "pool.log").exists() else ""
    i_sc, i_set = text.find("SELF-CHECK"), text.find("[set 001")
    trace_ok, note, bursts, writes = score_trace(run / "led_trace.csv", expect)
    whys = ";".join(f"{c}={v.get('why')}" for c, v in (sc.get("cameras") or {}).items())
    return {"run": run.name, "expect": expect, "got": got, "verdict_ok": got == expect,
            "check_s": sc.get("seconds"), "whys": whys,
            "order_ok": 0 <= i_sc < i_set, "trace_ok": trace_ok, "trace_note": note,
            "bursts": bursts, "writes": writes}


def boards_answer() -> bool:
    import board_probe
    return all(board_probe.first_contact(s).get("ok") for s in BOARDS.values())


def write_row(out: Path, row: dict) -> None:
    f = out / "cases.csv"
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def run_case(name: str, rep: int, out: Path, fh) -> dict:
    inject, clear, expect = CASES[name]
    if PIDFILE.exists():
        sys.exit("a pool loop is running: pool_mode.sh stop first")
    before = newest_run()
    for c in inject:
        r = subprocess.run(INJ + c, capture_output=True, text=True, timeout=90)
        log(f"{name}#{rep} inject {' '.join(c)}: rc {r.returncode} {r.stdout.strip()[-120:]}", fh)
        if r.returncode != 0:
            raise SystemExit("injection failed")
    subprocess.run(["bash", str(ROOT / "scripts" / "pool_mode.sh"), "start", "--self-check",
                    "--max-sets", "1"], capture_output=True, text=True, timeout=60)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 420:
        time.sleep(3)
        run = newest_run()
        if run != before and not PIDFILE.exists():
            break
    run = newest_run()
    row = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "case": name,
           "rep": rep}
    if run == before or run is None:
        row.update(result="FAIL", trace_note="no run folder")
    else:
        row.update(score_run(run, expect))
    for c in clear:
        r = subprocess.run(INJ + c, capture_output=True, text=True, timeout=90)
        log(f"{name}#{rep} clear {' '.join(c)}: rc {r.returncode} {r.stdout.strip()[-120:]}", fh)
    time.sleep(2)
    row["cleared_ok"] = boards_answer()
    if "result" not in row:
        row["result"] = "PASS" if all(row[k] for k in ("verdict_ok", "order_ok", "trace_ok",
                                                         "cleared_ok")) else "FAIL"
    write_row(out, row)
    log(f"{name}#{rep} {row['result']}: got {row.get('got')} (expect {expect}) "
        f"order {row.get('order_ok')} trace {row.get('trace_ok')} [{row.get('trace_note')}] "
        f"cleared {row['cleared_ok']} check {row.get('check_s')} s {row.get('whys')}", fh)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench-id", default=os.environ.get("SELFCHECK_BENCH_ID", "bench"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("case")
    c.add_argument("names", nargs="+", choices=sorted(CASES))
    c.add_argument("--rep", type=int, default=1)
    i = sub.add_parser("inspect")
    i.add_argument("run_dir")
    i.add_argument("--case", required=True)
    i.add_argument("--expect", required=True)
    i.add_argument("--rep", type=int, default=1)
    a = ap.parse_args()
    out = OUT / a.bench_id
    out.mkdir(parents=True, exist_ok=True)
    fh = (out / "bench.log").open("a")
    if a.cmd == "case":
        rows = [run_case(n, a.rep, out, fh) for n in a.names]
        return 0 if all(r["result"] == "PASS" for r in rows) else 1
    row = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "case": a.case,
           "rep": a.rep, **score_run(Path(a.run_dir), a.expect)}
    row["cleared_ok"] = boards_answer()
    row["result"] = "PASS" if all(row[k] for k in ("verdict_ok", "order_ok", "trace_ok",
                                                     "cleared_ok")) else "FAIL"
    write_row(out, row)
    log(f"{a.case}#{a.rep} {row['result']}: got {row['got']} order {row['order_ok']} "
        f"trace {row['trace_ok']} [{row['trace_note']}] check {row['check_s']} s", fh)
    return 0 if row["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
