"""Archive Nick's Reef Check zips to the SSD and unpack the videos. Scratch helper (E6).

    python3 -I archive_zips.py copy    <dest_zip_dir> <zip> ...   # unzip -t, copy, SHA-256 both
    python3 -I archive_zips.py verify  <dest_zip_dir> <zip> ...   # after a remount: every expected
                                                                  # zip present + re-hashed
    python3 -I archive_zips.py unpack  <dest_zip_dir> <run_dir> <zip> ... [--dry-run]
                                         # video members -> <run_dir>/<group>/<file>
    python3 -I archive_zips.py copy-file <video> <download_ledger.tsv> <run_dir>
                                         # spec rev 3: one non-zip video, exact name, sha checked

Sources are only read (never moved or deleted). Records go to <dest_zip_dir>/zips.tsv. The
`sha256_dest` column is hashed right after the write and may come from the page cache: only a
`verify` after the SSD has been unmounted and mounted again counts as the check.
`unpack` follows SPEC_e6_unpack_tierb.md rev 1 (PE 004): only the named zips that `copy` recorded;
plan.tsv + MANIFEST.md (planned GB) before any write; rejects absolute, `..` and symlink members
(nothing is unpacked then); flattened, sanitised names; CRC checked on read; SHA-256 per video in
unpack_ledger.tsv and the MANIFEST; an identical existing target is skipped, a different one stops;
stops when the SSD unmounts or free space < member + 5 GiB; any new path outside run_dir fails.
"""

import hashlib
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


MARGIN = 2 * 2**30  # keep 2 GiB free on the destination


def require_mounted(path: Path) -> None:
    """A /Volumes/<name>/... path must be on a mounted volume: writing there while the volume is
    unmounted silently creates the folder on the Mac's own disk."""
    parts = path.absolute().parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        vol = Path(*parts[:3])
        if not os.path.ismount(vol):
            raise SystemExit(f"{vol} is not mounted; refusing to write {path}")


def records(dest: Path) -> list[list[str]]:
    return [ln.split("\t") for ln in (dest / "zips.tsv").read_text().splitlines()[1:] if ln]


def cmd_copy(dest: Path, zips: list[Path]) -> int:
    require_mounted(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rec = dest / "zips.tsv"
    if not rec.exists():
        rec.write_text("zip\tbytes\tsha256_src\tsha256_dest\tunzip_t\tcopied_utc\n")
    done = {r[0] for r in records(dest)}
    for z in zips:
        if z.name in done:
            print(f"skip {z.name} (already recorded)", flush=True)
            continue
        t = subprocess.run(["unzip", "-tqq", str(z)], capture_output=True, text=True)
        if t.returncode != 0:
            print(f"UNZIP -t FAILED {z.name}: {t.stdout[-300:]} {t.stderr[-300:]}", flush=True)
            return 1
        out = dest / z.name
        part = out.with_name(out.name + ".part")
        if shutil.disk_usage(dest).free < z.stat().st_size + MARGIN:
            print(f"NO SPACE for {z.name} on {dest}", flush=True)
            return 1
        shutil.copyfile(z, part)
        h1, h2 = sha256(z), sha256(part)
        if h1 != h2 or part.stat().st_size != z.stat().st_size:
            print(f"MISMATCH {z.name}: {h1} vs {h2} (left as {part.name})", flush=True)
            return 1
        part.rename(out)  # the final name exists only for a copy whose hash matched
        with rec.open("a") as f:
            f.write(f"{z.name}\t{z.stat().st_size}\t{h1}\t{h2}\tok\t{now()}\n")
        print(f"ok {z.name} {z.stat().st_size} {h1[:12]}", flush=True)
    return 0


def cmd_verify(dest: Path, expected: list[str]) -> int:
    """Every expected zip must be recorded, present, the recorded size and the source hash."""
    require_mounted(dest)
    if not expected:
        raise SystemExit("verify needs the expected zips (the same list given to copy)")
    rows = {r[0]: r for r in records(dest)}
    bad = 0
    for name in expected:
        if name not in rows:
            print(f"MISSING {name} (not recorded)", flush=True)
            bad += 1
            continue
        _, size, h_src, *_ = rows[name]
        p = dest / name
        if not p.is_file():
            print(f"MISSING {name} (recorded, file not found)", flush=True)
            bad += 1
            continue
        ok = p.stat().st_size == int(size) and sha256(p) == h_src
        bad += not ok
        print(f"{'ok' if ok else 'BAD'} {name}", flush=True)
    if extra := sorted(set(rows) - set(expected)):
        print(f"note: recorded but not expected: {extra}", flush=True)
    print(
        f"verify: {len(expected) - bad}/{len(expected)} expected zips match at {now()}", flush=True
    )
    return 1 if bad else 0


# --------------------------------------------------------------------------- unpack
# SPEC_e6_unpack_tierb.md rev 1 (PE 004, sha256 5d0cd6c6ec5c…): must dos 1–5, rows U1–U4 and U6.

VIDEO_SUFFIXES = {".mp4", ".mov"}  # must do 3: video members only, any case
UNPACK_MARGIN = 5 * 2**30  # must do 5: free >= member size + 5 GB (GiB, the stricter reading)
UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9 ._()+-]")
PLAN_FIELDS = ["zip", "member", "kind", "bytes", "decision", "reason", "group", "file"]
LEDGER_FIELDS = ["zip", "member", "group", "file", "bytes", "sha256", "status", "utc"]


def mounted_volume(path: Path) -> Path | None:
    """The /Volumes/<name> that holds path, if that volume is mounted now."""
    parts = path.absolute().parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        vol = Path(*parts[:3])
        if os.path.ismount(vol):
            return vol
    return None


def member_kind(info: zipfile.ZipInfo) -> str:
    if info.is_dir():
        return "dir"
    if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):  # 3 = Unix
        return "symlink"
    return "file"


def sanitise(name: str) -> str:
    """Letters, digits, space and . _ ( ) + - are kept; anything else becomes '_'. Every Reef
    Check name passes unchanged, so the stems still match groups_reefcheck.json."""
    return UNSAFE_CHARS.sub("_", name)


def classify(info: zipfile.ZipInfo) -> dict:
    """One plan row: decision unpack / skip (listed, harmless) / reject (U1: nothing is written)."""
    raw = info.filename
    parts = PurePosixPath(raw.replace("\\", "/")).parts
    kind = member_kind(info)
    row = {"member": raw, "kind": kind, "bytes": info.file_size, "group": "", "file": ""}
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        return row | {"decision": "reject", "reason": "absolute path"}
    if ".." in parts:
        return row | {"decision": "reject", "reason": "'..' part"}
    if kind == "symlink":
        return row | {"decision": "reject", "reason": "symlink"}
    if kind == "dir":
        return row | {"decision": "skip", "reason": "directory"}
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return row | {"decision": "skip", "reason": "hidden or AppleDouble"}
    if PurePosixPath(parts[-1]).suffix.lower() not in VIDEO_SUFFIXES:
        return row | {"decision": "skip", "reason": "not a video"}
    group = sanitise(parts[0]) if len(parts) > 1 else "_root"
    return row | {"decision": "unpack", "reason": "", "group": group, "file": sanitise(parts[-1])}


def plan_unpack(zip_dir: Path, zip_names: list[str]) -> tuple[list[dict], list[str]]:
    """Every member of every named zip, classified; plus the problems that block any write."""
    rows, problems = [], []
    recorded = {r[0]: r for r in records(zip_dir)}
    for name in zip_names:
        if name not in recorded:
            problems.append(f"{name}: not in zips.tsv (only verified copies are unpacked)")
            continue
        z = zip_dir / name
        if not z.is_file() or z.stat().st_size != int(recorded[name][1]):
            problems.append(f"{name}: missing or not the recorded size")
            continue
        with zipfile.ZipFile(z) as zf:
            rows += [{"zip": name} | classify(info) for info in zf.infolist()]
    problems += [
        f"REJECT {r['zip']}: {r['member']!r} ({r['reason']})"
        for r in rows
        if r["decision"] == "reject"
    ]
    targets: dict[tuple[str, str], str] = {}
    for r in rows:
        if r["decision"] == "unpack":
            key = (r["group"], r["file"])
            if key in targets:
                problems.append(
                    f"COLLISION {r['group']}/{r['file']}: {targets[key]!r} and {r['member']!r}"
                )
            targets[key] = r["member"]
    return rows, problems


def _write_tsv(path: Path, rows: list[dict], fields: list[str]) -> None:
    lines = ["\t".join(fields)] + ["\t".join(str(r[f]) for f in fields) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def _read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    head, *body = path.read_text().splitlines()
    fields = head.split("\t")
    return [dict(zip(fields, ln.split("\t"), strict=True)) for ln in body if ln]


def _stream_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dst=None) -> str:
    """SHA-256 of the member's bytes, written to dst if given. zipfile checks the CRC at the end
    and raises BadZipFile on a mismatch (U4)."""
    h = hashlib.sha256()
    n = 0
    with zf.open(info) as src:
        while chunk := src.read(1 << 22):
            h.update(chunk)
            n += len(chunk)
            if dst is not None:
                dst.write(chunk)
    if n != info.file_size:
        raise zipfile.BadZipFile(f"{info.filename}: read {n} B, header says {info.file_size}")
    return h.hexdigest()


def _listing(path: Path) -> set[str]:
    return set(os.listdir(path)) if path.is_dir() else set()


def u6_extra(run_dir: Path, before: dict[Path, set[str]]) -> set[str]:
    """New names in the watched folders (run dir's parent, volume root) other than the run dir."""
    ours = {run_dir.name, f"._{run_dir.name}"}
    return {
        str(p / n)
        for p in before
        for n in _listing(p) - before[p]
        if not (p == run_dir.parent and n in ours)
    }


def tool_sha() -> str:
    here = Path(__file__).resolve().parent
    sha = (
        subprocess.run(
            ["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        or "unknown"
    )
    dirty = subprocess.run(
        ["git", "-C", str(here), "status", "--porcelain", "--", "."], capture_output=True, text=True
    ).stdout.strip()
    return sha + (" (dirty)" if dirty else "")


def write_manifest(run_dir: Path, zip_dir: Path, plan: list[dict], ledger: list[dict]) -> None:
    """MANIFEST.md from plan.tsv and the ledger: the plan totals first (must do 2), then one row
    per video with its size and SHA-256 (must do 3), latest ledger row per target."""
    todo = [r for r in plan if r["decision"] == "unpack"]
    per_zip: dict[str, list[int]] = {}
    for r in plan:
        per_zip.setdefault(r["zip"], [0, 0, 0])
        per_zip[r["zip"]][0] += 1
        if r["decision"] == "unpack":
            per_zip[r["zip"]][1] += 1
            per_zip[r["zip"]][2] += int(r["bytes"])
    latest = {(r["group"], r["file"]): r for r in ledger}
    planned = sum(int(r["bytes"]) for r in todo)
    lines = [
        "# Reef Check unpack (E6): Oct 2022 + South Wreck",
        "",
        "- **What:** video members of the verified SSD zip copies, unpacked per "
        "SPEC_e6_unpack_tierb.md rev 1 (sha256 5d0cd6c6ec5c…).",
        f"- **Source:** `{zip_dir}` (zips.tsv; the zips are only read). ~/Downloads is not read.",
        f"- **Tool:** rig `edge_ml/urchin/archive_zips.py unpack` @ `{tool_sha()}`, host "
        f"`{socket.gethostname()}`. Written {now()}.",
        f"- **Plan (before any write):** {len(todo)} videos, {planned:,} B "
        f"= {planned / 1e9:.2f} GB; "
        f"{sum(r['decision'] == 'skip' for r in plan)} members skipped, "
        f"{sum(r['decision'] == 'reject' for r in plan)} rejected (plan.tsv).",
        "",
        "| Zip | Members | Videos | Video bytes |",
        "|---|---|---|---|",
        *[f"| {z} | {m} | {v} | {b:,} |" for z, (m, v, b) in per_zip.items()],
        "",
        "## Videos",
        "",
        "| Group | File | Bytes | SHA-256 | Status | Zip |",
        "|---|---|---|---|---|---|",
    ]
    for r in todo:
        got = latest.get((r["group"], r["file"]))
        status, sha = (got["status"], got["sha256"]) if got else ("not yet", "")
        cell = f"`{sha}`" if sha else "—"
        lines.append(
            f"| {r['group']} | {r['file']} | {int(r['bytes']):,} | {cell} | {status} | {r['zip']} |"
        )
    planned = {(r["group"], r["file"]) for r in todo}
    if other := [r for k, r in latest.items() if k not in planned]:
        lines += [
            "",
            "## Videos not from this plan (spec rev 3: copied files)",
            "",
            "| Group | File | Bytes | SHA-256 | Status | Source |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {r['group']} | {r['file']} | {int(r['bytes']):,} | `{r['sha256']}` | {r['status']} "
            f"| {r['member']} |"
            for r in other
        ]
    if odd := [r for r in plan if r["decision"] != "unpack"]:
        lines += [
            "",
            "## Skipped or rejected members",
            "",
            "| Zip | Member | Decision | Reason |",
            "|---|---|---|---|",
        ]
        lines += [f"| {r['zip']} | {r['member']} | {r['decision']} | {r['reason']} |" for r in odd]
    (run_dir / "MANIFEST.md").write_text("\n".join(lines) + "\n")


def cmd_unpack(
    zip_dir: Path,
    run_dir: Path,
    zip_names: list[str],
    *,
    dry_run: bool = False,
    on_volume: bool = True,
) -> int:
    """0 = every planned video unpacked or already identical; 1 = a listed problem (reject,
    collision, different existing file, CRC failure, U6); 2 = stopped (SSD unmounted or full)."""
    if on_volume:  # must do 5: nothing is ever written to the Mac disk
        for p in (zip_dir, run_dir):
            if mounted_volume(p) is None:
                raise SystemExit(f"{p} is not on a mounted /Volumes volume; refusing")
    if not zip_names:
        raise SystemExit("unpack needs the target zip names (a list, never a glob)")
    watch = [run_dir.parent] + ([mounted_volume(run_dir)] if on_volume else [])
    before = {p: _listing(p) for p in watch}
    rows, problems = plan_unpack(zip_dir, zip_names)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(run_dir / "plan.tsv", rows, PLAN_FIELDS)
    (run_dir / "zip_dir.txt").write_text(f"{zip_dir}\n")
    ledger_path = run_dir / "unpack_ledger.tsv"
    write_manifest(run_dir, zip_dir, rows, _read_tsv(ledger_path))
    todo = [r for r in rows if r["decision"] == "unpack"]
    print(
        f"plan: {len(todo)} videos, {sum(r['bytes'] for r in todo) / 1e9:.2f} GB; "
        f"{len(problems)} problems",
        flush=True,
    )
    for p in problems:
        print(p, flush=True)
    if problems:
        return 1  # U1: rejects and collisions are listed and nothing is unpacked
    if dry_run:
        return 0
    if not ledger_path.exists():
        ledger_path.write_text("\t".join(LEDGER_FIELDS) + "\n")
    rc = 0
    for r in todo:
        if on_volume and mounted_volume(run_dir) is None:  # U2
            print(f"STOP: {run_dir} is no longer mounted (before {r['member']})", flush=True)
            return 2
        free = shutil.disk_usage(run_dir).free
        if free < r["bytes"] + UNPACK_MARGIN:  # U3
            print(f"STOP: {free / 2**30:.1f} GiB free < {r['member']} + 5 GiB", flush=True)
            return 2
        group_dir = run_dir / r["group"]
        target = group_dir / r["file"]
        if run_dir.resolve() not in target.resolve().parents:  # U1, belt and braces
            print(f"REJECT {r['member']!r}: target {target} leaves {run_dir}", flush=True)
            return 1
        group_dir.mkdir(exist_ok=True)
        part = target.with_name(target.name + ".part")
        with zipfile.ZipFile(zip_dir / r["zip"]) as zf:
            info = zf.getinfo(r["member"])
            try:
                if target.exists():  # must do 4: resume
                    h = _stream_member(zf, info)
                    if sha256(target) != h:
                        print(
                            f"STOP: {target} exists with a different SHA-256 than "
                            f"{r['zip']}:{r['member']} ({h[:12]}); nothing overwritten",
                            flush=True,
                        )
                        return 1
                    status = "skipped_identical"
                else:
                    with part.open("wb") as dst:
                        h = _stream_member(zf, info, dst)
                        dst.flush()
                        os.fsync(dst.fileno())
                    part.rename(target)
                    status = "unpacked"
            except (zipfile.BadZipFile, zlib.error, EOFError) as exc:  # U4: list, skip, go on
                part.unlink(missing_ok=True)
                h, status, rc = "", f"crc_fail: {str(exc)[:120]}", 1
        row = {
            "zip": r["zip"],
            "member": r["member"],
            "group": r["group"],
            "file": r["file"],
            "bytes": r["bytes"],
            "sha256": h,
            "status": status,
            "utc": now(),
        }
        with ledger_path.open("a") as f:
            f.write("\t".join(str(row[k]) for k in LEDGER_FIELDS) + "\n")
        print(f"{status} {r['group']}/{r['file']} {r['bytes']} {h[:12]}", flush=True)
    write_manifest(run_dir, zip_dir, rows, _read_tsv(ledger_path))
    if extra := u6_extra(run_dir, before):  # U6: any new path outside the run folder fails
        print(f"U6 FAIL: new paths outside {run_dir}: {sorted(extra)}", flush=True)
        return 1
    print(
        f"unpack: {len(todo)} videos done ({'all ok' if rc == 0 else 'see crc_fail rows'})",
        flush=True,
    )
    return rc


def cmd_copy_file(src: Path, src_ledger: Path, run_dir: Path, *, on_volume: bool = True) -> int:
    """Spec rev 3: one video that no zip holds (Mendocino, the 10/15 Drive download), by its exact
    name. Size + SHA-256 against its download ledger before the copy; copied into
    <run_dir>/<drive_folder>/ with a `copied` ledger + MANIFEST row; re-hashed after the copy.
    0 = copied or already identical; 1 = refused or a mismatch (stop and report)."""
    if on_volume:
        for p in (src, run_dir):
            if mounted_volume(p) is None:
                raise SystemExit(f"{p} is not on a mounted /Volumes volume; refusing")
    name = src.name
    if name.startswith(".") or src.suffix.lower() not in VIDEO_SUFFIXES or src.is_symlink():
        print(f"REFUSE {src}: not a video file by exact name (hidden/AppleDouble, suffix or link)")
        return 1
    rows = [r for r in _read_tsv(src_ledger)
            if r["file"] == name and r["drive_folder"] == src.parent.name]  # fmt: skip
    if len(rows) != 1:
        print(f"STOP: {len(rows)} rows for {src.parent.name}/{name} in {src_ledger}", flush=True)
        return 1
    want_bytes, want_sha, group = int(rows[0]["bytes"]), rows[0]["sha256"], rows[0]["drive_folder"]
    if not src.is_file() or src.stat().st_size != want_bytes:
        print(f"STOP: {src} is missing or not {want_bytes} B (download ledger)", flush=True)
        return 1
    if (got := sha256(src)) != want_sha:
        print(
            f"STOP: {src} sha256 {got[:12]} != ledger {want_sha[:12]}; nothing copied", flush=True
        )
        return 1
    before = {p: _listing(p) for p in [run_dir.parent, *([mounted_volume(run_dir)] if on_volume
                                                          else [])]}  # fmt: skip
    room = next(q for q in (run_dir, *run_dir.parents) if q.exists())
    if shutil.disk_usage(room).free < want_bytes + UNPACK_MARGIN:
        print(f"STOP: not enough free space for {name} + 5 GiB", flush=True)
        return 1
    target = run_dir / sanitise(group) / sanitise(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != want_sha:
            print(
                f"STOP: {target} exists with a different SHA-256; nothing overwritten", flush=True
            )
            return 1
        status = "skipped_identical"
    else:
        part = target.with_name(target.name + ".part")
        shutil.copyfile(src, part)
        with part.open("rb+") as f:
            os.fsync(f.fileno())
        if sha256(part) != want_sha or part.stat().st_size != want_bytes:  # re-check after copy
            print(f"STOP: the copy {part} does not match the ledger (left for inspection)")
            return 1
        part.rename(target)
        status = "copied"
    row = {"zip": src_ledger.name, "member": str(src), "group": target.parent.name,
           "file": target.name, "bytes": want_bytes, "sha256": want_sha, "status": status,
           "utc": now()}  # fmt: skip
    ledger_path = run_dir / "unpack_ledger.tsv"
    if not ledger_path.exists():
        ledger_path.write_text("\t".join(LEDGER_FIELDS) + "\n")
    with ledger_path.open("a") as f:
        f.write("\t".join(str(row[k]) for k in LEDGER_FIELDS) + "\n")
    zd = run_dir / "zip_dir.txt"
    zip_dir = Path(zd.read_text().strip()) if zd.exists() else Path("(no unpack yet)")
    write_manifest(run_dir, zip_dir, _read_tsv(run_dir / "plan.tsv"), _read_tsv(ledger_path))
    if extra := u6_extra(run_dir, before):
        print(f"U6 FAIL: new paths outside {run_dir}: {sorted(extra)}", flush=True)
        return 1
    print(f"{status} {target.parent.name}/{target.name} {want_bytes} {want_sha[:12]}", flush=True)
    return 0


def main() -> int:
    cmd, dest = sys.argv[1], Path(sys.argv[2])
    if cmd == "copy":
        return cmd_copy(dest, [Path(a) for a in sys.argv[3:]])
    if cmd == "verify":
        return cmd_verify(dest, [Path(a).name for a in sys.argv[3:]])
    if cmd == "copy-file":
        return cmd_copy_file(dest, Path(sys.argv[3]), Path(sys.argv[4]))
    if cmd == "unpack":
        args = [a for a in sys.argv[4:] if a != "--dry-run"]
        return cmd_unpack(dest, Path(sys.argv[3]), args, dry_run="--dry-run" in sys.argv[4:])
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
