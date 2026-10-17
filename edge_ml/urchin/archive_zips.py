"""Archive Nick's Reef Check zips to the SSD and unpack the videos. Scratch helper (E6).

    python3 -I archive_zips.py copy    <dest_zip_dir> <zip> ...   # unzip -t, copy, SHA-256 both
    python3 -I archive_zips.py verify  <dest_zip_dir> <zip> ...   # after a remount: every expected
                                                                  # zip present + re-hashed
    python3 -I archive_zips.py unpack  <dest_zip_dir> <video_dir> # MP4 members -> video/<folder>/

Sources are only read (never moved or deleted). Records go to <dest_zip_dir>/zips.tsv. The
`sha256_dest` column is hashed right after the write and may come from the page cache: only a
`verify` after the SSD has been unmounted and mounted again counts as the check.
`unpack` is not covered by SPEC_e6_night_pilot rev 3 (no zip extraction): it needs its own spec
and go-ahead before it is run.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


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


def cmd_unpack(dest: Path, video_dir: Path) -> int:
    require_mounted(dest)
    require_mounted(video_dir)
    out_rows = []
    for name in sorted(r[0] for r in records(dest)):
        z = dest / name
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.is_dir() or Path(info.filename).suffix.lower() != ".mp4":
                    continue
                if any(p.startswith(".") or p in ("__MACOSX", "..") for p in parts):
                    continue
                target = video_dir.joinpath(*parts)  # keeps the zip's folders
                target.parent.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(target.parent).free < info.file_size + MARGIN:
                    print(f"NO SPACE for {info.filename} on {video_dir}", flush=True)
                    return 1
                tmp = target.with_name(target.name + ".part")
                with zf.open(info) as src, tmp.open("wb") as dst:  # zipfile checks the CRC
                    shutil.copyfileobj(src, dst, 1 << 22)
                h = sha256(tmp)
                if target.exists():
                    if sha256(target) != h:
                        print(f"DIFFERS from existing: {target} (kept both: {tmp})", flush=True)
                        return 1
                    tmp.unlink()  # identical to the copy already verified on the SSD
                    status = "identical_to_existing"
                else:
                    tmp.rename(target)
                    status = "unpacked"
                rel = "/".join(parts)
                out_rows.append(f"{parts[0]}\t{rel}\t{info.file_size}\t{h}\t{z.name}\t{status}")
                print(f"{status} {rel} {h[:12]}", flush=True)
    (video_dir.parent / "unpack_ledger.tsv").write_text(
        "drive_folder\tfile\tbytes\tsha256\tzip\tstatus\n" + "\n".join(out_rows) + "\n"
    )
    print(f"unpack: {len(out_rows)} videos", flush=True)
    return 0


def main() -> int:
    cmd, dest = sys.argv[1], Path(sys.argv[2])
    if cmd == "copy":
        return cmd_copy(dest, [Path(a) for a in sys.argv[3:]])
    if cmd == "verify":
        return cmd_verify(dest, [Path(a).name for a in sys.argv[3:]])
    if cmd == "unpack":
        return cmd_unpack(dest, Path(sys.argv[3]))
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
