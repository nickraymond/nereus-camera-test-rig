"""Host tool: deploy the OpenMV capture service to a board — Spec §10, §15.

Copies the board Python files onto the OpenMV filesystem over USB and resets the board
so ``main.py`` (the capture service) starts. Transfer uses ``mpremote`` (official
MicroPython tooling) over the raw REPL — this is a **dev/flash-time** action, not a
runtime capture path, so it is exempt from the "no arbitrary remote execution" rule that
governs the runtime JSON protocol (§8).

Board files are deployed **flat** to the board root (the board imports them as
``import command_protocol`` etc.), while in the repo they live under ``openmv/common``
(shared) and ``openmv/<board>`` (board-specific). Only the matching board's
``board_config.py``/``main.py``/``boot.py`` are sent, so there is no ``if board == ...``
logic on the device (§6).

Usage::

    python -m host_tools.deploy_openmv --board n6                 # single board on USB
    python -m host_tools.deploy_openmv --board n6 --serial 005537493543
    python -m host_tools.deploy_openmv --board n6 --port /dev/ttyACM0 --no-reset
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from host_tools.discover_openmv import find_port  # noqa: E402

# Board file manifest: (repo path relative to root, board destination filename).
# Shared modules first, board-specific next, main.py LAST so a mid-deploy reset can
# never launch a new main against stale modules.
_COMMON = [
    ("openmv/common/command_protocol.py", "command_protocol.py"),
    ("openmv/common/device_info.py", "device_info.py"),
    ("openmv/common/capture_service.py", "capture_service.py"),
]
BOARD_MANIFESTS = {
    "n6": _COMMON + [
        ("openmv/n6/board_config.py", "board_config.py"),
        ("openmv/n6/boot.py", "boot.py"),
        ("openmv/n6/main.py", "main.py"),
    ],
}


class DeployError(RuntimeError):
    pass


def _mpremote_base(mpremote):
    """Resolve the mpremote invocation to a command list.

    Default is ``<this python> -m mpremote`` so it works whether or not an ``mpremote``
    console script is on PATH (it usually is not when the venv isn't activated). A
    caller-supplied string is split with shell rules for override flexibility.
    """
    if mpremote is None:
        return [sys.executable, "-m", "mpremote"]
    return shlex.split(mpremote)


def _run_mpremote(base, port: str, *args: str) -> None:
    cmd = [*base, "connect", port, *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise DeployError(
            "mpremote failed (%d): %s :: %s" % (result.returncode, " ".join(cmd), " | ".join(tail))
        )


def deploy(board: str, serial_number=None, port=None, mpremote=None, reset=True):
    """Copy the board's file manifest and (optionally) reset it. Returns the port used."""
    manifest = BOARD_MANIFESTS.get(board)
    if manifest is None:
        raise DeployError("unknown board %r; known: %s" % (board, ", ".join(BOARD_MANIFESTS)))

    resolved = port or find_port(serial_number)
    if not resolved:
        raise DeployError(
            "no OpenMV board found (serial_number=%r); is it connected?" % serial_number
        )

    base = _mpremote_base(mpremote)
    for rel_path, dest_name in manifest:
        src = _REPO_ROOT / rel_path
        if not src.is_file():
            raise DeployError("missing board file: %s" % src)
        print("deploy %s -> :%s" % (rel_path, dest_name))
        _run_mpremote(base, resolved, "fs", "cp", str(src), ":" + dest_name)

    if reset:
        print("reset board")
        _run_mpremote(base, resolved, "reset")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy the OpenMV capture service (Spec §10).")
    parser.add_argument("--board", default="n6", choices=sorted(BOARD_MANIFESTS),
                        help="which board's manifest to deploy")
    parser.add_argument("--serial", dest="serial_number", default=None,
                        help="USB serial number of the target board (from discover_openmv)")
    parser.add_argument("--port", default=None, help="explicit device path (overrides --serial)")
    parser.add_argument("--mpremote", default=None,
                        help="mpremote command (default: '<python> -m mpremote')")
    parser.add_argument("--no-reset", dest="reset", action="store_false",
                        help="do not reset the board after copying")
    args = parser.parse_args(argv)

    try:
        port = deploy(
            board=args.board, serial_number=args.serial_number, port=args.port,
            mpremote=args.mpremote, reset=args.reset,
        )
    except DeployError as exc:
        print("deploy failed: %s" % exc, file=sys.stderr)
        return 1
    print("deployed %s service to %s" % (args.board, port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
