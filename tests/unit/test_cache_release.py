"""sync_video.py page-cache release (spec pool3 rev 2.3/2.4 must do 7): the clip files are
byte-identical with and without it, it runs in its own thread on files opened by path, and
host_stats.csv carries cached_mb + dirty_mb."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("sync_video", ROOT / "scripts" / "sync_video.py")
sv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sv)

# Linux (the Pi): the real calls. A dev Mac has neither, so fsync and a no-op stand in.
SYNC = getattr(os, "fdatasync", os.fsync)
ADVISE = getattr(os, "posix_fadvise", lambda fd, off, n, adv: None)
if not hasattr(os, "POSIX_FADV_DONTNEED"):
    os.POSIX_FADV_DONTNEED = 4  # Linux's value; only passed to the stand-in above


def _clip(out: Path, release_s: float | None, seconds: int = 60, fps: int = 10) -> dict:
    """A 60 s clip's worth of writes, compressed in time: three writers (two mjpeg-like, one
    h264-like) append deterministic chunks while the releaser runs at release_s (None = off)."""
    out.mkdir()
    stop, stats = threading.Event(), {}
    rel = None
    if release_s is not None:
        rel = threading.Thread(target=sv.cache_releaser,
                               args=(out, release_s, stop, stats, SYNC, ADVISE))
        rel.start()

    def writer(name, size):
        with (out / name).open("wb") as f:
            for i in range(seconds * fps):
                f.write(hashlib.sha256(f"{name}{i}".encode()).digest() * (size // 32 + i % 7))
                f.flush()
                if i % 50 == 0:
                    time.sleep(0.001)

    ws = [threading.Thread(target=writer, args=a)
          for a in (("openmv_n6.mjpeg", 4096), ("openmv_ae3.mjpeg", 2048),
                    ("imx708.h264", 3072))]
    [w.start() for w in ws]
    [w.join() for w in ws]
    stop.set()
    if rel:
        rel.join(timeout=10)
    return stats


def _digests(d: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())
            if p.name != "cache_release.csv"}


def test_clip_files_byte_identical_with_and_without_release(tmp_path):
    off = _clip(tmp_path / "off", None)
    on = _clip(tmp_path / "on", 0.002)
    assert off == {}
    assert on["passes"] >= 2 and on["errors"] == 0
    assert _digests(tmp_path / "on") == _digests(tmp_path / "off")
    assert len(_digests(tmp_path / "on")) == 3


def _record(tmp_path):
    calls = []

    def sync(fd):
        calls.append(("sync", os.fstat(fd).st_size))

    def advise(fd, off, n, adv):
        calls.append(("advise", off, n, adv))
    return calls, sync, advise


def test_release_opens_by_path_skips_unchanged_and_runs_a_last_pass(tmp_path):
    calls, sync, advise = _record(tmp_path)
    (tmp_path / "a.mjpeg").write_bytes(b"x" * 10)
    (tmp_path / "sub").mkdir()  # not a file: skipped
    stop, stats = threading.Event(), {}
    t = threading.Thread(target=sv.cache_releaser, args=(tmp_path, 0.05, stop, stats, sync,
                                                         advise))
    t.start()
    time.sleep(0.2)  # several passes; a.mjpeg unchanged after the first
    (tmp_path / "a.mjpeg").write_bytes(b"x" * 25)
    stop.set()  # the last pass picks up the final bytes
    t.join(timeout=5)
    syncs = [c for c in calls if c[0] == "sync"]
    assert syncs[0] == ("sync", 10) and syncs[-1] == ("sync", 25)
    assert ("sync", 10) in syncs and syncs.count(("sync", 10)) == 1
    assert all(c == ("advise", 0, 0, os.POSIX_FADV_DONTNEED) for c in calls if c[0] == "advise")
    rows = (tmp_path / "cache_release.csv").read_text().splitlines()
    assert rows[0] == "mono_s,files,bytes,release_ms"
    assert len(rows) - 1 == stats["passes"] >= 3
    assert stats["max_ms"] is not None and stats["errors"] == 0


def test_last_pass_releases_every_file_even_unchanged(tmp_path):
    # QE 011 N1: finish() hashes each clip file before stop, which caches it again; a size-only
    # skip on the last pass would leave ~120 MB cached and fail the next run's idle check.
    calls, sync, advise = _record(tmp_path)
    (tmp_path / "a.mjpeg").write_bytes(b"x" * 10)
    stop, stats = threading.Event(), {}
    t = threading.Thread(target=sv.cache_releaser, args=(tmp_path, 0.05, stop, stats, sync,
                                                         advise))
    t.start()
    time.sleep(0.2)  # first pass releases it, later periodic passes skip it (unchanged)
    stop.set()
    t.join(timeout=5)
    assert stats["passes"] >= 3
    assert [c for c in calls if c[0] == "sync"] == [("sync", 10), ("sync", 10)]  # first + last
    assert calls.count(("advise", 0, 0, os.POSIX_FADV_DONTNEED)) == 2


def test_release_error_is_counted_not_fatal(tmp_path):
    (tmp_path / "a.mjpeg").write_bytes(b"x")

    def sync(fd):
        raise OSError(5, "EIO")
    stop, stats = threading.Event(), {}
    stop.set()
    sv.cache_releaser(tmp_path, 1.0, stop, stats, sync, lambda *a: None)
    assert stats["passes"] == 1 and stats["errors"] == 1 and "EIO" in stats["last_error"]


def test_release_without_the_calls_is_a_no_op(tmp_path):
    stop, stats = threading.Event(), {}
    sv.cache_releaser(tmp_path, 1.0, stop, stats, None, None)
    assert stats["unsupported"] and stats["passes"] == 0
    assert not (tmp_path / "cache_release.csv").exists()


def test_cache_release_flag_default_and_bounds(monkeypatch, capsys):
    assert sv.build_parser().parse_args([]).cache_release_s == 5.0
    assert sv.build_parser().parse_args(["--cache-release-s", "0"]).cache_release_s == 0
    for bad in ("5.5", "-1"):
        monkeypatch.setattr(sys, "argv", ["sync_video.py", "--cache-release-s", bad])
        assert sv.main() == 2
        assert "--cache-release-s" in capsys.readouterr().err


def test_host_stats_has_cached_and_dirty_mb(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable:  204800 kB\nCached:  165888 kB\nDirty:  2560 kB\n"
                   "CmaFree:  10240 kB\n")
    stop = threading.Event()

    def run(*a, **k):
        stop.set()  # one sample
        return subprocess.CompletedProcess(a, 0, stdout="throttled=0x0\n")
    sv.host_sampler(tmp_path / "host_stats.csv", stop, meminfo=mem, run=run)
    head, row = (tmp_path / "host_stats.csv").read_text().splitlines()
    cols = dict(zip(head.split(","), row.split(",")))
    assert cols["cached_mb"] == "162" and cols["dirty_mb"] == "2"
    assert cols["cma_free_mb"] == "10" and cols["throttled"] == "0x0"
