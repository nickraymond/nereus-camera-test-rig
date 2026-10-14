"""host_tools.pool_ab_sheet: A/B rows from a fake pool_render folder (no hardware)."""

import json

from host_tools import pool_ab_sheet as ab
from PIL import Image


def _img(path, w=1280, h=800, color=(100, 120, 140)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path)


def test_sheet_has_native_openmv_rows_and_scaled_imx(tmp_path):
    run = tmp_path / "pool009_x"
    src_set = run / "2026-10-14" / "exp_A_pool"
    _img(src_set / "captures" / "imx708" / "imx708_raw_A.jpg", 4608, 2592)
    render = tmp_path / "pool009_x_render"
    rset = render / "pool009_x" / "exp_A_pool"
    for cam in ("openmv_n6", "openmv_ae3"):
        _img(rset / f"{cam}_raw_A_mac_grayworld_8bit.png")
        _img(rset / f"{cam}_image_A_board_isp_render.jpg")
        _img(rset / f"{cam}_raw_A_mac_grayworld_8bit_captioned.png", 1280, 900)  # ignored
    _img(rset / "imx708_raw_A_mac_srgb_8bit.png", 4608, 2592)
    (render / "render_manifest.json").write_text(json.dumps({"runs": [str(run)]}))
    assert ab.find_pair(rset, "openmv_n6")[0].name == "openmv_n6_raw_A_mac_grayworld_8bit.png"
    assert ab.find_pair(rset, "imx708")[1] == src_set / "captures" / "imx708" / "imx708_raw_A.jpg"
    assert ab.main(["--render", str(render)]) == 0
    out = Image.open(render / "ab_exp_A_pool.png")
    assert out.width == 2560  # two OpenMV frames side by side at 1:1
