"""E6: archive Nick's Reef Check zips to the SSD (copy, verify, unpack), on tiny local zips."""

import stat
import zipfile

import pytest
from edge_ml.urchin import archive_zips as az


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_copy_verify_unpack_roundtrip(tmp_path, capsys):
    src = tmp_path / "Downloads"
    src.mkdir()
    a, b = src / "South Wreck-1.zip", src / "Oct 2022-1.zip"
    _zip(a, {"South Wreck/c1.MP4": b"v" * 1000, "__MACOSX/South Wreck/._c1.MP4": b"x"})
    _zip(b, {"October 2022/c2.MP4": b"w" * 500, "October 2022/notes.txt": b"n"})
    before = {p: p.read_bytes() for p in (a, b)}
    dest = tmp_path / "ssd" / "zips"
    assert az.cmd_copy(dest, [a, b]) == 0
    assert az.cmd_copy(dest, [a]) == 0  # already recorded → skipped, not recopied
    assert "skip South Wreck-1.zip" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in (a, b)} == before  # sources untouched
    rows = (dest / "zips.tsv").read_text().splitlines()
    assert len(rows) == 3 and all(r.split("\t")[2] == r.split("\t")[3] for r in rows[1:])
    assert az.cmd_verify(dest, [a.name, b.name]) == 0
    assert not list(dest.glob("*.part"))

    run = tmp_path / "ssd" / "unpack"
    assert az.cmd_unpack(dest, run, [a.name, b.name], on_volume=False) == 0
    assert (run / "South Wreck" / "c1.MP4").read_bytes() == b"v" * 1000
    assert (run / "October 2022" / "c2.MP4").exists()
    assert sorted(p.name for p in run.rglob("*")) == sorted(
        [
            "MANIFEST.md",
            "plan.tsv",
            "unpack_ledger.tsv",
            "South Wreck",
            "c1.MP4",
            "October 2022",
            "c2.MP4",
        ]
    )  # notes.txt and the __MACOSX sidecar are listed as skipped, never written
    led = az._read_tsv(run / "unpack_ledger.tsv")
    assert [r["status"] for r in led] == ["unpacked", "unpacked"]
    assert led[0]["sha256"] == az.sha256(run / led[0]["group"] / led[0]["file"])
    man = (run / "MANIFEST.md").read_text()
    assert "2 videos, 1,500 B" in man and led[0]["sha256"] in man
    assert az.cmd_unpack(dest, run, [a.name, b.name], on_volume=False) == 0  # resume
    assert [r["status"] for r in az._read_tsv(run / "unpack_ledger.tsv")][2:] == [
        "skipped_identical",
        "skipped_identical",
    ]

    data = bytearray((dest / a.name).read_bytes())
    data[10] ^= 0xFF
    (dest / a.name).write_bytes(bytes(data))
    assert az.cmd_verify(dest, [a.name, b.name]) == 1


def test_verify_checks_every_expected_zip_not_just_the_records(tmp_path, capsys):
    zips = []
    for i in range(3):
        z = tmp_path / f"z{i}.zip"
        _zip(z, {f"F/c{i}.MP4": bytes([i]) * 100})
        zips.append(z)
    dest = tmp_path / "dest"
    assert az.cmd_copy(dest, zips[:2]) == 0  # a partial copy: 2 of 3
    names = [z.name for z in zips]
    assert az.cmd_verify(dest, names[:2]) == 0
    assert az.cmd_verify(dest, names) == 1
    assert "MISSING z2.zip (not recorded)" in capsys.readouterr().out
    (dest / "z1.zip").unlink()  # recorded but gone: reported, no crash
    assert az.cmd_verify(dest, names[:2]) == 1
    assert "MISSING z1.zip (recorded, file not found)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="expected zips"):
        az.cmd_verify(dest, [])


def test_copy_refuses_a_broken_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    assert az.cmd_copy(tmp_path / "dest", [bad]) == 1
    assert not (tmp_path / "dest" / "bad.zip").exists()


def test_refuses_unmounted_volume(tmp_path):
    with pytest.raises(SystemExit, match="not mounted"):
        az.require_mounted(az.Path("/Volumes/__no_such_volume__/NereusData"))
    az.require_mounted(tmp_path / "x")  # not under /Volumes: no check


# ------------------------------------------------- unpack (SPEC_e6_unpack_tierb rev 1)


def _recorded(tmp_path, members, name="z.zip"):
    """A zip copied (and so recorded in zips.tsv) into tmp_path/zips."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    z = src / name
    with zipfile.ZipFile(z, "w") as zf:
        for m, data in members.items():
            zf.writestr(m, data)
    dest = tmp_path / "zips"
    assert az.cmd_copy(dest, [z]) == 0
    return dest


def _symlink_info(name):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("/abs/evil.MP4", "absolute path"),
        ("SW/../../evil.MP4", "'..' part"),
        ("C:/evil.MP4", "absolute path"),
        (_symlink_info("SW/link.MP4"), "symlink"),
    ],
)
def test_unpack_rejects_zip_slip_and_symlinks_and_writes_nothing(tmp_path, capsys, bad, reason):
    dest = _recorded(tmp_path, {"SW/ok.MP4": b"o" * 10, bad: b"x" * 10})
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 1
    out = capsys.readouterr().out
    assert f"({reason})" in out and "REJECT z.zip" in out
    assert sorted(p.name for p in run.iterdir()) == ["MANIFEST.md", "plan.tsv"]  # no video
    assert not (tmp_path / "evil.MP4").exists() and not (tmp_path.parent / "evil.MP4").exists()
    plan = az._read_tsv(run / "plan.tsv")
    assert [r["decision"] for r in plan].count("reject") == 1
    assert "| reject | " + reason in (run / "MANIFEST.md").read_text()


def test_unpack_member_filter_and_flat_sanitised_names(tmp_path):
    members = {
        "South Wreck/day1/a.MP4": b"a",
        "South Wreck/b.mov": b"b",
        "South Wreck/c.Mp4": b"c",
        "South Wreck/d:e?.MOV": b"d",
        "South Wreck/notes.txt": b"n",
        "South Wreck/x.jpg": b"j",
        "South Wreck/._a.MP4": b"s",
        "__MACOSX/South Wreck/._b.mov": b"s",
        "South Wreck/": b"",
        "top.mp4": b"t",
    }
    dest = _recorded(tmp_path, members)
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 0
    got = sorted(str(p.relative_to(run)) for p in run.rglob("*") if p.is_file())
    assert got == sorted(
        [
            "MANIFEST.md",
            "plan.tsv",
            "unpack_ledger.tsv",
            "South Wreck/a.MP4",
            "South Wreck/b.mov",
            "South Wreck/c.Mp4",
            "South Wreck/d_e_.MOV",
            "_root/top.mp4",
        ]
    )
    reasons = {r["member"]: r["reason"] for r in az._read_tsv(run / "plan.tsv")}
    assert reasons["South Wreck/notes.txt"] == "not a video"
    assert reasons["South Wreck/._a.MP4"] == "hidden or AppleDouble"
    assert reasons["__MACOSX/South Wreck/._b.mov"] == "hidden or AppleDouble"
    assert reasons["South Wreck/"] == "directory"
    assert az.sanitise("South Wreck-21-7-30-0946-DanAbbott-f4.MP4") == (
        "South Wreck-21-7-30-0946-DanAbbott-f4.MP4"
    )  # real names pass unchanged


def test_unpack_flattening_collision_writes_nothing(tmp_path, capsys):
    dest = _recorded(tmp_path, {"SW/day1/c.MP4": b"a", "SW/day2/c.MP4": b"b"})
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 1
    assert "COLLISION SW/c.MP4" in capsys.readouterr().out
    assert not (run / "SW").exists()


def test_unpack_only_named_recorded_zips(tmp_path, capsys):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a"})
    with zipfile.ZipFile(dest / "stray.zip", "w") as zf:
        zf.writestr("X/stray.MP4", b"s")
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip", "stray.zip"], on_volume=False) == 1
    assert "stray.zip: not in zips.tsv" in capsys.readouterr().out
    assert not (run / "SW").exists()
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 0
    assert not (run / "X").exists()
    with pytest.raises(SystemExit, match="target zip names"):
        az.cmd_unpack(dest, run, [], on_volume=False)


def test_unpack_free_space_stop(tmp_path, capsys, monkeypatch):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a" * 100})
    real = az.shutil.disk_usage
    monkeypatch.setattr(
        az.shutil, "disk_usage", lambda p: real(p)._replace(free=az.UNPACK_MARGIN + 99)
    )
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 2
    assert "STOP:" in capsys.readouterr().out and not (run / "SW").exists()
    monkeypatch.setattr(
        az.shutil, "disk_usage", lambda p: real(p)._replace(free=az.UNPACK_MARGIN + 100)
    )
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 0


def test_unpack_same_sha_skips_different_sha_stops(tmp_path, capsys):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a" * 10, "SW/b.MP4": b"b" * 10})
    run = tmp_path / "run"
    (run / "SW").mkdir(parents=True)
    (run / "SW" / "a.MP4").write_bytes(b"a" * 10)  # identical: skipped, not rewritten
    (run / "SW" / "b.MP4").write_bytes(b"B" * 10)  # different: stop, never overwritten
    mtime = (run / "SW" / "a.MP4").stat().st_mtime_ns
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 1
    assert "exists with a different SHA-256" in capsys.readouterr().out
    assert (run / "SW" / "b.MP4").read_bytes() == b"B" * 10
    assert (run / "SW" / "a.MP4").stat().st_mtime_ns == mtime
    assert [r["status"] for r in az._read_tsv(run / "unpack_ledger.tsv")] == ["skipped_identical"]
    assert not list(run.rglob("*.part"))


def test_unpack_crc_failure_is_listed_and_the_rest_continue(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    z = src / "z.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("SW/a.MP4", b"A" * 64)
        zf.writestr("SW/b.MP4", b"B" * 64)
    dest = tmp_path / "zips"
    assert az.cmd_copy(dest, [z]) == 0  # recorded intact, then one data byte flips on disk
    data = bytearray((dest / "z.zip").read_bytes())
    data[data.index(b"A" * 64) + 5] = ord("X")
    (dest / "z.zip").write_bytes(bytes(data))
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 1
    led = {r["file"]: r["status"] for r in az._read_tsv(run / "unpack_ledger.tsv")}
    assert led["a.MP4"].startswith("crc_fail") and led["b.MP4"] == "unpacked"
    assert not (run / "SW" / "a.MP4").exists() and not list(run.rglob("*.part"))
    assert "crc_fail" in (run / "MANIFEST.md").read_text()


def test_unpack_stops_when_the_volume_unmounts(tmp_path, capsys, monkeypatch):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a", "SW/b.MP4": b"b"})
    calls = []

    def fake_mounted(p):  # 2 entry checks + 1 U6 watch + member 1 = mounted; then gone
        calls.append(p)
        return tmp_path if len(calls) <= 4 else None

    monkeypatch.setattr(az, "mounted_volume", fake_mounted)
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"]) == 2
    assert "no longer mounted" in capsys.readouterr().out
    assert [r["file"] for r in az._read_tsv(run / "unpack_ledger.tsv")] == ["a.MP4"]


def test_unpack_refuses_a_run_dir_off_the_ssd(tmp_path):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a"})
    with pytest.raises(SystemExit, match="not on a mounted /Volumes volume"):
        az.cmd_unpack(dest, tmp_path / "run", ["z.zip"])
    assert not (tmp_path / "run").exists()


def test_unpack_u6_new_path_outside_the_run_folder_fails(tmp_path, capsys, monkeypatch):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a"})
    run = tmp_path / "out" / "run"
    real = az._stream_member

    def leaky(zf, info, dst=None):
        (run.parent / "stray").write_text("x")
        return real(zf, info, dst)

    monkeypatch.setattr(az, "_stream_member", leaky)
    assert az.cmd_unpack(dest, run, ["z.zip"], on_volume=False) == 1
    out = capsys.readouterr().out
    assert "U6 FAIL" in out and str(run.parent / "stray") in out


def test_unpack_dry_run_writes_only_the_plan(tmp_path):
    dest = _recorded(tmp_path, {"SW/a.MP4": b"a" * 7})
    run = tmp_path / "run"
    assert az.cmd_unpack(dest, run, ["z.zip"], dry_run=True, on_volume=False) == 0
    assert sorted(p.name for p in run.iterdir()) == ["MANIFEST.md", "plan.tsv"]
    assert "1 videos, 7 B" in (run / "MANIFEST.md").read_text()
