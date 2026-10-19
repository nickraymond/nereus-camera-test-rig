"""E6 full run driver: stills from every unpacked Reef Check video (SPEC_e6_unpack_tierb rev 1).

    python -m edge_ml.urchin.fullrun extract <unpack_dir> <run_dir> [--python P] [--mount M]

`unpack_dir` is the SSD folder written by `archive_zips.py unpack` (plan.tsv, unpack_ledger.tsv,
<group>/<video>). The video list comes from its ledger, never from a glob (exFAT `._` files).
Videos run in unpack order (rev 2: the 4 added videos after the 25).
`run_dir` is the Mac control folder (~/NereusData/nereus-camera-test-rig/<date>_e6_fullrun/):
run.json, extract.log, quality.csv, the merged stills.csv / inventory.csv, and links. exFAT has no
symlinks, so the links live here: inputs/NN/<video> → the SSD video (extract_stills takes a folder,
and one video per folder keeps one video per call), extract → <unpack_dir>/extract, and
stills/<stem> → extract/NN/stills/<stem>. The stills themselves are written on the SSD.

Must do 6: extract_stills.py runs unchanged (PREREG E0–E5), one video at a time, with the pilot's
per-video quality columns. Before each video: SSD mounted, SSD and Mac free ≥ 8 GiB (U2, U3,
proposal stop-early c), `pmset -g therm` checked and paused on a limit (U5). Stop early if neither
of the first 2 videos keeps ≥ 10 stills/min (the pilot's rule at the spec's 10). U6: any new
name in the SSD root or next to unpack_dir fails the run. Study-only code; never imported from src/.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from edge_ml.urchin.archive_zips import _read_tsv
from edge_ml.urchin.pilot import (
    MIN_FREE_GIB,
    SSD_MOUNT,
    env_problem,
    free_gib,
    merge_stills,
    read_csv,
    read_json,
    run_watched,
    tool_cmd,
    video_quality,
    wait_cool,
    write_csv,
    write_json,
)
from edge_ml.urchin.pilot import _now as now

log = logging.getLogger("urchin.fullrun")

MIN_STILLS_PER_MIN = 10.0  # spec stop-early: the first 2 full videos
DONE = ("unpacked", "skipped_identical", "copied")
QUALITY_FIELDS = [
    "nn", "group", "video", "duration_s", "candidates", "window_best", "kept", "stills_per_min",
    "blur_share", "dup_share", "median_sharpness", "wall_s", "failed",
]  # fmt: skip


def unpacked_videos(unpack_dir: Path) -> list[dict]:
    """Ledger rows of the videos on the SSD: the latest row per target, in the order targets
    first appear in the ledger, i.e. the unpack order (spec rev 2: the 4 added videos are
    unpacked, so extracted, after the 25). A resume re-appends rows without reordering."""
    latest: dict[tuple[str, str], dict] = {}
    for r in _read_tsv(unpack_dir / "unpack_ledger.tsv"):
        latest[(r["group"], r["file"])] = r  # an existing key keeps its first position
    return [r for r in latest.values() if r["status"] in DONE]


def problem(unpack_dir: Path, run_dir: Path, mount: Path) -> str | None:
    if reason := env_problem(unpack_dir, mount):
        return reason
    if (gib := free_gib(run_dir)) < MIN_FREE_GIB:
        return f"Mac free disk {gib:.1f} GiB < {MIN_FREE_GIB:g} GiB"
    return None


def _listing(p: Path) -> set[str]:
    return set(os.listdir(p)) if p.is_dir() else set()


def first_two_fail(entries: list[dict]) -> bool:
    first2 = sorted(entries, key=lambda e: e["start_utc"])[:2]
    return len(first2) == 2 and not any(
        not e.get("failed") and e.get("quality", {}).get("stills_per_min", 0) >= MIN_STILLS_PER_MIN
        for e in first2
    )


def write_quality(run_dir: Path, state: dict) -> None:
    rows = []
    for e in sorted(state["extract"].values(), key=lambda e: e["nn"]):
        q = e.get("quality", {})
        rows.append({"nn": e["nn"], "group": e["group"], "video": e["video"],
                     "duration_s": e.get("duration_s", ""), **{k: q.get(k, "") for k in
                     QUALITY_FIELDS[4:11]}, "wall_s": e.get("wall_s", ""),
                     "failed": e.get("failed", "")})  # fmt: skip
    write_csv(run_dir / "quality.csv", rows, QUALITY_FIELDS)


def cmd_extract(unpack_dir: Path, run_dir: Path, python: str, mount: Path) -> int:
    """0 = every video extracted or listed as failed; 2 = stopped (reason in run.json events)."""
    videos = unpacked_videos(unpack_dir)
    if not videos:
        raise SystemExit(f"no unpacked videos in {unpack_dir / 'unpack_ledger.tsv'}")
    run_dir.mkdir(parents=True, exist_ok=True)
    state = read_json(run_dir / "run.json", {"extract": {}, "therm": [], "events": []})
    state.setdefault("unpack_dir", str(unpack_dir))

    def stop(reason: str, **where) -> int:
        state["events"].append({"utc": now(), "stop": reason, **where})
        write_json(run_dir / "run.json", state)
        write_quality(run_dir, state)
        log.error("STOP: %s %s", reason, where or "")
        return 2

    if reason := problem(unpack_dir, run_dir, mount):
        return stop(reason, before="extract")
    watch = [unpack_dir.parent, mount]
    before = {str(p): sorted(_listing(p)) for p in watch}
    state["events"].append({"utc": now(), "start": "extract", "listings": before})
    (unpack_dir / "extract").mkdir(exist_ok=True)
    link = run_dir / "extract"
    if not link.is_symlink():
        link.symlink_to(unpack_dir / "extract")
    for i, v in enumerate(videos, start=1):
        nn, key = f"{i:02d}", f"{v['group']}/{v['file']}"
        if state["extract"].get(key, {}).get("done"):
            continue
        if reason := problem(unpack_dir, run_dir, mount):
            return stop(reason, before=key)
        wait_cool(state["therm"], f"extract {nn} {key}")
        in_dir, out_dir = run_dir / "inputs" / nn, run_dir / "extract" / nn
        in_dir.mkdir(parents=True, exist_ok=True)
        src = in_dir / v["file"]
        if not src.is_symlink():
            src.symlink_to(unpack_dir / v["group"] / v["file"])
        entry = {"nn": nn, "group": v["group"], "video": v["file"], "sha256": v["sha256"],
                 "start_utc": now()}  # fmt: skip
        t0 = time.monotonic()
        status = run_watched(
            tool_cmd(python, "extract_stills", "inventory", str(in_dir), str(out_dir)),
            lambda: problem(unpack_dir, run_dir, mount),
        )
        inv = read_csv(out_dir / "inventory.csv") if (out_dir / "inventory.csv").exists() else []
        if status not in ("ok", "rc=1"):
            return stop(status, during=key)
        if not inv or inv[0].get("error"):
            entry |= {"failed": "E0 ffprobe", "done": True}
        elif inv[0]["sha256"] != v["sha256"]:  # the file read is the file the ledger recorded
            return stop(f"sha256 of {key} differs from unpack_ledger.tsv", during=key)
        else:
            status = run_watched(
                tool_cmd(python, "extract_stills", "extract", str(in_dir), str(out_dir)),
                lambda: problem(unpack_dir, run_dir, mount),
            )
            if status not in ("ok", "rc=1"):
                return stop(status, during=key)
            ex_run = read_json(out_dir / "extract_run.json")
            if ex_run is None:  # a crash, not a bad video: stop so the step gets a repo fix
                return stop(f"extract crashed ({status})", during=key)
            rows = read_csv(out_dir / "stills.csv") if (out_dir / "stills.csv").exists() else []
            entry |= {"duration_s": float(inv[0]["duration_s"] or 0), "done": True,
                      "quality": video_quality(rows, float(inv[0]["duration_s"] or 0))}  # fmt: skip
            if ex_run.get("failed") or not rows:
                entry["failed"] = "decode"
        entry["wall_s"] = round(time.monotonic() - t0, 1)
        state["extract"][key] = entry
        write_json(run_dir / "run.json", state)
        write_quality(run_dir, state)
        log.info("%s %s: %s", nn, key, entry.get("failed") or entry["quality"])
        if first_two_fail(list(state["extract"].values())):
            return stop(f"neither of the first 2 videos keeps >= {MIN_STILLS_PER_MIN:g} stills/min")
    merge_stills(run_dir, state)
    after = {str(p): sorted(_listing(p)) for p in watch}
    extra = {f"{p}/{n}" for p in after for n in set(after[p]) - set(before[p])}
    state["events"].append({"utc": now(), "done": "extract", "listings": after,
                            "u6_extra": sorted(extra)})  # fmt: skip
    write_json(run_dir / "run.json", state)
    write_quality(run_dir, state)
    if extra:
        log.error("U6 FAIL: new paths outside %s: %s", unpack_dir, sorted(extra))
        return 1
    failed = [k for k, e in state["extract"].items() if e.get("failed")]
    log.info("extract: %d videos, %d failed %s", len(state["extract"]), len(failed), failed or "")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["extract"])
    ap.add_argument("unpack_dir", type=Path)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the pipeline tools")
    ap.add_argument("--mount", type=Path, default=SSD_MOUNT)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return cmd_extract(args.unpack_dir, args.run_dir, args.python, args.mount)


if __name__ == "__main__":
    sys.exit(main())
