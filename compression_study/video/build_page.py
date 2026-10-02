"""Build the "Clip Codec Bench" page: player files + data + template → one publishable folder.

    python -m compression_study.video.build_page --data <data> \
        --out compression_study/work/video_page

Reads ``summary.json`` (analyze.py), ``scores.jsonl`` (per-frame VMAF for the player) and the
Pi live-path log; writes ``index.html``, ``v/<clip>/<encoder>_<budget>k.mp4`` (the actual
encoded files, unchanged) and ``v/<clip>/ref.mp4`` (a near-lossless x264 crf 20 copy of the
lossless source, because browsers cannot play FFV1). ``files.json`` maps published paths to
local files for the Artifact tool. Narrative text lives in ``narrative.py`` beside this file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from compression_study.video import narrative
from compression_study.video.score import load_scores

ENCODERS = {
    "h264_hw": {
        "label": "H.264 · Zero hardware",
        "short": "H.264",
        "codec": "h264",
        "where": "device",
    },
    "av1_svt_p12": {
        "label": "AV1 · Zero (SVT-AV1)",
        "short": "AV1",
        "codec": "av1",
        "where": "device",
    },
    "h264_x264": {
        "label": "H.264 · best case (x264, Mac)",
        "short": "H.264 best",
        "codec": "h264",
        "where": "mac",
    },
    "h265_x265": {
        "label": "H.265 · best case (x265, Mac)",
        "short": "H.265 best",
        "codec": "h265",
        "where": "mac",
    },
    "av1_svt_p4": {
        "label": "AV1 · best case (SVT-AV1 slow, Mac)",
        "short": "AV1 best",
        "codec": "av1",
        "where": "mac",
    },
}
DEVICE = ["h264_hw", "av1_svt_p12"]
CEILING = ["h264_x264", "h265_x265", "av1_svt_p4"]

LABELS = {
    "imx_bob_1080": (
        "IMX708 · 1080p 30 fps · motion",
        "buoy motion simulated on the recorded clip",
    ),
    "imx_bob_720": ("IMX708 · 720p 30 fps · motion", "scored full-screen at 1080p"),
    "imx_bob_720_10": ("IMX708 · 720p 10 fps · motion", "every 3rd frame; scored at 1080p"),
    "imx_bob_360_10": ("IMX708 · 360p 10 fps · motion", "every 3rd frame; scored at 1080p"),
    "imx_static_1080": ("IMX708 · 1080p 30 fps · still scene", "camera and scene still"),
    "imx_static_720": ("IMX708 · 720p 30 fps · still scene", "scored full-screen at 1080p"),
    "imx_static_720_10": ("IMX708 · 720p 10 fps · still scene", "every 3rd frame; scored at 1080p"),
    "n6_bob": ("N6 · 1280×800 18 fps · motion", "buoy motion simulated on the recorded clip"),
    "n6_bob_9": ("N6 · 1280×800 9 fps · motion", "every 2nd frame"),
    "n6_bob_400_9": ("N6 · 640×400 9 fps · motion", "every 2nd frame; scored at 1280×800"),
    "n6_static": ("N6 · 1280×800 18 fps · still scene", "camera and scene still"),
}
TABLE_GROUPS = [
    (
        "IMX708 on the Pi Zero 2 W · motion",
        ["imx_bob_1080", "imx_bob_720", "imx_bob_720_10", "imx_bob_360_10"],
    ),
    (
        "IMX708 on the Pi Zero 2 W · still scene",
        ["imx_static_1080", "imx_static_720", "imx_static_720_10"],
    ),
    ("OpenMV N6 (encoded on the Pi) · motion", ["n6_bob", "n6_bob_9", "n6_bob_400_9"]),
    ("OpenMV N6 (encoded on the Pi) · still scene", ["n6_static"]),
]
BEST_GROUPS = [
    ("imx_bob", "IMX708 · motion"),
    ("imx_static", "IMX708 · still scene"),
    ("n6_bob", "N6 · motion"),
    ("n6_static", "N6 · still scene"),
]
PLAYER = {  # clip: budgets offered
    "imx_bob_720": [200, 400, 800],
    "imx_bob_1080": [200, 400, 800],
    "imx_bob_720_10": [100, 200, 400],
    "imx_bob_360_10": [50, 100, 200],
    "imx_static_720": [100, 200, 400],
    "n6_bob": [200, 400, 800],
    "n6_bob_9": [100, 200, 400],
}
PLAYER_ENCODERS = ["h264_hw", "av1_svt_p12"]
# Cut sheet: one mid-roll frame, card + chart crop at 1:1, both device codecs at each budget
CUTS = [
    {
        "stem": "imx_bob_720",
        "frame": 330,
        "crop": (480, 300, 410, 200),
        "budgets": [100, 200, 400, 800],
    },
    {"stem": "n6_bob", "frame": 200, "crop": (480, 300, 330, 330), "budgets": [100, 200, 400, 800]},
]
SHORT_RES = {
    "imx_bob_1080": "1080p30",
    "imx_bob_720": "720p30",
    "imx_bob_720_10": "720p10",
    "imx_bob_360_10": "360p10",
    "imx_static_1080": "1080p30",
    "imx_static_720": "720p30",
    "imx_static_720_10": "720p10",
    "n6_bob": "800p18",
    "n6_bob_9": "800p9",
    "n6_bob_400_9": "400p9",
    "n6_static": "800p18",
}


def ref_copy(data: Path, stem: str) -> Path:
    out = data / "display" / f"ref_{stem}.mp4"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(data / "sources" / f"{stem}.mkv"),
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(out),
            ],
            check=True,
        )
    return out


def frame_png(video: Path, n: int, crop: tuple, out: Path) -> None:
    w, h, x, y = crop
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{n}),crop={w}:{h}:{x}:{y}",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
    )


def cut_sheets(d: Path, out: Path, scores: dict, S: dict) -> list:
    sheets = []
    for c in CUTS:
        stem, n = c["stem"], c["frame"]
        src = S["sources"][stem]
        orig = out / "cs" / stem / "original.png"
        frame_png(d / "sources" / f"{stem}.mkv", n, c["crop"], orig)
        rows = []
        for b in c["budgets"]:
            cells = []
            for enc in DEVICE:
                r = scores.get((stem, enc, b))
                if not r:
                    cells.append(None)
                    continue
                png = out / "cs" / stem / f"{enc}_{b}k.png"
                frame_png(d / r["path"], n, c["crop"], png)
                cells.append(
                    {
                        "enc": enc,
                        "url": str(png.relative_to(out)),
                        "size_B": r["size_B"],
                        "vmaf_frame": round(r["vmaf_per_frame"][n]),
                        "vmaf": r["vmaf"],
                    }
                )
            rows.append({"budget": b, "cells": cells})
        sheets.append(
            {
                "stem": stem,
                "title": LABELS[stem][0],
                "frame": n,
                "t": round(n / src["fps"], 2),
                "w": c["crop"][0],
                "h": c["crop"][1],
                "original": str(orig.relative_to(out)),
                "rows": rows,
            }
        )
    return sheets


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    d, out = a.data, a.out
    S = json.loads((d / "summary.json").read_text())
    scores = {}
    for r in load_scores(d):
        scores[(r["source"], r["encoder"], r["budget_kB"])] = r
    if out.exists():
        shutil.rmtree(out)
    (out / "v").mkdir(parents=True)
    files = {}

    # player
    clips = {}
    for stem, budgets in PLAYER.items():
        src = S["sources"][stem]
        ref = ref_copy(d, stem)
        pub = f"v/{stem}/ref.mp4"
        files[pub] = ref
        c = {
            "label": LABELS[stem][0],
            "fps": src["fps"],
            "width": src["width"],
            "height": src["height"],
            "ref": {"url": pub, "size_B": ref.stat().st_size},
            "files": {},
        }
        for enc in PLAYER_ENCODERS:
            for b in budgets:
                r = scores.get((stem, enc, b))
                if not r:
                    continue
                pub = f"v/{stem}/{enc}_{b}k.mp4"
                files[pub] = d / r["path"]
                c["files"].setdefault(enc, {})[b] = {
                    "url": pub,
                    "size_B": r["size_B"],
                    "vmaf": r["vmaf"],
                    "vmaf_display": r.get("vmaf_display", r["vmaf"]),
                    "per_frame": [round(x) for x in r["vmaf_per_frame"]],
                }
        clips[stem] = c
    player = {
        "order": list(PLAYER),
        "clips": clips,
        "encoders": PLAYER_ENCODERS,
        "default_budget": 400,
    }

    # budget table
    cells = {}
    for k, per_b in S["at_budget_display"].items():
        stem, enc = k.split("|")
        cells.setdefault(stem, {})[enc] = per_b
    table = {
        "budgets": S["budgets_kB"],
        "cells": cells,
        "groups": [
            {
                "label": g,
                "rows": [
                    {"stem": s, "label": LABELS[s][0].split(" · ", 1)[1]}
                    for s in stems
                    if s in cells
                ],
            }
            for g, stems in TABLE_GROUPS
        ],
    }
    best_cells = {}
    for g, per_enc in S["best_per_budget"].items():
        for enc, per_b in per_enc.items():
            for b, v in per_b.items():
                best_cells.setdefault(g, {}).setdefault(enc, {})[b] = {
                    "vmaf": v["vmaf"],
                    "label": SHORT_RES[v["source"]],
                }
    best = {
        "groups": [{"key": k, "label": lab} for k, lab in BEST_GROUPS],
        "budgets": S["budgets_kB"],
        "enc": DEVICE + ["av1_svt_p4", "h265_x265"],
        "cells": best_cells,
    }

    # charts (display VMAF vs real size)
    charts = []
    for _g, stems in TABLE_GROUPS:
        for stem in stems:
            series = {}
            for enc in DEVICE + CEILING:
                pts = sorted(
                    (r["size_B"] / 1000, r.get("vmaf_display", r["vmaf"]), r["budget_kB"])
                    for (s, e, _b), r in scores.items()
                    if s == stem and e == enc
                )
                if pts:
                    series[enc] = [[round(x, 1), round(y, 1), z] for x, y, z in pts]
            if series:
                charts.append(
                    {
                        "stem": stem,
                        "title": LABELS[stem][0],
                        "sub": LABELS[stem][1],
                        "series": series,
                    }
                )

    cuts = cut_sheets(d, out, scores, S)
    for p in (out / "cs").rglob("*.png"):
        files[str(p.relative_to(out))] = p

    live = []
    lp = d / "live" / "live.jsonl"
    if lp.exists():
        live = [json.loads(ln) for ln in lp.read_text().splitlines() if ln.strip()]
    text = narrative.build(S, live)
    data = {
        "encoders": ENCODERS,
        "device_encoders": DEVICE,
        "ceiling_encoders": CEILING,
        "chart_encoders": DEVICE + CEILING,
        "player": player,
        "table": table,
        "best": best,
        "charts": charts,
        "cuts": cuts,
        **text,
    }
    tpl = (Path(__file__).with_name("page_template.html")).read_text()
    html = tpl.replace("/*DATA*/", json.dumps(data, separators=(",", ":")))
    (out / "index.html").write_text(html)
    for pub, src in files.items():
        dst = out / pub
        if dst.resolve() == Path(src).resolve():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (out / "files.json").write_text(json.dumps({p: str(out / p) for p in files}, indent=1))
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    big = max((p.stat().st_size, p.name) for p in out.rglob("*.mp4"))
    print(
        f"page {(out / 'index.html').stat().st_size / 1e3:.0f} kB, {len(files)} videos, "
        f"total {total / 1e6:.1f} MB, largest {big[1]} {big[0] / 1e6:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
