#!/usr/bin/env python3
"""board_hashes.py — read back the sha256 of every service file on an OpenMV board, vs this tree.

Purpose: pin the board build on a test card (docs/pool2_20261021/TEST_CARD_B.md §2): after
  ``host_tools/deploy_openmv.py``, prove each board runs exactly this tree's files. The file list
  is deploy_openmv's own manifest. One mpremote connection per board (``fs sha256sum`` for every
  file, then ``reset``), because a second raw-REPL entry is flaky on the AE3 and mpremote leaves
  a board at its REPL unless it is reset.

Usage (Pi, repo root):
  python scripts/board_hashes.py --board n6 --serial 10003500025043364d343000 [--no-reset]
Prints ``<sha256>  <board file>  <match|DIFF|missing>`` per file; exit 0 only if all match.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from host_tools.deploy_openmv import BOARD_MANIFESTS  # noqa: E402
from host_tools.discover_openmv import find_port  # noqa: E402


def parse_sums(text: str) -> dict[str, str]:
    """``mpremote fs sha256sum`` output -> {board filename: sha256}. Pure. mpremote 1.29 prints a
    ``sha256sum :<file>`` line, then the hash on its own line (seen on nereus002, 2026-10-17)."""
    out, name = {}, None
    for line in text.splitlines():
        m = re.match(r"sha256sum :/?(?:flash/)?([\w.\-]+)\s*$", line.strip())
        if m:
            name = m.group(1)
        elif name and re.fullmatch(r"[0-9a-f]{64}", line.strip()):
            out[name], name = line.strip(), None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", required=True, choices=sorted(BOARD_MANIFESTS))
    ap.add_argument("--serial", default=None)
    ap.add_argument("--no-reset", dest="reset", action="store_false")
    a = ap.parse_args()
    port = find_port(a.serial)
    if not port:
        print(f"board_hashes: no board for serial {a.serial}")
        return 2
    manifest = BOARD_MANIFESTS[a.board]
    cmd = [sys.executable, "-m", "mpremote", "connect", port, "fs", "sha256sum",
           *(":" + dest for _, dest in manifest)] + (["+", "reset"] if a.reset else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    got = parse_sums(r.stdout + r.stderr)
    ok = r.returncode == 0
    for rel, dest in manifest:
        want = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        have = got.get(dest)
        state = "missing" if have is None else ("match" if have == want else "DIFF")
        ok &= state == "match"
        print(f"{have or '-' * 64}  {dest}  {state}")
    print(f"board_hashes {a.board} {a.serial} {port}: {'ALL MATCH' if ok else 'MISMATCH'}"
          f"{'' if r.returncode == 0 else f' (mpremote rc={r.returncode})'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
