"""Card V3 through the colour pipeline (SPEC §4 Phase 8 Card V3, OQ-47): the four V3 card YAMLs
load through the pipeline's card code, and the synthetic c1 example frame is located and sampled
by ``locate`` and ``patches`` where its ground truth says."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.water_model import grey_reflectance

cv2 = pytest.importorskip("cv2")

from nereus_camera_test_rig.color.jpeg_geometry import JpegMap  # noqa: E402
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec  # noqa: E402
from nereus_camera_test_rig.color.patches import homography, sample_jpeg  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CARDS = [REPO / "configs" / "cards" / f"nereus_v3_c{n}.yaml" for n in range(1, 5)]
EXAMPLE = REPO / "tests" / "fixtures" / "reference_card_v3" / "nereus_v3_c1_example_1280x800"
SAME = JpegMap.offset(0, 0)  # the example is a plain image: "RAW" and image coordinates agree


@pytest.mark.parametrize("path", CARDS, ids=lambda p: p.stem)
def test_v3_cards_load_through_the_pipeline_card_code(path):
    card = load_card(path)
    spec = tag_spec(card)
    assert spec.family == "DICT_APRILTAG_25h9"
    lo, hi = spec.ratio_range
    assert lo < card.quad_ratio < hi and card.quad_ratio < 2.5  # outside V2's old 2.5–6.0 range
    rho = grey_reflectance(card)
    assert set(rho) >= {p.id for p in card.group("grey")}
    # design reflectances (V3 has no white patch; the print is measured later, OQ-40):
    # light .58, light-2 .35, mid .18 (= surround), black 0
    got = [rho[g] for g in ("gray_light", "gray_light2", "gray_mid", "gray_black")]
    np.testing.assert_allclose(got, [0.578, 0.352, 0.181, 0.0], atol=0.001)


def test_c1_example_frame_is_located_and_sampled_where_the_ground_truth_says():
    card = load_card(CARDS[0])
    truth = json.loads(EXAMPLE.with_suffix(".json").read_text())
    png = EXAMPLE.with_suffix(".png")

    rec = locate_frame(None, png, card.corner_map, SAME, geometry=tag_geometry(card),
                       spec=tag_spec(card))
    assert rec["located"] and rec["locate_method"] == "apriltag4", rec
    order = [card.corner_map[k] for k in ("tl", "tr", "br", "bl")]
    expected = np.array([truth["tags"][str(t)]["center"] for t in order])
    assert np.abs(np.array(rec["quad_raw"]) - expected).max() < 1.5  # 0.92 px when written

    H = homography(card, np.array(rec["quad_raw"]))
    for pid, (x, y) in truth["patch_centers"].items():
        b = card.patch(pid).box
        c = cv2.perspectiveTransform(np.array([[[b.x + b.w / 2, b.y + b.h / 2]]]), H)[0, 0]
        assert np.hypot(c[0] - x, c[1] - y) < 1.5, pid  # 1.03 px when written

    # the example is rendered from the card's design colours (V3 is not measured yet)
    sampled = sample_jpeg(png, rec["quad_raw"], card, SAME)["patches"]
    for p in card.patches:
        assert sampled[p.id]["n_px"] > 50, p.id
        np.testing.assert_allclose(sampled[p.id]["mean"], p.truth, atol=1.0, err_msg=p.id)
