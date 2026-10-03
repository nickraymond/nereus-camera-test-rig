"""Step C baseline: today's fixed rung walk (bm #120 still.raw.distances [3.8, 4.6, 5.95, 8.25],
first rung that fits the cap wins) on the sweep frames, for the same ROIs and caps as
byte_target_bench.sh. Desk (Mac) encodes with the production encoder; bytes are identical
to the Pi's. Writes <run>/fixed_rungs_desk.csv.

    python -m compression_study.presets.fixed_rungs_desk --run <runs/sweep_…> --prod <BM_Devel_Pi>
"""

import argparse
import csv
import json
import tempfile
from pathlib import Path

from compression_study.presets import preset_study as S
from compression_study.presets import roi_sweep as W

RUNGS = (3.8, 4.6, 5.95, 8.25)
SIZES = [(800, 450), (1200, 676), (1600, 900), (2000, 1124), (2304, 1296), (3072, 1728)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prod", required=True)
    a = ap.parse_args(argv)
    run = Path(a.run)
    rc, _ = S.load_prod(Path(a.prod))
    rows = []
    for fi in (0, 1, 2):
        stem = run / "cap" / f"stop_+0_r{fi}"
        colour = rc.colour_params(json.loads(stem.with_suffix(".json").read_text()))
        for w, h in SIZES:
            box = W.roi_box(w, h)
            crop = rc.read_dng_crop(str(stem.with_suffix(".dng")), box)
            codes = rc.code_planes(crop)
            sizes = {}
            with tempfile.TemporaryDirectory() as td:
                for cap in (180, 195):
                    res = {"frame": fi, "roi": f"{w}x{h}", "cap": cap, "status": "fallback",
                           "encodes": 0, "d": "", "bytes": "", "fill": ""}
                    for d in RUNGS:
                        if d not in sizes:
                            sizes[d] = len(S.nrjxl_blob(rc, crop, codes, colour, d, "native",
                                                        box, (4608, 2592), Path(td)))
                        res["encodes"] += 1
                        if rc.message_count(sizes[d], S.CHUNK) <= cap:
                            res.update(status="ok", d=d, bytes=sizes[d],
                                       fill=round(sizes[d] / (cap * 288), 4))
                            break
                    rows.append(res)
                    print(res, flush=True)
    with open(run / "fixed_rungs_desk.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
