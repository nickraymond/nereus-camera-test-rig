"""Render the before/after slider page inputs for the 5 s / 100 KB result (Mac).

    python -m compression_study.video_100k.slider --run <RUN_ROOT> --out <RUN_ROOT>/slider
    python ~/.claude/skills/before-after-report/scripts/build_report.py \
        <RUN_ROOT>/slider/spec.json <RUN_ROOT>/slider/page.html --split

Every image is one frame at 1280×720, lossless PNG, rendered the same way on both sides:
"before" = the source frame (the 5 s window reference), "after" = the same frame of what the
viewer sees (decoded exactly as scored, ``sheet.display_file``). Card close-ups are the fixed
card box, enlarged 3× nearest-neighbour on both sides. Facts come from decision/decision.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from .candidates import registry
from .common import card_box, q, read_jsonl, sh, src_path
from .sheet import display_file

REC = "pi_svt_p4_lowmem"
FAST = "pi_svt_p6_lowmem"
H264 = "pi_x264_vs"
BG = "pi_svt_p6_lowmem+bgcache"


def frame_png(src: Path, n: int, out: Path) -> Path:
    sh(
        f"ffmpeg -hide_banner -v error -y -i {q(src)} -vf \"select='eq(n\\,{n})',format=rgb24\" "
        f"-frames:v 1 -update 1 {q(out)}"
    )
    return out


def card_png(full: Path, clip: str, out: Path, zoom: int = 3) -> Path:
    x, y, w, h = card_box(clip)
    im = Image.open(full).convert("RGB").crop((x, y, x + w, y + h))
    im.resize((w * zoom, h * zoom), Image.NEAREST).save(out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    reg = registry()
    data = a.run / "pi"
    recs = {
        (r["cand"], r["clip"]): r
        for r in read_jsonl(data / "results.jsonl")
        if r.get("status") == "ok"
    }
    img = a.out / "img"
    img.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="slider_"))
    views = [  # (key, clip, frame choice, afters, title, note)
        (
            "calm",
            "bmcam003_round1",
            "mid",
            [REC, H264],
            "Calm pool, card at 2 m",
            "Middle frame of the 5 s clip. Most of a static scene.",
        ),
        (
            "diver",
            "bmcam003_round3",
            "worst",
            [REC, FAST, H264],
            "Diver passing the card (hardest clip)",
            "The frame where the recommended encode scores lowest in this clip.",
        ),
        (
            "busy",
            "bmcam003_round3@150",
            "worst",
            [REC, BG],
            "Stress case: diver close, bubbles (15–20 s into the recording)",
            "Sensitivity window, not used for ranking: the busiest 5 s we have.",
        ),
    ]
    sections = []
    try:
        for key, clip, which, afters, title, note in views:
            per = recs[(REC, clip)]["vmaf_per_frame"]
            n = min(range(len(per)), key=per.__getitem__) if which == "worst" else len(per) // 2
            before = frame_png(src_path(clip), n, img / f"{key}_before.png")
            card_b = card_png(before, clip, img / f"{key}_card_before.png")
            full_afters, card_afters = [], []
            for c in afters:
                r = recs[(c, clip)]
                disp = display_file(reg[c], r, data, clip, tmp)
                f = frame_png(disp, n, img / f"{key}_{c.replace('+', '_')}.png")
                cp = card_png(f, clip, img / f"{key}_card_{c.replace('+', '_')}.png")
                lab = (
                    f"{LABEL[c]} · {r['size_B'] / 1000:.1f} kB · VMAF {r['vmaf_per_frame'][n]:.0f}"
                )
                full_afters.append({"label": lab, "image": f"img/{f.name}"})
                card_afters.append({"label": lab, "image": f"img/{cp.name}"})
            sections.append(
                {
                    "title": title,
                    "note": f"{note} Frame {n} of 50; click for full screen and 1:1.",
                    "before": f"img/{before.name}",
                    "before_label": "Before · original video frame",
                    "afters": full_afters,
                }
            )
            sections.append(
                {
                    "title": f"{title}: the colour card, 3×",
                    "note": "Pixels enlarged, no smoothing.",
                    "before": f"img/{card_b.name}",
                    "before_label": "Before · original",
                    "pixelated": True,
                    "afters": card_afters,
                }
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    spec = json.loads((Path(__file__).with_name("slider_text.json")).read_text())
    spec["sections"] = sections
    (a.out / "spec.json").write_text(json.dumps(spec, indent=1))
    print(f"wrote {a.out / 'spec.json'} with {len(sections)} views")
    return 0


LABEL = {
    REC: "After · recommended (AV1, Pi)",
    FAST: "After · faster AV1 (Pi)",
    H264: "After · H.264 (Pi software)",
    BG: "After · AV1 + background sent earlier",
}

if __name__ == "__main__":
    raise SystemExit(main())
