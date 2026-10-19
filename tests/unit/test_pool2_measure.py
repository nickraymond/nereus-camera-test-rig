"""edge_ml/pool2/measure.py + layout.py on a synthetic board rendered through a known camera.

Rendered pinhole views use the guard lens (no distortion); test_distorted_view_* renders through
bm #184's N6 lens and measures with it (rev 16)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from edge_ml.pool2 import board, measure, pose
from edge_ml.pool2.layout import LayoutError, load_layout

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "edge_ml" / "pool2" / "board_layout.template.yaml"
PPM = 4.0  # board-image px per mm


def _layout(tmp_path, **over):
    data = yaml.safe_load(TEMPLATE.read_text())
    data.update(measured=True, source="test", **over)
    p = tmp_path / "layout.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_layout(p)


def _board_image(lay) -> tuple[np.ndarray, np.ndarray]:
    """The board drawn in mm at PPM px/mm (BGR) + the mm -> board-image px offset."""
    x0, y0, x1, y1 = lay.outline_mm
    w, h = int((x1 - x0) * PPM), int((y1 - y0) * PPM)
    img = np.full((h, w, 3), 230, np.uint8)
    off = np.array([-x0, -y0])

    def px(pts_mm):
        return np.round((np.asarray(pts_mm) + off) * PPM).astype(np.int32)
    cv2.fillConvexPoly(img, px([[0, 0], [420, 0], [420, 270], [0, 270]]), (118, 118, 118))
    for p in lay.card.patches:
        q = px(lay.patch_quad_mm(p.id, inner=1.0))
        r, g, b = (int(v) for v in p.truth)
        cv2.fillConvexPoly(img, q, (b, g, r))
    dic = cv2.aruco.getPredefinedDictionary(pose.TAG_DICT)
    cell = lay.tag_edge_mm / measure.TAG_CELLS
    for i, (cx, cy) in lay.tag_centres_mm.items():
        quiet = px([[cx - lay.tag_edge_mm / 2 - cell, cy - lay.tag_edge_mm / 2 - cell],
                    [cx + lay.tag_edge_mm / 2 + cell, cy + lay.tag_edge_mm / 2 + cell]])
        cv2.rectangle(img, tuple(quiet[0]), tuple(quiet[1]), (200, 200, 200), -1)
        side = int(round(lay.tag_edge_mm * PPM))
        m = cv2.aruco.generateImageMarker(dic, i, side, borderBits=1)
        tl = px([[cx - lay.tag_edge_mm / 2, cy - lay.tag_edge_mm / 2]])[0]
        img[tl[1]:tl[1] + side, tl[0]:tl[0] + side] = m[..., None]
    se = lay.slanted_edge
    (sx, sy), s = se["centre_mm"], se["side_mm"]
    g = se["grey_margin_mm"] + 5
    cv2.rectangle(img, tuple(px([[sx - s / 2 - g, sy - s / 2 - g]])[0]),
                  tuple(px([[sx + s / 2 + g, sy + s / 2 + g]])[0]), (128, 128, 128), -1)
    cv2.fillConvexPoly(img, px(lay.square_corners_mm()), (20, 20, 20))
    for (cx, cy), d in lay.samples.values():
        c = px([[cx, cy]])[0]
        cv2.circle(img, (int(c[0]), int(c[1])), int(d / 2 * PPM), (120, 40, 90), -1)
    return img, off


def _camera_view(lay, K, w, h, depth_mm, shift_mm=(0.0, 0.0), blur=0.8):
    """The board face-on at depth_mm, its card centre shift_mm off the optical axis."""
    bimg, off = _board_image(lay)
    # board-image px -> board mm -> camera image px
    A = np.array([[1 / PPM, 0, -off[0]], [0, 1 / PPM, -off[1]], [0, 0, 1]])
    cx, cy = 210.0 + shift_mm[0], 135.0 + shift_mm[1]
    B = np.array([[K[0, 0] / depth_mm, 0, K[0, 2] - K[0, 0] * cx / depth_mm],
                  [0, K[1, 1] / depth_mm, K[1, 2] - K[1, 1] * cy / depth_mm], [0, 0, 1]])
    H = B @ A
    img = cv2.warpPerspective(bimg, H, (w, h), flags=cv2.INTER_AREA,
                              borderValue=(60, 60, 60))
    return cv2.GaussianBlur(img, (0, 0), blur) if blur else img


def _pinhole(K):
    return pose.Lens("openmv_n6", K, None, False, "test: pinhole render")


def _distort(img, lens):
    """Re-render a pinhole image through lens.dist: each raw pixel samples its ideal position."""
    h, w = img.shape[:2]
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    pts = np.stack([xs.ravel(), ys.ravel()], axis=1).reshape(-1, 1, 2)
    ideal = cv2.undistortPoints(pts, lens.K, lens.dist, P=lens.K).reshape(h, w, 2)
    return cv2.remap(img, ideal[..., 0].astype(np.float32), ideal[..., 1].astype(np.float32),
                     cv2.INTER_LINEAR, borderValue=(60, 60, 60))


@pytest.fixture
def lay(tmp_path):
    return _layout(tmp_path)


def test_layout_template_is_refused_unless_allowed():
    with pytest.raises(LayoutError, match="measured"):
        load_layout(TEMPLATE)
    lay = load_layout(TEMPLATE, allow_template=True)
    assert set(lay.tags_mm) == {0, 1, 2, 3}
    assert lay.tag_centres_mm[0] == pytest.approx((44.5, 44.5))
    assert lay.tag_centres_mm[3] == pytest.approx((375.5, 225.5))  # 331 x 181 mm apart


def test_layout_rejects_a_sample_outside_the_outline(tmp_path):
    data = yaml.safe_load(TEMPLATE.read_text())
    data["measured"] = True
    data["samples"]["u80a"]["centre_mm"] = [-200.0, 60.0]
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(LayoutError, match="outside"):
        load_layout(p)


@pytest.mark.parametrize("depth_m", [1.5, 2.0])  # the 660 x 510 mm template fits from ~1.1 m
def test_measure_frame_recovers_range_px_and_colour(lay, depth_m):
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    img = _camera_view(lay, K, 1280, 800, depth_m * 1000)
    rec = measure.measure_frame(img, _pinhole(K), lay)
    assert rec["tags"] == 4
    assert rec["range_m"] == pytest.approx(depth_m, rel=0.02)
    assert rec["pos_m"][2] == pytest.approx(depth_m, rel=0.02)
    assert abs(rec["pos_m"][0]) < 0.01 and abs(rec["pos_m"][1]) < 0.01  # card centre on axis
    assert rec["board_whole"] is True
    for name, ((_c), d) in lay.samples.items():
        assert rec["samples"][name]["px"] == pytest.approx(K[0, 0] * d / (depth_m * 1000),
                                                           rel=0.02)
    assert rec["mtf50_lpmm"] is not None and rec["mtf50_lpmm"] > 0
    assert rec["edge_w_px"] is not None and rec["edge_w_px"] > 0
    # rendered with the truth colours: small errors only (edge blur at the patch border aside)
    assert rec["de00"] and max(rec["de00"].values()) < 3.0


def test_reference_box_contains_the_drawn_sample(lay):
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    img = _camera_view(lay, K, 1280, 800, 1500.0, blur=0)
    rec = measure.measure_frame(img, _pinhole(K), lay)
    x1, y1, x2, y2 = rec["samples"]["u80a"]["box"]
    blob = cv2.inRange(img, (110, 30, 80), (130, 50, 100))
    cx, cy = int(K[0, 2]), int(K[1, 2])  # the card centre sits on the principal point
    ys, xs = np.nonzero(blob[:cy, :cx])  # u80a: up and left of the card centre
    found = [xs.min(), ys.min(), xs.max(), ys.max()]
    assert board.iou([x1, y1, x2, y2], [float(v) for v in found]) > 0.6


def test_template_board_does_not_fit_the_n6_at_1_m(lay):
    """Rev 13 fit check, measured: 510 mm of board height is > the N6's ~0.49 m view at 1 m."""
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    rec = measure.measure_frame(_camera_view(lay, K, 1280, 800, 1000.0), _pinhole(K), lay)
    assert rec["tags"] == 4 and rec["board_whole"] is False
    assert board.board_fits(K, 1280, 800, (660.0, 510.0), 1.0) is False


def test_board_cut_by_the_frame_edge_is_not_whole(lay):
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    # board pushed right so the right-hand samples leave the frame; all four tags stay in view
    img = _camera_view(lay, K, 1280, 800, 1500.0, shift_mm=(-320.0, 0.0))
    rec = measure.measure_frame(img, _pinhole(K), lay)
    assert rec["tags"] == 4 and rec["board_whole"] is False


def test_single_tag_gives_a_range_but_no_w1_frame(lay):
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    img = _camera_view(lay, K, 1280, 800, 1000.0)
    # blank out tags 1-3
    tags = pose.detect_tags(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    for i in (1, 2, 3):
        c = tags[i]
        cv2.fillConvexPoly(img, np.round(c).astype(np.int32), (118, 118, 118))
    rec = measure.measure_frame(img, _pinhole(K), lay)
    assert rec["tags"] == 1 and rec["range_m"] == pytest.approx(1.0, rel=0.03)
    assert rec["board_whole"] is False and rec["samples"] == {}


def test_tiny_rois_give_no_width_instead_of_a_wrong_one(lay):
    K = pose.camera_matrix("openmv_n6", 640, 400)
    img = _camera_view(lay, K, 640, 400, 3500.0, blur=1.5)
    rec = measure.measure_frame(img, _pinhole(K), lay)
    # at 3.5 m on 640 x 400 a tag cell is ~2 px: under MIN_HALF_PX, so no tag-edge width
    assert rec["tag_edge_w_px"] is None


def test_distorted_view_measured_with_its_lens(lay):
    """Rev 16 end to end: a board rendered through the N6 lens, off-centre, measured with it."""
    lens = pose.lens_for("openmv_n6", 1280, 800)
    assert lens.mapped
    pin = _camera_view(lay, lens.K, 1280, 800, 1800.0, shift_mm=(-120.0, -60.0), blur=0.6)
    img = _distort(pin, lens)
    rec = measure.measure_frame(img, lens, lay)
    assert rec["tags"] == 4 and rec["range_m"] == pytest.approx(1.8, rel=0.015)
    assert abs(rec["range_raw_m"] / 1.8 - 1) > abs(rec["range_m"] / 1.8 - 1)
    assert rec["reproj_px"] < 1.0
    assert rec["samples"]["u80b"]["px"] == pytest.approx(
        measure.sample_box(_true_proj(lens, 1800.0, (-120.0, -60.0)), lay.samples["u80b"][0],
                           80.0)["px_across"], abs=1.0)


def _true_proj(lens, depth_mm, shift_mm):
    """The renders' pose: face-on, card centre + shift on the optical axis, at depth_mm."""
    t = np.array([[-(210.0 + shift_mm[0])], [-(135.0 + shift_mm[1])], [depth_mm]])
    return measure.projector(np.zeros((3, 1)), t, lens)


def test_rev16_offcentre_tag_range_needs_undistortion():
    """Rev 16 test (1): k1 = -0.42 (bm #184's N6), 1.0 m, off-centre: within 1 % undistorted,
    >= 5 % off raw."""
    lens = pose.lens_for("openmv_n6", 1280, 800)
    assert lens.dist[0] == pytest.approx(-0.4214, abs=1e-4)
    from edge_ml.pool2 import board as b
    corners_mm = b.tag_corners_mm(340.0, 150.0, 63.0)   # 340 / 150 mm off the axis, at 1.0 m
    obj = np.c_[corners_mm, np.full(4, 1000.0)]
    raw, _ = cv2.projectPoints(obj, np.zeros(3), np.zeros(3), lens.K, lens.dist)
    raw = {7: raw.reshape(4, 2)}
    assert (raw[7][:, 0] < 1280).all() and (raw[7][:, 1] < 800).all()
    und = pose.undistort_corners(raw, lens)
    r_und = pose.board_range(und, (7,), 63.0, lens.K)["range_m"]
    r_raw = pose.board_range(raw, (7,), 63.0, lens.K)["range_m"]
    assert r_und == pytest.approx(1.0, rel=0.01)
    assert abs(r_raw - 1.0) >= 0.05


def test_rev16_sample_box_lands_on_its_raw_position(lay):
    """Rev 16 test (2): boxes from the undistorted-corner pose, projected back with dist_coeffs,
    land within 0.5 px of the sample's true raw-image position."""
    lens = pose.lens_for("openmv_n6", 1280, 800)
    true = _true_proj(lens, 1500.0, (-150.0, -80.0))
    tags_raw = {i: true(lay.tags_mm[i]) for i in lay.tags_mm}
    rvec, tvec = measure.board_pose(pose.undistort_corners(tags_raw, lens), lay, lens.K)
    proj = measure.projector(rvec, tvec, lens)
    for name, (centre, d) in lay.samples.items():
        got = measure.sample_box(proj, centre, d)["box"]
        want = measure.sample_box(true, centre, d)["box"]
        assert np.max(np.abs(np.array(got) - np.array(want))) < 0.5, name


def test_rev16_guard_keeps_raw_corners_and_says_why():
    """Rev 16 test (3): a mode that can't be mapped to the fit keeps raw corners, labelled."""
    lens = pose.lens_for("openmv_ae3", 800, 600)
    assert lens.mapped is False and lens.dist is None and "guard" in lens.why
    raw = {0: np.array([[100.0, 100.0], [140.0, 100.0], [140.0, 140.0], [100.0, 140.0]])}
    assert np.array_equal(pose.undistort_corners(raw, lens)[0], raw[0])
    assert lens.record()["mapped"] is False and "guard" in lens.record()["why"]
    assert pose.lens_for("imx708", 1920, 1080, None).mapped is False   # no ScalerCrop recorded
    assert pose.lens_for("openmv_ae3", 640, 400).mapped is True        # binned full field


def test_guard_k_is_unscaled_with_the_principal_point_at_the_centre():
    """QE 008 R6 on #133: a guard mode is windowed (rev 13: no scale), so fx / fy stay at the
    1280x800 fit; the window offset is unknown, so the principal point is the frame centre."""
    lens = pose.lens_for("openmv_ae3", 800, 600)
    fx, fy = pose.CALIB["openmv_ae3"][2:4]
    assert lens.K[0, 0] == pytest.approx(fx) and lens.K[1, 1] == pytest.approx(fy)  # not x0.625
    assert (lens.K[0, 2], lens.K[1, 2]) == (400.0, 300.0)
    assert "unscaled" in lens.why and "principal point unknown" in lens.why


@pytest.mark.parametrize("cam,w,h", [("openmv_n6", 960, 600), ("openmv_ae3", 640, 400),
                                     ("openmv_n6", 640, 400)])
def test_rev16_offcentre_range_at_a_scaled_wed_mode(cam, w, h):
    """QE 008 N7: rev 16 test (1) at a scaled Wed mode (K mapped by the scale rule, coefficients
    unchanged): within 1 % undistorted."""
    lens = pose.lens_for(cam, w, h)
    assert lens.mapped and lens.K[0, 0] == pytest.approx(pose.CALIB[cam][2] * w / 1280)
    from edge_ml.pool2 import board as b
    obj = np.c_[b.tag_corners_mm(300.0, 130.0, 63.0), np.full(4, 1000.0)]
    raw, _ = cv2.projectPoints(obj, np.zeros(3), np.zeros(3), lens.K, lens.dist)
    raw = {7: raw.reshape(4, 2)}
    assert (raw[7][:, 0] > 0).all() and (raw[7][:, 0] < w).all() and (raw[7][:, 1] < h).all()
    r_und = pose.board_range(pose.undistort_corners(raw, lens), (7,), 63.0, lens.K)["range_m"]
    assert r_und == pytest.approx(1.0, rel=0.01)


def test_pinned_lens_matches_the_bm184_files():
    """pose.CALIB / DIST are bm #184's intrinsics_full, and the files still hash as pinned."""
    import hashlib
    import json
    for cam, (path, sha) in pose.CALIB_FILES.items():
        f = Path(path).expanduser()
        if not f.exists():
            pytest.skip(f"{f} not on this host")
        assert hashlib.sha256(f.read_bytes()).hexdigest() == sha
        name = {"openmv_n6": "n6", "openmv_ae3": "ae3", "imx708": "imx708"}[cam]
        fit = next(c for c in json.loads(f.read_text())["cameras"] if c["camera"] == name)
        i = fit["intrinsics_full"]
        assert pose.CALIB[cam] == (i["image_size"][0], i["image_size"][1], i["fx"], i["fy"],
                                   i["cx"], i["cy"])
        assert pose.DIST[cam] == tuple(i["dist_coeffs"])
