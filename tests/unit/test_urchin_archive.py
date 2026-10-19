"""E6: archive Nick's Reef Check zips to the SSD (copy, verify, unpack), on tiny local zips."""

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

    video = tmp_path / "ssd" / "video"
    assert az.cmd_unpack(dest, video) == 0
    assert (video / "South Wreck" / "c1.MP4").read_bytes() == b"v" * 1000
    assert (video / "October 2022" / "c2.MP4").exists()
    assert not (video / "October 2022" / "notes.txt").exists()
    assert not list(video.rglob("._*"))
    assert az.cmd_unpack(dest, video) == 0  # idempotent: identical files are not rewritten
    assert "identical_to_existing" in (video.parent / "unpack_ledger.tsv").read_text()

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


def test_unpack_keeps_subfolders_and_reads_only_recorded_zips(tmp_path):
    z = tmp_path / "s.zip"
    _zip(z, {"South Wreck/day1/c.MP4": b"a" * 10, "South Wreck/day2/c.MP4": b"b" * 10})
    dest = tmp_path / "dest"
    assert az.cmd_copy(dest, [z]) == 0
    _zip(dest / "stray.zip", {"X/stray.MP4": b"s"})  # not in zips.tsv: never unpacked
    video = tmp_path / "video"
    assert az.cmd_unpack(dest, video) == 0
    assert (video / "South Wreck" / "day1" / "c.MP4").read_bytes() == b"a" * 10
    assert (video / "South Wreck" / "day2" / "c.MP4").read_bytes() == b"b" * 10
    assert not (video / "X").exists()


def test_copy_refuses_a_broken_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 not really a zip")
    assert az.cmd_copy(tmp_path / "dest", [bad]) == 1
    assert not (tmp_path / "dest" / "bad.zip").exists()


def test_refuses_unmounted_volume(tmp_path):
    with pytest.raises(SystemExit, match="not mounted"):
        az.require_mounted(az.Path("/Volumes/__no_such_volume__/NereusData"))
    az.require_mounted(tmp_path / "x")  # not under /Volumes: no check
