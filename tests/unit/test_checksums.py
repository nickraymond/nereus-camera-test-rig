"""Unit tests for storage.checksums — Spec §5, §10."""

from __future__ import annotations

import hashlib

import pytest

from nereus_camera_test_rig.storage import checksums


def test_sha256_bytes_matches_hashlib():
    data = b"nereus reference card evidence"
    assert checksums.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_file_matches_bytes(tmp_path):
    data = b"\x00\x01\x02 binary-ish capture payload \xff\xfe"
    p = tmp_path / "capture.bin"
    p.write_bytes(data)
    assert checksums.sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_sha256_file_large_multichunk(tmp_path):
    # Exceed the 1 MiB read chunk to exercise the streaming path.
    data = b"x" * (checksums._CHUNK * 2 + 123)
    p = tmp_path / "big.bin"
    p.write_bytes(data)
    assert checksums.sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_sha256_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        checksums.sha256_file(tmp_path / "nope.bin")
