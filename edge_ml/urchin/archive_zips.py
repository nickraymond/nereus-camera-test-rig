"""Archive Nick's Reef Check zips to the SSD and unpack the videos. Scratch helper (E6).

    python3 -I archive_zips.py copy    <dest_zip_dir> <zip> ...   # unzip -t, copy, SHA-256 both
    python3 -I archive_zips.py verify  <dest_zip_dir>             # re-hash dest vs record (remount)
    python3 -I archive_zips.py unpack  <dest_zip_dir> <video_dir> # MP4 members -> video/<folder>/

Sources are only read (never moved or deleted). Records go to <dest_zip_dir>/zips.tsv.
"""

import hashlib
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


def cmd_copy(dest: Path, zips: list[Path]) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    rec = dest / "zips.tsv"
    if not rec.exists():
        rec.write_text("zip\tbytes\tsha256_src\tsha256_dest\tunzip_t\tcopied_utc\n")
    done = {ln.split("\t")[0] for ln in rec.read_text().splitlines()[1:]}
    for z in zips:
        if z.name in done:
            print(f"skip {z.name} (already recorded)", flush=True)
            continue
        t = subprocess.run(["unzip", "-tqq", str(z)], capture_output=True, text=True)
        if t.returncode != 0:
            print(f"UNZIP -t FAILED {z.name}: {t.stdout[-300:]} {t.stderr[-300:]}", flush=True)
            return 1
        out = dest / z.name
        shutil.copyfile(z, out)
        h1, h2 = sha256(z), sha256(out)
        if h1 != h2 or out.stat().st_size != z.stat().st_size:
            print(f"MISMATCH {z.name}: {h1} vs {h2}", flush=True)
            return 1
        with rec.open("a") as f:
            f.write(f"{z.name}\t{z.stat().st_size}\t{h1}\t{h2}\tok\t{now()}\n")
        print(f"ok {z.name} {z.stat().st_size} {h1[:12]}", flush=True)
    return 0


def cmd_verify(dest: Path) -> int:
    rows = [ln.split("\t") for ln in (dest / "zips.tsv").read_text().splitlines()[1:]]
    bad = 0
    for name, size, h_src, _h_dest, *_ in rows:
        p = dest / name
        h = sha256(p)
        ok = h == h_src and p.stat().st_size == int(size)
        bad += not ok
        print(f"{'ok' if ok else 'BAD'} {name}", flush=True)
    print(f"verify: {len(rows) - bad}/{len(rows)} match at {now()}", flush=True)
    return 1 if bad else 0


def cmd_unpack(dest: Path, video_dir: Path) -> int:
    out_rows = []
    for z in sorted(dest.glob("[!.]*.zip")):
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.is_dir() or Path(info.filename).suffix.lower() != ".mp4":
                    continue
                if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                    continue
                folder, name = parts[0], parts[-1]
                target = video_dir / folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
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
                out_rows.append(f"{folder}\t{name}\t{info.file_size}\t{h}\t{z.name}\t{status}")
                print(f"{status} {folder}/{name} {h[:12]}", flush=True)
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
        return cmd_verify(dest)
    if cmd == "unpack":
        return cmd_unpack(dest, Path(sys.argv[3]))
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
