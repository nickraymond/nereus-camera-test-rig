"""``grvi`` stage (SPEC §4 Phase 8 S2a baseline a, OQ-31): exports the backend at a pinned
commit with ``git archive``, runs the runner in a separate interpreter, and samples the output
on the same card area as every other method. A fake backend stands in for GRVI."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from host_tools.grvi_baseline import grvi

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.patches import canonical_tag_quad
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
CARD_PATH = REPO / "configs" / "cards" / "nereus_v2.yaml"
CARD = load_card(CARD_PATH)
TEMPLATE = REPO / "tests" / "fixtures" / "reference_card" / "reference_card_template_3000x1000.png"

FAKE_GRVI = '''
from pathlib import Path
UPSTREAM_COMMIT = "fake"
DEFAULT_PROFILE, DEFAULT_LAYOUT = Path("fake_profile.json"), Path("fake_layout.json")
def load_profile(): return {}
def load_layout(): return {}
def runtime_versions(): return {"python": "x"}
def pipeline_constants(profile): return {}
def correct(img, profile, layout):
    if img[0, 0, 0] == 7:  # the "no card" frame
        return None, {"card_detected": False, "tag_count": 0}
    return 255 - img, {"card_detected": True, "tag_count": 4}   # an obvious transform
def encode_jpeg(rgb):
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.fromarray(rgb).save(buf, format="JPEG", quality=98)
    return buf.getvalue()
'''


def _fake_backend(path: Path) -> str:
    pkg = path / "backend" / "app" / "services" / "processing"
    pkg.mkdir(parents=True)
    for d in (path / "backend", path / "backend" / "app", path / "backend" / "app" / "services",
              pkg):
        (d / "__init__.py").write_text("")
    (pkg / "grvi.py").write_text(FAKE_GRVI)
    (path / "other.txt").write_text("not exported")
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "fake"], check=True)
    return subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_grvi_stage_runs_the_backend_and_samples_its_output(tmp_path):
    sha = _fake_backend(tmp_path / "backend_repo")
    ds, root = tmp_path / "ds", tmp_path / "out"
    (ds / "ref").mkdir(parents=True)
    view = cv2.getPerspectiveTransform(np.float32([[0, 0], [2999, 0], [2999, 999], [0, 999]]),
                                       np.float32([[100, 100], [1100, 120], [1080, 450],
                                                   [110, 440]]))
    img = cv2.warpPerspective(cv2.imread(str(TEMPLATE)), view, (1200, 600))
    cv2.imwrite(str(ds / "ref" / "A1.JPG"), img, [cv2.IMWRITE_JPEG_QUALITY, 98])
    img[0, 0] = 7
    cv2.imwrite(str(ds / "ref" / "N1.png"), img)  # lossless, so the marker pixel survives
    ingest_dir, locate_dir = root / "ingest", root / "locate"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "jpeg"])
        w.writeheader()
        w.writerows([{"stem": "A1", "jpeg": "ref/A1.JPG"}, {"stem": "N1", "jpeg": "ref/N1.png"},
                     {"stem": "X1", "jpeg": "ref/A1.JPG"}])
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    quad = cv2.perspectiveTransform(canonical_tag_quad(CARD).reshape(4, 1, 2), view).reshape(4, 2)
    locate_dir.mkdir()
    (locate_dir / "corners.json").write_text(json.dumps(
        {s: {"located": True, "quad_raw": (quad + 8).tolist()} for s in ("A1", "N1", "X1")}))
    write_stage(locate_dir, "locate", upstream=[ingest_dir], params={"jpeg_from_raw": {
        "centre_raw": [0, 0], "centre_jpeg": [-8, -8], "k": [1.0],
        "exclude_frames": {"X1": "shifted"}}})
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("dataset: synthetic\n")

    summary = grvi(locate_dir, cfg, CARD_PATH, tmp_path / "backend_repo", "HEAD",
                   Path(sys.executable), workers=1, grvi_workers=1)
    assert summary["backend_sha"] == sha and summary["frames"] == 2
    assert summary["card_detected"] == 1 and summary["no_card"] == ["N1"]
    assert summary["skipped_jpeg_excluded"] == ["X1"] and summary["errors"] == {}
    data = json.loads((Path(summary["out_dir"]) / "patches.json").read_text())
    assert data["N1"] == {"no_card": True}
    for p in CARD.patches:  # the fake inverts the image; sampled on the right card area
        np.testing.assert_allclose(data["A1"]["patches"][p.id]["mean"],
                                   255 - np.asarray(p.design or p.truth), atol=4, err_msg=p.id)
    record = verify_fresh(Path(summary["out_dir"]))
    assert record["params"]["backend_sha"] == sha
    assert not (Path(summary["out_dir"]) / "other.txt").exists()
