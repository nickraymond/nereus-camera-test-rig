"""scripts/capture_raw_imx708.py against a fake rpicam-still: meter → lock → RAW → verify."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("capture_raw_imx708",
                                              ROOT / "scripts" / "capture_raw_imx708.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

# Echoes the locked controls back as metadata (or the "auto" choice when unlocked); writes a
# JPEG and, with --raw, a DNG the size of a 16-bit mode frame. FAKE_GAIN_ERROR skews gain.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a == ["--version"]:
    print("rpicam-apps build: fake"); sys.exit(0)
def val(flag, default=None):
    return a[a.index(flag) + 1] if flag in a else default
out = val("-o"); w, h = (int(v) for v in val("--mode").split(":"))
open(out, "wb").write(b"\xff\xd8fake")
if "--raw" in a:
    open(out[:-4] + ".dng", "wb").write(b"\0" * (w * h * 2 + 1000))
gain = float(val("--gain", 2.0)) + float(os.environ.get("FAKE_GAIN_ERROR", 0))
meta = {"ExposureTime": int(val("--shutter", 20000)), "AnalogueGain": gain,
        "DigitalGain": 1.0,
        "ColourGains": [float(v) for v in val("--awbgains", "1.8,1.6").split(",")],
        "LensPosition": float(val("--lens-position", 1.5)), "Lux": 400}
json.dump(meta, open(val("--metadata"), "w"))
'''


@pytest.fixture
def fake_rpicam(tmp_path):
    path = tmp_path / "rpicam-still"
    path.write_text(FAKE.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    path.chmod(0o755)
    return str(path)


def test_meters_then_locks_and_verifies_every_stop(tmp_path, fake_rpicam):
    out = tmp_path / "run"
    rc = cap.main(["--rpicam", fake_rpicam, "--mode", "64:36", "--out", str(out),
                   "--meter-ms", "0", "--shot-ms", "0"])
    s = json.loads((out / "capture_raw.json").read_text())
    assert rc == 0 and s["passed"]
    # metered 20000 µs at gain 2.0 → 40000 µs at gain 1.0, then −1 / 0 / +1 stop
    assert [x["requested"]["ExposureTime"] for x in s["shots"]] == [20000, 40000, 80000]
    assert all(x["requested"]["ColourGains"] == [1.8, 1.6] for x in s["shots"])
    assert all(x["requested"]["LensPosition"] == 1.5 for x in s["shots"])
    assert {p.name for p in out.iterdir()} >= {"meter.jpg", "stop_+0.dng", "stop_+1.json"}


def test_a_readback_mismatch_fails_and_is_recorded(tmp_path, fake_rpicam, monkeypatch):
    monkeypatch.setenv("FAKE_GAIN_ERROR", "0.2")  # the camera does not honour --gain
    out = tmp_path / "run"
    rc = cap.main(["--rpicam", fake_rpicam, "--mode", "64:36", "--out", str(out),
                   "--meter-ms", "0", "--shot-ms", "0", "--stops", "0"])
    s = json.loads((out / "capture_raw.json").read_text())
    assert rc == 1 and not s["passed"]
    assert s["shots"][0]["checks"]["readback_AnalogueGain"] is False


def test_never_overwrites_a_run(tmp_path, fake_rpicam):
    (tmp_path / "run").mkdir()
    with pytest.raises(FileExistsError):
        cap.main(["--rpicam", fake_rpicam, "--mode", "64:36", "--out", str(tmp_path / "run")])
