"""``color/`` import boundary (SPEC §20), checked at runtime.

An AST scan of ``color/*.py`` would miss modules pulled in indirectly (e.g. through
``analysis/``), so this imports the package in a fresh interpreter and inspects
``sys.modules``. Also guards that tests run *this checkout's* code: the shared venv's
editable install points at the primary checkout, which silently tests the wrong code from
a git worktree unless pytest's ``pythonpath`` puts this checkout's ``src`` first.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import nereus_camera_test_rig

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

COLOR_MODULES = ["nereus_camera_test_rig.color", "nereus_camera_test_rig.color.card"]

# Capture path, web stack, serial transport, and LGPL rawpy (Mac-only tools, SPEC §20).
FORBIDDEN = [
    "nereus_camera_test_rig.web",
    "nereus_camera_test_rig.cameras",
    "nereus_camera_test_rig.capture",
    "nereus_camera_test_rig.controller",
    "serial",
    "flask",
    "rawpy",
]


def _loaded_after_import(modules: list[str]) -> set[str]:
    code = (
        "import importlib, json, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), str(REPO)]))
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return set(json.loads(out.stdout))


def test_color_does_not_import_capture_web_serial_or_rawpy():
    loaded = _loaded_after_import(COLOR_MODULES)
    hits = sorted(m for m in loaded for f in FORBIDDEN if m == f or m.startswith(f + "."))
    assert not hits, f"color/ pulled in forbidden modules: {hits}"


def test_tests_run_this_checkouts_code():
    pkg = Path(nereus_camera_test_rig.__file__).resolve()
    assert pkg.is_relative_to(SRC), (
        f"tests imported {pkg}, not this checkout's {SRC} — check pytest `pythonpath`"
    )
