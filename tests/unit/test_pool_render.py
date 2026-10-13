"""Unit tests for ``host_tools/pool_render.py`` (pool-run render + best-frame cut sheet).

Synthetic tiny inputs only, except the opt-in real-data test: set ``POOL_RENDER_FIXTURE`` to a
pulled run folder (e.g. the 2026-09-29 hil_soak fixture) to render it end to end.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from _color_helpers import dng_tags, flat_mosaic, write_dng  # noqa: E402
from host_tools import pool_render as pr  # noqa: E402

from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ  # noqa: E402

TS = "20261014T170000Z"


# --- synthetic run builders --------------------------------------------------------------------

def _noise_jpeg(path: Path, w: int, h: int, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 190, size=(h, w, 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)
    return path


def _rpicam_json(jpg: Path, lens: float = 0.8) -> None:
    Path(f"{jpg}.rpicam.json").write_text(json.dumps({
        "ExposureTime": 1000, "AnalogueGain": 1.5, "DigitalGain": 1.0, "LensPosition": lens,
        "ColourGains": [1.5, 2.0], "Lux": 500.0}))


def _bayer(folder: Path, camera: str, values=(40, 80, 160), w: int = 64, h: int = 40,
           rotation: int = 0) -> Path:
    """Uniform BGGR mosaic: B, G, R levels from ``values`` = (R, G, B)."""
    r, g, b = values
    mosaic = np.zeros((h, w), dtype=np.uint8)
    mosaic[0::2, 0::2] = b
    mosaic[0::2, 1::2] = g
    mosaic[1::2, 0::2] = g
    mosaic[1::2, 1::2] = r
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{camera}_raw_{TS}.bayer"
    data = mosaic.tobytes()
    path.write_bytes(data)
    path.with_suffix(".json").write_text(json.dumps({
        "format": "openmv_bayer", "width": w, "height": h, "bits": 8, "cfa": "BGGR",
        "black_level": 0, "white_level": 255, "exposure_us": 5000, "gain_db": 6.0,
        "mount_rotation_deg": rotation, "sha256": hashlib.sha256(data).hexdigest()}))
    return path


def _make_set(run: Path, name: str = f"exp_{TS}_pool", cameras=("imx708", "openmv_n6"),
              jpeg_size=(48, 32)) -> Path:
    set_dir = run / "2026-10-14" / name
    cap = set_dir / "captures"
    set_dir.mkdir(parents=True)
    (set_dir / "experiment.json").write_text(json.dumps({"captures": [], "raw_captures": []}))
    if "imx708" in cameras:
        d = cap / "imx708"
        jpg = _noise_jpeg(d / f"imx708_raw_{TS}.jpg", *jpeg_size)
        _rpicam_json(jpg)
        tags = dng_tags(cfa_codes=(2, 1, 1, 0), black=(64, 64, 64, 64))  # BGGR, 10-bit
        tags[4] = (50717, 4, 1, 1023, True)  # WhiteLevel
        write_dng(d / f"imx708_raw_{TS}.dng",
                  flat_mosaic("BGGR", shape=(32, 48), black=(64,) * 4, white=1023), tags)
    for cam in ("openmv_n6", "openmv_ae3"):
        if cam in cameras:
            _bayer(cap / cam, cam)
            _noise_jpeg(cap / cam / f"{cam}_image_{TS}.jpg", 64, 40)
    return set_dir


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


# --- discovery ------------------------------------------------------------------------------

def test_discovery_sorted_and_missing_camera_is_skipped_not_fatal(tmp_path):
    run = tmp_path / "run"
    _make_set(run, f"exp_{TS}_pool", cameras=("imx708", "openmv_n6"))
    _make_set(run, "exp_20261014T160000Z_pool", cameras=("openmv_n6",))
    sets = pr.discover_sets(run)
    assert [s.name for s in sets] == ["exp_20261014T160000Z_pool", f"exp_{TS}_pool"]

    ctx = pr.Ctx(tmp_path / "out")
    rows, crops = pr.score_set(ctx, run, sets[1])
    by_cam = {r["camera"]: r for r in rows}
    assert set(by_cam) == set(pr.CAMERAS)
    assert by_cam["openmv_ae3"]["status"] == "FAIL"
    assert by_cam["openmv_ae3"]["reason"] == "camera missing in set"
    assert any("openmv_ae3 has no files" in w for w in ctx.warnings)
    assert by_cam["imx708"]["scored_role"] == "raw_jpg"
    assert by_cam["imx708"]["lens_position"] == 0.8
    assert by_cam["openmv_n6"]["scored_role"] == "bayer"
    assert by_cam["openmv_n6"]["gain_db"] == 6.0
    # tiny JPEG is not 4608x2592: no production crop, but a visible warning
    assert crops == []
    assert any("production crop needs 4608x2592" in w for w in ctx.warnings)


def test_locate_files_roles(tmp_path):
    set_dir = _make_set(tmp_path / "run")
    ctx = pr.Ctx(tmp_path / "out")
    imx = pr.locate_files(set_dir, "imx708", ctx)
    assert imx["dng"].name == f"imx708_raw_{TS}.dng"
    assert imx["raw_jpg_meta"].name == f"imx708_raw_{TS}.jpg.rpicam.json"
    assert imx["still_jpg"] is None
    n6 = pr.locate_files(set_dir, "openmv_n6", ctx)
    assert n6["bayer"].suffix == ".bayer" and n6["bayer_json"].suffix == ".json"
    assert n6["board_jpg"].name == f"openmv_n6_image_{TS}.jpg"


# --- scoring --------------------------------------------------------------------------------

@pytest.mark.parametrize("mean, clip, status", [
    (100, 0.0, "PASS"), (100, 1.0, "PASS"), (100, 1.01, "WARN"), (100, 5.0, "WARN"),
    (100, 5.01, "FAIL"), (14.9, 0.0, "FAIL"), (15.0, 0.0, "WARN"), (39.9, 0.0, "WARN"),
    (40.0, 0.0, "PASS"), (210.0, 0.0, "PASS"), (210.1, 0.0, "WARN"), (220, 6.0, "FAIL"),
])
def test_classify_thresholds(mean, clip, status):
    assert pr.classify(mean, clip)[0] == status


def test_frame_metrics_known_values():
    gray = np.zeros((10, 20), dtype=np.uint8)
    gray[:, 10:] = 255
    m = pr.frame_metrics(gray)
    assert m["mean_luma"] == pytest.approx(127.5)
    assert m["clip_hi_pct"] == pytest.approx(50.0)
    assert m["clip_lo_pct"] == pytest.approx(50.0)
    assert m["sharpness_lapvar"] > 0 and m["tenengrad"] > 0
    flat = pr.frame_metrics(np.full((10, 20), 128, np.uint8))
    assert flat["sharpness_lapvar"] == 0 and flat["clip_hi_pct"] == 0


def test_sharpest_block_finds_the_textured_patch():
    gray = np.full((400, 600), 128, np.uint8)
    rng = np.random.default_rng(1)
    gray[200:300, 400:500] = rng.integers(0, 255, (100, 100), dtype=np.uint8)
    x, y, w, h = pr.sharpest_block(gray, 100)
    assert (w, h) == (100, 100)
    assert abs(x - 400) <= 25 and abs(y - 200) <= 25


# --- production crop ------------------------------------------------------------------------------

def test_production_crop_exact_native_pixels():
    h, w = pr.NATIVE_IMX708[1], pr.NATIVE_IMX708[0]
    ys, xs = np.mgrid[0:h, 0:w]
    img = np.stack([xs, ys, np.zeros_like(xs)], axis=2).astype(np.uint16)
    crop = pr.production_crop(img)
    assert crop.shape == (900, 1600, 3)
    assert tuple(crop[0, 0, :2]) == (1504, 846)
    assert tuple(crop[-1, -1, :2]) == (1504 + 1599, 846 + 899)
    with pytest.raises(ValueError, match="4608x2592"):
        pr.production_crop(img[:, :4000])


def test_crop_rows_on_full_native_jpeg(tmp_path):
    run = tmp_path / "run"
    set_dir = _make_set(run, cameras=("imx708",), jpeg_size=pr.NATIVE_IMX708)
    ctx = pr.Ctx(tmp_path / "out")
    _rows, crops = pr.score_set(ctx, run, set_dir)
    assert len(crops) == 1
    png = cv2.imread(str(crops[0]["_png"]))
    assert png.shape == (900, 1600, 3)
    src = cv2.imread(str(set_dir / f"captures/imx708/imx708_raw_{TS}.jpg"), pr.JPEG_FLAGS)
    assert np.array_equal(png, src[846:846 + 900, 1504:1504 + 1600])  # PNG is lossless
    assert (tmp_path / "out" / crops[0]["jpg"]).stat().st_size > 0


# --- selection ----------------------------------------------------------------------------------

def _row(name, sharp, status):
    return {"name": name, "sharpness_lapvar": sharp, "status": status}


def test_top_k_excludes_fail_and_orders_by_sharpness():
    rows = [_row("a", 10, "PASS"), _row("b", 50, "FAIL"), _row("c", 30, "WARN"),
            _row("d", 20, "PASS"), {"name": "e", "status": "FAIL"}]
    picked, fallback = pr.top_k(rows, 2)
    assert [r["name"] for r in picked] == ["c", "d"] and fallback is False
    picked, _ = pr.top_k(rows, 10)
    assert [r["name"] for r in picked] == ["c", "d", "a"]


def test_top_k_all_fail_falls_back_and_marks_it():
    rows = [_row("a", 10, "FAIL"), _row("b", 50, "FAIL"), {"name": "c", "status": "FAIL"}]
    picked, fallback = pr.top_k(rows, 3)
    assert [r["name"] for r in picked] == ["b", "a"] and fallback is True
    assert pr.top_k([], 3) == ([], False)
    assert pr.best_of(rows)["fallback_all_fail"] is True


# --- renders -------------------------------------------------------------------------------------

def test_openmv_render_gray_world_neutral_and_rotation(tmp_path):
    ctx = pr.Ctx(tmp_path)
    path = _bayer(tmp_path / "a", "openmv_n6", values=(40, 80, 160))
    srgb16, info = pr.render_openmv(path, ctx)
    assert srgb16.dtype == np.uint16 and srgb16.shape == (40, 64, 3)
    centre = srgb16[10:30, 10:50].reshape(-1, 3).astype(float).mean(axis=0)
    assert np.ptp(centre) < 0.01 * centre.mean()  # gray world made it neutral
    assert info["awb"] == "Mac gray-world AWB"
    rotated, _ = pr.render_openmv(_bayer(tmp_path / "b", "openmv_n6", rotation=90), ctx)
    assert rotated.shape == (64, 40, 3)


def test_dng_ccm_recovers_rpicam_ccm():
    ccm = np.array([[1.59, -0.37, -0.22], [-0.44, 1.85, -0.41], [0.03, -0.77, 1.74]])
    wb = (1.34, 1.0, 3.45)
    color_matrix1 = np.linalg.inv(SRGB_TO_XYZ @ ccm @ np.diag(wb))  # what rpicam writes
    assert np.allclose(pr.dng_ccm(color_matrix1, wb), ccm, atol=1e-9)
    assert pr.dng_ccm(None, wb) is None


def test_wb_clip_keeps_saturation_white():
    rgb = np.ones((2, 2, 3), np.float32)
    rgb[0, 0] = (0.2, 0.3, 0.1)
    out = pr.apply_wb_clip(rgb, (1.5, 1.0, 2.0))
    assert np.allclose(out[1, 1], 1.0)
    assert np.allclose(out[0, 0], (0.3, 0.3, 0.2))


def test_srgb_encode_endpoints_and_8bit():
    lin = np.array([0.0, 0.0031308, 0.18, 1.0, 2.0], np.float32)
    s16 = pr.encode_srgb16(lin)
    assert s16[0] == 0 and s16[3] == 65535 and s16[4] == 65535
    assert pr.to8(s16)[2] == 118  # 18 % grey -> sRGB 118


def test_render_imx708_dng_synthetic(tmp_path):
    set_dir = _make_set(tmp_path / "run", cameras=("imx708",))
    dng = set_dir / f"captures/imx708/imx708_raw_{TS}.dng"
    srgb16, info = pr.render_imx708_dng(dng, {"ColourGains": [1.5, 2.0], "DigitalGain": 1.0},
                                        pr.Ctx(tmp_path))
    assert srgb16.dtype == np.uint16 and srgb16.shape == (32, 48, 3)
    assert info["wb_source"] == "rpicam.json ColourGains"
    assert info["ccm_source"].startswith("DNG ColorMatrix1")


# --- output folder + end to end -----------------------------------------------------------------

def test_resolve_out_default_and_refusals(tmp_path):
    run = tmp_path / "pool_run"
    run.mkdir()
    assert pr.resolve_out([run], None, None) == (tmp_path / "pool_run_render").resolve()
    with pytest.raises(ValueError, match="inside run folder"):
        pr.resolve_out([run], run / "render", None)
    with pytest.raises(ValueError, match="inside run folder"):
        pr.resolve_out([run], run, None)
    root = tmp_path / "spot"
    (root / "bmcam003").mkdir(parents=True)
    with pytest.raises(ValueError, match="Spotter unit folder"):
        pr.resolve_out([run], root / "bmcam003" / "x", root)
    assert pr.resolve_out([run], root / "elsewhere", root) == (root / "elsewhere").resolve()


def test_main_end_to_end_synthetic(tmp_path):
    run = tmp_path / "pool_run"
    _make_set(run, cameras=("imx708", "openmv_n6"))
    (run / "sets.jsonl").write_text("{}\n")
    before = _tree(run)
    assert pr.main([str(run)]) == 0
    assert _tree(run) == before  # the run folder is evidence: untouched
    out = tmp_path / "pool_run_render"
    for name in ("scores.csv", "crops.csv", "best_images.html", "best_images.png",
                 "render_manifest.json", "render.log"):
        assert (out / name).stat().st_size > 0, name
    lines = (out / "scores.csv").read_text().strip().splitlines()
    assert len(lines) == 1 + 3  # header + one row per camera
    manifest = json.loads((out / "render_manifest.json").read_text())
    assert manifest["counts"]["frames_rendered"] == 2
    assert any(i["path"].endswith(".dng") for i in manifest["inputs"])
    assert any(i["path"].endswith("sets.jsonl") for i in manifest["inputs"])
    assert all(len(i["sha256"]) == 64 for i in manifest["inputs"])
    assert "imx708_dng" in manifest["method"]
    # every <img> is shown at its natural pixel size
    html_text = (out / "best_images.html").read_text()
    imgs = re.findall(r"<img src='([^']+)' width='(\d+)' height='(\d+)'", html_text)
    assert imgs
    for src, w, h in imgs:
        shape = cv2.imread(str(out / src)).shape
        assert (shape[1], shape[0]) == (int(w), int(h)), src
    assert "scaled to" in html_text and "1:1 crop, native px" in html_text
    set_out = out / "pool_run" / f"exp_{TS}_pool"
    assert (set_out / f"imx708_raw_{TS}_mac_srgb_16bit.tif").is_file()
    assert (set_out / f"openmv_n6_image_{TS}_board_isp_render.jpg").read_bytes() == (
        run / "2026-10-14" / f"exp_{TS}_pool" / f"captures/openmv_n6/openmv_n6_image_{TS}.jpg"
    ).read_bytes()


def test_main_zero_rendered_exits_nonzero(tmp_path):
    run = tmp_path / "empty_run"
    (run / "2026-10-14" / f"exp_{TS}_pool").mkdir(parents=True)
    assert pr.main([str(run), "--out", str(tmp_path / "o")]) == 1
    assert pr.main([str(tmp_path / "missing")]) == 2


def test_main_refuses_out_inside_run(tmp_path):
    run = tmp_path / "run"
    _make_set(run)
    assert pr.main([str(run), "--out", str(run / "render")]) == 2
    assert not (run / "render").exists()


def test_spotter_best_ignores_non_unit_folders_and_unproven_1600x900(tmp_path):
    root = tmp_path / "spot"
    _noise_jpeg(root / "bmcam003" / "sweep" / "a.jpg", *pr.NATIVE_IMX708)
    _noise_jpeg(root / "other" / "b.jpg", *pr.NATIVE_IMX708)
    _noise_jpeg(root / "bmcam004" / "c.jpg", 1600, 900)
    before = _tree(root)
    ctx = pr.Ctx(tmp_path / "out")
    best, notes = pr.spotter_best(ctx, root)
    assert [b["unit"] for b in best] == ["bmcam003"]
    assert cv2.imread(str(best[0]["png"])).shape == (900, 1600, 3)
    assert any("bmcam004: skipped 1 JPEG(s): 1600x900" in n for n in notes)
    assert _tree(root) == before  # read only
    assert pr.spotter_best(ctx, tmp_path / "out") == (
        [], [f"no Spotter files found under {tmp_path / 'out'}"])


# --- opt-in real data -----------------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("POOL_RENDER_FIXTURE"),
                    reason="set POOL_RENDER_FIXTURE to a pulled run folder")
def test_real_fixture_renders(tmp_path):
    run = Path(os.environ["POOL_RENDER_FIXTURE"])
    out = tmp_path / "render"
    assert pr.main([str(run), "--all", "--out", str(out)]) == 0
    manifest = json.loads((out / "render_manifest.json").read_text())
    kinds = {o["kind"]: o for o in manifest["outputs"]}
    assert (kinds["imx708_dng_render_png8"]["width"],
            kinds["imx708_dng_render_png8"]["height"]) == pr.NATIVE_IMX708
    assert kinds["imx708_dng_render_tiff16"]["bytes"] > 1_000_000
    assert (kinds["imx708_prod_crop_png"]["width"], kinds["imx708_prod_crop_png"]["height"]) \
        == (1600, 900)
    info = next(iter(manifest["timings"]["dng_render_info"].values()))
    assert info["ccm_source"].startswith("DNG ColorMatrix1")
    assert info["ccm_max_abs_diff_vs_rpicam_json"] < 0.01
