"""``ingest`` stage + solar elevation (SPEC §4 Phase 8 S1) on a tiny synthetic dataset with
fake metadata — the "no code changes for a new dataset" path, without the real files."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from nereus_camera_test_rig.color.ingest import ingest
from nereus_camera_test_rig.color.stages import StaleInputError, verify_fresh
from nereus_camera_test_rig.color.sun import solar_declination_and_eot, solar_elevation_deg
from nereus_camera_test_rig.config import ConfigError

T0 = datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc)
REF = "1_reference_A_iso100"

# stem: (category, minutes after T0, depth m, raw?)
SHOTS = {
    "S001": ("0_surface_card", 0.0, 0.2, True),
    "S002": (REF, 5.0, 15.0, True),     # sweep 1
    "S003": (REF, 5.5, 15.2, True),     # sweep 1 (30 s later)
    "S004": (REF, 6.0, 12.0, True),     # sweep 2: depth jump > 1.5 m
    "S005": (REF, 9.0, 12.1, True),     # sweep 3: > 90 s gap
    "S006": ("4_no_card", 10.0, 8.0, False),  # JPG only
    "S007": (REF, 600.0, 9.0, True),    # dive 2, sweep 4
    "S008": ("2_underwater_preset", 601.0, 9.0, True),
}


def make_dataset(tmp_path: Path, shots=SHOTS, hand_rows=None) -> Path:
    root = tmp_path / "ds"
    for stem, (cat, _, _, raw) in shots.items():
        d = root / "raw" / cat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.JPG").write_bytes(b"jpg" + stem.encode())
        if raw:
            (d / f"{stem}.dng").write_bytes(b"raw" + stem.encode())
    if hand_rows is not None:
        with (root / "manifest.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["stem", "category", "water_depth_m",
                                              "flash_fired", "notes"])
            w.writeheader()
            w.writerows(hand_rows)
    return root


def fake_metadata(shots=SHOTS):
    def read(paths):
        out = []
        for p in paths:
            _, minutes, depth, _ = shots[Path(p).stem]
            out.append({"path": str(p), "time_utc": (T0 + timedelta(minutes=minutes)).isoformat(),
                        "depth_m": depth, "exposure_s": 0.01, "iso": 100, "fnumber": 2.0,
                        "flash_fired": Path(p).stem == "S008", "black_level2": [257] * 4})
        return out
    return read


def write_config(tmp_path: Path, **overrides) -> Path:
    cfg = {"dataset": "synthetic", "camera": "test_cam",
           "dives": {1: {"site": "a", "start_utc": "2026-09-16T01:29:00Z",
                         "end_utc": "2026-09-16T01:45:00Z"},
                     2: {"site": "b", "start_utc": "2026-09-16T11:00:00Z",
                         "end_utc": "2026-09-16T11:40:00Z"}},
           "sites": {"a": {"lat": 34.0, "lon": -119.75}, "b": {"lat": 34.01, "lon": -119.4}}}
    cfg.update(overrides)
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def run(tmp_path, **kw):
    ds = make_dataset(tmp_path, hand_rows=kw.pop("hand_rows", None))
    summary = ingest(ds, kw.pop("config", None) or write_config(tmp_path), tmp_path / "out",
                     fake_metadata())
    rows = list(csv.DictReader(open(Path(summary["out_dir"]) / "manifest.csv")))
    return summary, {r["stem"]: r for r in rows}


def test_manifest_dives_sweeps_and_jpeg_only(tmp_path):
    summary, rows = run(tmp_path)
    assert summary["shots"] == 8 and summary["with_raw"] == 7
    assert summary["dives"] == 2 and summary["sweeps"] == 4 and summary["flash_fired"] == 1
    assert summary["dataset_id"].startswith("synthetic-")
    assert [rows[s]["sweep_id"] for s in ("S002", "S003", "S004", "S005", "S007")] == \
        ["1", "1", "2", "3", "4"]
    assert rows["S001"]["sweep_id"] == "" and rows["S008"]["sweep_id"] == ""
    assert rows["S007"]["dive_id"] == "2" and rows["S007"]["site"] == "b"
    assert rows["S006"]["has_raw"] == "False" and rows["S006"]["file"].endswith("S006.JPG")
    assert rows["S002"]["file"] == "raw/1_reference_A_iso100/S002.dng"
    assert rows["S002"]["black_level"] == "257 257 257 257"
    assert -10 < float(rows["S002"]["sun_elevation_deg"]) < 10  # near sunset, Santa Cruz


def test_stage_json_tracks_the_dataset_config(tmp_path):
    cfg = write_config(tmp_path)
    summary, _ = run(tmp_path, config=cfg)
    out = Path(summary["out_dir"])
    assert verify_fresh(out)["params"]["dataset_id"] == summary["dataset_id"]
    cfg.write_text(cfg.read_text() + "# edited\n")
    with pytest.raises(StaleInputError, match="changed"):
        verify_fresh(out)


def test_dataset_id_changes_when_a_file_changes(tmp_path):
    first, _ = run(tmp_path)
    (tmp_path / "ds" / "raw" / REF / "S002.dng").write_bytes(b"different length")
    second = ingest(tmp_path / "ds", write_config(tmp_path), tmp_path / "out", fake_metadata())
    assert first["dataset_id"] != second["dataset_id"]


def test_dive_count_and_windows_are_enforced(tmp_path):
    one_dive = write_config(tmp_path, dives={1: {"site": "a", "start_utc": "2026-09-16T00:00:00Z",
                                                 "end_utc": "2026-09-17T00:00:00Z"}})
    with pytest.raises(ConfigError, match="found 2 dives"):
        run(tmp_path, config=one_dive)
    tight = write_config(tmp_path, dives={
        1: {"site": "a", "start_utc": "2026-09-16T01:31:00Z", "end_utc": "2026-09-16T01:45:00Z"},
        2: {"site": "b", "start_utc": "2026-09-16T11:00:00Z", "end_utc": "2026-09-16T11:40:00Z"}})
    with pytest.raises(ConfigError, match="dive 1 .* outside"):
        run(tmp_path, config=tight)


def test_hand_manifest_notes_carried_and_disagreements_reported(tmp_path):
    hand = [{"stem": s, "category": c, "water_depth_m": d, "flash_fired": str(s == "S008"),
             "notes": ""} for s, (c, _, d, _) in SHOTS.items()]
    hand[1]["notes"] = "card tilted"
    hand[2]["water_depth_m"] = 99.0
    summary, rows = run(tmp_path, hand_rows=hand)
    assert rows["S002"]["notes"] == "card tilted"
    assert summary["cross_check_issues"] == ["S003: depth 15.2 vs hand 99.0"]


def test_a_stem_in_two_categories_fails(tmp_path):
    ds = make_dataset(tmp_path)
    (ds / "raw" / "4_no_card" / "S002.JPG").write_bytes(b"dup")
    with pytest.raises(ConfigError, match="two categories"):
        ingest(ds, write_config(tmp_path), tmp_path / "out", fake_metadata())


def test_summary_is_json(tmp_path):
    summary, _ = run(tmp_path)
    assert json.loads((Path(summary["out_dir"]) / "summary.json").read_text())["sweeps"] == 4


@pytest.mark.parametrize(
    "when, decl, tol",
    [
        ("2026-03-20T14:46:00", 0.0, 0.05),    # March equinox 2026
        ("2026-09-23T00:05:00", 0.0, 0.05),    # September equinox 2026
        ("2026-06-21T08:24:00", 23.436, 0.01),  # June solstice 2026 (obliquity)
    ],
)
def test_declination_at_equinoxes_and_solstice(when, decl, tol):
    t = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
    assert solar_declination_and_eot(t)[0] == pytest.approx(decl, abs=tol)


@pytest.mark.parametrize(
    "when, eot", [("2026-11-03T12:00:00", 16.4), ("2026-02-11T12:00:00", -14.2)]
)
def test_equation_of_time_extremes(when, eot):
    t = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
    assert solar_declination_and_eot(t)[1] == pytest.approx(eot, abs=0.15)


def test_noon_elevation_is_90_minus_latitude_plus_declination():
    lat, lon = 34.01, -119.40  # Anacapa
    day = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    samples = [day + timedelta(seconds=30 * k) for k in range(4 * 120)]
    peak_t = max(samples, key=lambda t: solar_elevation_deg(t, lat, lon))
    decl = solar_declination_and_eot(peak_t)[0]
    assert solar_elevation_deg(peak_t, lat, lon) == pytest.approx(90 - lat + decl, abs=0.02)
