"""scripts/pool_mode.sh arm/disarm in a sandbox: temp HOME, fake crontab + lifepo4wered-cli.
The field-safety contract (QE #97): disarm restores the dive-autostart flag's pre-arm state
and never removes a stand-down flag Nick made himself; other crontab lines survive."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pool_mode.sh"

FAKE_CRONTAB = """#!/bin/bash
f="$HOME/.fake_crontab"
case "$1" in
  -l) [ -f "$f" ] && cat "$f" || { echo "no crontab for pi" >&2; exit 1; } ;;
  -r) rm -f "$f" ;;
  -) cat >"$f" ;;
  *) cp "$1" "$f" ;;
esac
"""


@pytest.fixture()
def sandbox(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    home, fake = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    fake.mkdir()
    (fake / "crontab").write_text(FAKE_CRONTAB)
    (fake / "lifepo4wered-cli").write_text("#!/bin/bash\necho 1791930000\n")
    if shutil.which("sha256sum") is None:  # macOS: shasum has the same output format
        (fake / "sha256sum").write_text('#!/bin/bash\nexec shasum -a 256 "$@"\n')
    for f in fake.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "POOL_MODE_PATH_PREFIX": str(fake),
           "POOL_PYTHON": sys.executable}

    def run(*args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                              text=True, timeout=60)
    return home, run


def test_arm_disarm_creates_then_removes_an_absent_flag(sandbox):
    home, run = sandbox
    (home / ".fake_crontab").write_text("0 3 * * * echo nick-job\n")
    r = run("arm", "--hours", "6")
    assert r.returncode == 0, r.stdout + r.stderr
    flag = home / ".no_dive_autostart"
    assert flag.is_file() and "pool_mode.sh" in flag.read_text()
    cron = (home / ".fake_crontab").read_text()
    assert "nick-job" in cron and "POOL_MODE" in cron and "@reboot" in cron
    assert (home / ".pool_armed").read_text().split() == ["--hours", "6"]
    r = run("disarm")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not flag.exists()
    assert (home / ".fake_crontab").read_text() == "0 3 * * * echo nick-job\n"
    assert not (home / ".pool_armed").exists()


def test_disarm_never_removes_nicks_own_stand_down(sandbox):
    home, run = sandbox
    flag = home / ".no_dive_autostart"
    flag.write_text("")  # Nick's own `touch ~/.no_dive_autostart`
    assert run("arm").returncode == 0
    assert flag.read_text() == ""  # not adopted, not modified
    assert (home / ".pool_armed").read_text() == ""  # no args: empty, never a blank arg
    r = run("disarm")
    assert r.returncode == 0 and flag.exists() and flag.read_text() == ""
    assert "left" in r.stdout


def test_rearm_keeps_ownership_of_pool_modes_flag(sandbox):
    home, run = sandbox
    assert run("arm").returncode == 0
    assert run("arm", "--hours", "2").returncode == 0  # second arm: flag is "ours"
    assert run("disarm").returncode == 0
    assert not (home / ".no_dive_autostart").exists()
