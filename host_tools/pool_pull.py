"""pool_pull.py — pull the rig's pool runs to the Mac and verify every file (pool test 2026-10-14).

Purpose
    One command after the pool: copy ``results/pool/<run>/`` folders from nereus002 with rsync
    (resumable), then check every set's ``SHA256SUMS`` (written on the Pi right after the set)
    and the ``experiment.json`` hashes. Raw files are never modified; the report is separate.

Usage (Mac, repo root):
    python -m host_tools.pool_pull --no-dng                 # FAST first pass: all but the DNGs
    python -m host_tools.pool_render <dest>/<run> ...       # cut sheet from the JPEG/Bayer
    python -m host_tools.pool_pull --dng-for-render <dest>/<run>_render   # the picked DNGs
    python -m host_tools.pool_pull                          # everything (archive), resumable
    python -m host_tools.pool_pull --host pi@10.42.0.1      # at the pool: Mac on the rig's AP
    python -m host_tools.pool_pull --run pool003_20261014T170102Z --date 20261014

Hosts: ``pi@192.168.1.35`` on the bench LAN (default), ``pi@10.42.0.1`` when the Mac joins the
    rig's own open Wi-Fi ``nereus002`` (its AP fallback comes up ~60 s after boot when the home
    Wi-Fi is absent), or Tailscale ``pi@100.93.55.68``.

Outputs
    <dest>/<run>/...            exact copy of the Pi run folder (default dest
                                ~/NereusPool/<YYYYMMDD>/nereus002, next to the Spotter pulls)
    <dest>/pull_report.json     latest pass: per run sets, files, bytes, verified/bad/missing,
                                rsync seconds, MB/s of bytes actually transferred (also kept as
                                pull_report_<time>.json, one per pass)
Exit 0 = OK, every set verified; 1 = BAD (checksum mismatch or missing file); 2 = nothing to
    pull; 3 = UNVERIFIED: no mismatch, but a set has no SHA256SUMS — normally the last set, cut
    short when the rig was powered off (its files are checked against experiment.json where it
    exists) or a set still in flight. Look at the named set before trusting it.

Limitations: run-level files (run_manifest.json, sets.jsonl, power.csv, pool.log) change while
    a run is live and are not hashed; a file the Pi did not list in SHA256SUMS is not flagged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_HOST = "pi@192.168.1.35"
DEFAULT_REMOTE = "nereus-rig-poolcam/results/pool"
SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "BatchMode=yes"]


def remote_runs(host: str, remote: str) -> list[str]:
    out = subprocess.run(["ssh", *SSH_OPTS, host, f"ls -1 {remote}"], capture_output=True,
                         text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit(f"[pool_pull] cannot list {host}:{remote}: {out.stderr.strip()}")
    return sorted(n for n in out.stdout.split() if n.startswith("pool"))


def rsync_run(host: str, remote: str, run: str, dest: Path, no_dng: bool = False,
              files_from: Path | None = None) -> tuple[float, int]:
    """Copy one run folder (resumable, keeps partial files). Returns (seconds, bytes moved).
    ``no_dng`` skips the 24 MB DNGs (~80 % of the bytes; ~2 MB/s measured over the bench Wi-Fi
    on 2026-10-13); ``files_from`` copies only the listed run-relative paths."""
    t0 = time.monotonic()
    extra = (["--exclude", "*.dng"] if no_dng else []) + (
        ["--files-from", str(files_from)] if files_from else [])
    cmd = ["rsync", "-a", "--partial", "--stats", "--timeout=60", *extra,
           "-e", "ssh " + " ".join(SSH_OPTS), f"{host}:{remote}/{run}/", str(dest / run) + "/"]
    print(f"[pool_pull] rsync {host}:{remote}/{run} -> {dest / run}", flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"[pool_pull] rsync failed ({res.returncode}): {res.stderr.strip()} "
                         "— re-run the same command to resume (--partial)")
    moved = 0
    for line in res.stdout.splitlines():  # "Total transferred file size: 1,234 bytes"
        if line.startswith("Total transferred file size:"):
            moved = int("".join(ch for ch in line.split(":", 1)[1] if ch.isdigit()) or 0)
    return time.monotonic() - t0, moved


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_set(exp: Path, deferred: tuple = ()) -> dict:
    """SHA256SUMS (every file) + experiment.json (every capture) against the local copy.
    Files with a suffix in ``deferred`` (``.dng`` after a --no-dng pull) may be absent."""
    res = {"set": exp.name, "verified": 0, "bad": [], "missing": [], "deferred": 0,
           "unverified": False}
    sums = exp / "SHA256SUMS"
    known: dict[str, str] = {}
    if sums.is_file():
        for line in sums.read_text().splitlines():
            digest, _, rel = line.partition("  ")
            if not rel:
                continue
            known[rel] = digest
            p = exp / rel
            if not p.is_file() and p.suffix in deferred:
                res["deferred"] += 1
            elif not p.is_file():
                res["missing"].append(rel)
            elif _sha256(p) != digest:
                res["bad"].append(rel)
            else:
                res["verified"] += 1
    else:
        res["unverified"] = True
    rec_path = exp / "experiment.json"
    if rec_path.is_file():
        rec = json.loads(rec_path.read_text())
        for c in rec.get("captures", []) + rec.get("raw_captures", []):
            if c.get("status") != "completed" or not c.get("output_path"):
                continue
            name = Path(c["output_path"]).name
            hits = list(exp.glob(f"captures/*/{name}"))
            if not hits and Path(name).suffix in deferred:
                continue
            if not hits:
                res["missing"].append(name)
            elif not known and _sha256(hits[0]) != c.get("sha256"):
                res["bad"].append(name)
            elif known and known.get(str(hits[0].relative_to(exp))) not in (None, c.get("sha256")):
                res["bad"].append(name + " (experiment.json hash)")
    elif not res["unverified"]:
        res["missing"].append("experiment.json")
    return res


def verify_run(run_dir: Path, deferred: tuple = ()) -> dict:
    sets = sorted(run_dir.glob("*/exp_*"))
    results = [verify_set(s, deferred) for s in sets]
    files = [p for p in run_dir.rglob("*") if p.is_file()]
    return {"run": run_dir.name, "sets": len(sets),
            "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "files_verified": sum(r["verified"] for r in results),
            "files_deferred": sum(r["deferred"] for r in results),
            "bad": [f"{r['set']}/{b}" for r in results for b in r["bad"]],
            "missing": [f"{r['set']}/{m}" for r in results for m in r["missing"]],
            "unverified_sets": [r["set"] for r in results if r["unverified"]],
            "stop_reason": _manifest(run_dir).get("stop_reason")}


def dng_list_for_render(render_dir: Path, dest: Path) -> dict[str, list[str]]:
    """{run: [run-relative DNG paths]} for the sets pool_render picked (render_manifest
    ``selected``; entries are ``<run>/<set>/<file>``). The DNG path comes from the set's
    SHA256SUMS in the light copy, so no guessing of the date folder or timestamp."""
    man = json.loads((render_dir / "render_manifest.json").read_text())
    want: dict[str, set] = {}
    for entry in (man.get("selected") or {}).get("imx708", []):  # only IMX708 picks need a DNG
        run, set_name = entry.split("/")[:2]
        for exp in (dest / run).glob(f"*/{set_name}"):
            sums = exp / "SHA256SUMS"
            rels = [ln.partition("  ")[2] for ln in sums.read_text().splitlines()] \
                if sums.is_file() else []
            for rel in rels:
                if rel.endswith(".dng"):
                    want.setdefault(run, set()).add(f"{exp.parent.name}/{exp.name}/{rel}")
    return {run: sorted(paths) for run, paths in want.items()}


def _manifest(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / "run_manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="run root on the Pi (from ~)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="pool day folder under ~/NereusPool (default: today)")
    ap.add_argument("--dest", default=None, help="default ~/NereusPool/<date>/nereus002")
    ap.add_argument("--run", action="append", help="only this run (repeatable)")
    ap.add_argument("--verify-only", action="store_true", help="no rsync, re-check local copy")
    ap.add_argument("--no-dng", action="store_true",
                    help="fast first pass: everything except the DNGs")
    ap.add_argument("--dng-for-render", type=Path, default=None,
                    help="pull only the DNGs of the sets this pool_render folder picked")
    args = ap.parse_args(argv)
    dest = Path(args.dest or Path.home() / "NereusPool" / args.date / "nereus002").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    picked = dng_list_for_render(args.dng_for_render, dest) if args.dng_for_render else {}
    runs = args.run or (sorted(picked) if args.dng_for_render else
                        [p.name for p in sorted(dest.glob("pool*")) if p.is_dir()]
                        if args.verify_only else remote_runs(args.host, args.remote))
    if not runs:
        print("[pool_pull] no pool runs found", flush=True)
        return 2
    report = {"host": args.host, "remote": args.remote, "dest": str(dest),
              "pulled_at_mac": datetime.now().astimezone().isoformat(timespec="seconds"),
              "runs": []}
    t_all = time.monotonic()
    for run in runs:
        files_from = None
        if picked.get(run):
            files_from = dest / f".dng_list_{run}.txt"
            files_from.write_text("\n".join(picked[run]) + "\n")
            print(f"[pool_pull] {run}: {len(picked[run])} DNG(s) picked by the render", flush=True)
        secs, moved = (0.0, 0) if args.verify_only else rsync_run(
            args.host, args.remote, run, dest, args.no_dng, files_from)
        t_v = time.monotonic()
        r = verify_run(dest / run, (".dng",) if (args.no_dng or args.dng_for_render) else ())
        r.update({"rsync_seconds": round(secs, 1),
                  "verify_seconds": round(time.monotonic() - t_v, 1),
                  "bytes_transferred": moved,
                  "mb_per_s": round(moved / 1e6 / secs, 2) if secs > 0 else None})
        report["runs"].append(r)
        status = ("BAD" if r["bad"] or r["missing"] else
                  "UNVERIFIED" if r["unverified_sets"] else "OK")
        print(f"[pool_pull] {status} {run}: {r['sets']} sets, {r['files']} files, "
              f"{r['bytes'] / 1e9:.2f} GB, {r['files_verified']} verified"
              + (f", {r['files_deferred']} DNG deferred" if r["files_deferred"] else "")
              + (f", {len(r['bad'])} BAD {r['bad'][:3]}" if r["bad"] else "")
              + (f", {len(r['missing'])} MISSING {r['missing'][:3]}" if r["missing"] else "")
              + (f", UNVERIFIED sets (no SHA256SUMS: cut short at power-off or in flight) "
                 f"{r['unverified_sets']}" if r["unverified_sets"] else "")
              + f" · rsync {secs:.0f} s, {moved / 1e6:.0f} MB moved · stop {r['stop_reason']}",
              flush=True)
    report["total_seconds"] = round(time.monotonic() - t_all, 1)
    text = json.dumps(report, indent=1) + "\n"
    (dest / "pull_report.json").write_text(text)
    (dest / f"pull_report_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json").write_text(text)
    print(f"[pool_pull] report {dest / 'pull_report.json'} ({report['total_seconds']:.0f} s)")
    if any(r["bad"] or r["missing"] for r in report["runs"]):
        return 1
    return 3 if any(r["unverified_sets"] for r in report["runs"]) else 0


if __name__ == "__main__":
    sys.exit(main())
