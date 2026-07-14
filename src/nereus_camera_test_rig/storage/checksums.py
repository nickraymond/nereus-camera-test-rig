"""File checksums — Spec §5, §10.

SHA-256 over capture artifacts, used both to record evidence integrity in metadata
and to verify OpenMV file transfers (Spec §10). Reads in chunks so large images and
videos don't have to be held in memory at once.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024  # 1 MiB


def sha256_file(path: str | Path) -> str:
    """Return the hex SHA-256 digest of the file at ``path``.

    Raises ``FileNotFoundError`` if the path does not exist.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"cannot checksum missing file: {p}")
    digest = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of an in-memory bytes buffer."""
    return hashlib.sha256(data).hexdigest()
