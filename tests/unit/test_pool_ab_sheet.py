"""host_tools.pool_ab_sheet: A/B rows from a fake pool_render folder (no hardware)."""

import json

from host_tools import pool_ab_sheet as ab
from PIL import Image


def _img(path, w=1280, h=800, color=(100, 120, 140)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path)


def _fake(tmp_path, with_ae3_b=True, name="pool009_x"):
    run = tmp_path / name
    src_set = run / "2026-10-14" / "exp_A_pool"
    _img(src_set / "captures" / "imx708" / "imx708_raw_A.jpg", 4608, 2592)
    (src_set / "captures" / "openmv_n6").mkdir(parents=True)
    (src_set / "captures" / "openmv_n6" / "capture.json").write_text(json.dumps(
        {"output": {"size_bytes": 640000}, "sensor_metadata": {"jpeg_quality": 100}}))
    render = tmp_path / f"{name}_render"
    rset = render / name / "exp_A_pool"
    for cam in ("openmv_n6", "openmv_ae3"):
        _img(rset / f"{cam}_raw_A_mac_grayworld_8bit.png")
        if cam == "openmv_n6" or with_ae3_b:
            _img(rset / f"{cam}_image_A_board_isp_render.jpg")
        _img(rset / f"{cam}_raw_A_mac_grayworld_8bit_captioned.png", 1280, 900)  # ignored
    _img(rset / "imx708_raw_A_mac_srgb_8bit.png", 4608, 2592)
    (render / "render_manifest.json").write_text(json.dumps({"runs": [str(run)]}))
    return render, rset


def test_sheet_has_native_openmv_rows_and_scaled_imx(tmp_path):
    render, rset = _fake(tmp_path)
    src = tmp_path / "pool009_x" / "2026-10-14" / "exp_A_pool"
    assert ab.find_pair(rset, "openmv_n6")[0].name == "openmv_n6_raw_A_mac_grayworld_8bit.png"
    assert ab.find_pair(rset, "imx708")[1] == src / "captures" / "imx708" / "imx708_raw_A.jpg"
    assert ab.b_info(rset, "openmv_n6") == "q100, 640 kB"  # B's quality + size on the sheet
    assert ab.main(["--render", str(render)]) == 0
    assert Image.open(render / "ab_exp_A_pool.png").width == 2560  # two 1:1 OpenMV frames


def test_missing_b_fails_loudly_and_never_overwrites_a_complete_sheet(tmp_path, capsys):
    render, rset = _fake(tmp_path)
    assert ab.main(["--render", str(render)]) == 0
    good = (render / "ab_exp_A_pool.png").read_bytes()
    (rset / "openmv_ae3_image_A_board_isp_render.jpg").unlink()
    assert ab.main(["--render", str(render), "--set", "exp_A_pool"]) == 3
    err = capsys.readouterr().err
    assert "MISSING openmv_ae3 B" in err
    assert (render / "ab_exp_A_pool_INCOMPLETE.png").is_file()
    assert (render / "ab_exp_A_pool.png").read_bytes() == good  # complete sheet untouched
    assert ab.main(["--render", str(render), "--set", "exp_A_pool", "--allow-missing"]) == 0


def test_more_than_one_run_needs_run(tmp_path):
    render, _ = _fake(tmp_path)
    (render / "pool010_y").mkdir()
    assert ab.main(["--render", str(render)]) == 2
    assert ab.main(["--render", str(render), "--run", "pool009_x"]) == 0
