"""``measure-card`` (SPEC §20: real-world measurements over design files): the printed card is read
in air, white-balanced on a common anchor grey; the measured block replaces the design values as
the reference everywhere, while the design values stay visible for comparison."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
import yaml

from nereus_camera_test_rig.color.card import CardError, load_card
from nereus_camera_test_rig.color.measure_card import linear_to_srgb8, measure, measure_card
from nereus_camera_test_rig.color.metrics import srgb8_to_linear
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage
from nereus_camera_test_rig.color.water_model import grey_reflectance

REPO = Path(__file__).resolve().parents[2]
CARD_PATH = REPO / "configs" / "cards" / "nereus_v2.yaml"


def _design_card_yaml(tmp: Path) -> Path:
    """The V2 card with design values only (the repo YAML carries a measured block)."""
    data = yaml.safe_load(CARD_PATH.read_text())
    data.pop("measured", None)
    path = tmp / "nereus_v2_design.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


DESIGN_PATH = _design_card_yaml(Path(tempfile.mkdtemp()))
CARD = load_card(DESIGN_PATH)


def printed(pid: str) -> np.ndarray:
    """A print paler than its design: colours pulled 40 % towards their own grey level."""
    d = srgb8_to_linear(np.asarray(CARD.patch(pid).truth, float))
    return d if CARD.patch(pid).group == "grey" else 0.6 * d + 0.4 * d.mean()


def frame(light=(0.7, 1.0, 0.9), exclude=()) -> tuple[dict, dict]:
    raw = {p.id: {"mean": (printed(p.id) * light).tolist(), "clip_frac": [0, 0, 0]}
           for p in CARD.patches}
    for s in CARD.sub_patches:
        raw[s.id] = raw[s.parent]
    q = {pid: {"usable": pid not in exclude} for pid in raw}
    return q, raw


def test_measure_recovers_the_print_and_quantifies_the_design_gap():
    frames = {"A": frame(), "B": frame(light=(0.5, 1.0, 1.2), exclude=("gray_mid",))}
    result = measure(CARD, frames, np.eye(3))
    assert result["anchor"] == "gray_mid_right"  # gray_mid not usable in B: common anchor
    assert result["scale_anchor"] == "gray_mid" and result["values"]["gray_mid"][0] == 128.0
    for p in CARD.group("color"):  # the synthetic print's greys equal the design, so no rescale
        np.testing.assert_allclose(result["values"][p.id], linear_to_srgb8(printed(p.id)),
                                   atol=0.1, err_msg=p.id)
    yellow = result["patches"]["yellow"]
    assert yellow["dC"] < -10 and yellow["de2000_vs_design"] > 5 and yellow["n_frames"] == 2
    assert result["patches"]["gray_mid"]["n_frames"] == 2  # via its usable right half in B
    assert result["values"]["gray_light"][0] == result["values"]["gray_light"][1]  # neutral


def test_the_measured_block_becomes_the_reference_and_keeps_the_design(tmp_path):
    data = yaml.safe_load(DESIGN_PATH.read_text())
    data["measured"] = {"source": "measured_test", "values": {"yellow": [200.0, 180.5, 90.0],
                                                             "gray_light": [190.0] * 3}}
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(data))
    card = load_card(path)
    assert card.truth_is_measured and card.truth_source == "measured_test"
    assert card.design_source == CARD.truth_source
    assert card.patch("yellow").truth == (200.0, 180.5, 90.0)
    assert card.patch("yellow").design == CARD.patch("yellow").truth
    assert card.patch("cyan").truth == CARD.patch("cyan").truth
    assert card.patch("cyan").design is None
    assert grey_reflectance(card)["gray_light"] == pytest.approx(srgb8_to_linear(190.0))
    data["measured"]["values"]["nope"] = [1, 2, 3]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(CardError, match="unknown patch ids"):
        load_card(path)


def test_measure_card_stage_writes_a_block_that_matches_once_pasted(tmp_path):
    root = tmp_path / "ds"
    (root / "qc").mkdir(parents=True)
    (root / "patches").mkdir()
    q, raw = frame()
    (root / "qc" / "qc.json").write_text(json.dumps({"AIR": {"patches": q}}))
    (root / "patches" / "patches.json").write_text(json.dumps({"AIR": {"raw": {"patches": raw}}}))
    write_stage(root / "qc", "qc")
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("dataset: synthetic\nmedium: {default: water, air: [AIR]}\n")
    calib = tmp_path / "cam.yaml"
    calib.write_text(yaml.safe_dump({"camera_id": "cam", "color_matrix": {
        "matrix": np.eye(3).tolist()}}))
    summary = measure_card(root / "qc", DESIGN_PATH, calib, cfg)
    assert summary["reference_frames"] == ["AIR"] and not summary["config_matches"]
    assert summary["median_de2000_vs_design"]["color"] > 5
    block = yaml.safe_load(summary["config_block"])["measured"]
    assert block["reference_frames"] == ["AIR"] and "yellow" in block["values"]
    card_yaml = tmp_path / "card.yaml"
    card_yaml.write_text(DESIGN_PATH.read_text() + "\n" + summary["config_block"])
    again = measure_card(root / "qc", card_yaml, calib, cfg)
    assert again["config_matches"]  # the design is still the anchor, so values are stable
    verify_fresh(Path(again["out_dir"]))


def test_the_brightness_scale_is_set_on_the_first_wb_anchor():
    q, raw = frame()
    raw["gray_dark"] = {"mean": (np.asarray(raw["gray_dark"]["mean"]) * 1.4).tolist(),
                        "clip_frac": [0, 0, 0]}  # dark grey printed 40 % too light
    q2, raw2 = frame(exclude=("gray_mid", "gray_mid_right"))
    raw2["gray_dark"] = raw["gray_dark"]
    result = measure(CARD, {"A": (q, raw), "B": (q2, raw2)}, np.eye(3))
    assert result["anchor"] == "gray_dark" and result["scale_anchor"] == "gray_mid"
    assert result["values"]["gray_mid"][0] == pytest.approx(128.0, abs=0.1)
    assert result["values"]["gray_dark"][0] > 74 + 10  # measured lighter than its design
