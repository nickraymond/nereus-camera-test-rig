"""host_tools.received_card_score on the synthetic V3 c1 scene (the print master placed in a
1280x800 frame): the card is found and, scored against the design, its colours come back."""

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("cv2")

from host_tools import received_card_score as R  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/reference_card_v3"
SCENE = FIXTURES / "nereus_v3_c1_example_1280x800.png"


def test_scores_the_synthetic_scene_against_design(tmp_path):
    rc = R.main([str(SCENE), "--truth", "design", "--output-dir", str(tmp_path)])
    assert rc == 0
    rows = list(csv.DictReader(open(tmp_path / "received_scores.csv")))
    assert len(rows) == 1 and rows[0]["path"] == "display" and not rows[0]["error"]
    assert rows[0]["tags"] == "0 1 2 3"
    assert float(rows[0]["de00_mean"]) < 2.0          # rendered from the design values
    assert json.loads((tmp_path / "received_scores.json").read_text())["truth"].startswith("design")
    assert (tmp_path / "index.html").stat().st_size > 1000
    patches = list(csv.DictReader(open(tmp_path / "patches.csv")))
    assert len(patches) == 12


def test_a_missing_card_is_a_reported_failure(tmp_path):
    blank = tmp_path / "blank.png"
    import cv2
    import numpy as np

    cv2.imwrite(str(blank), np.full((400, 600, 3), 128, np.uint8))
    rc = R.main([str(blank), "--output-dir", str(tmp_path / "out")])
    assert rc == 1
    rows = list(csv.DictReader(open(tmp_path / "out" / "received_scores.csv")))
    assert "not found" in rows[0]["error"]
