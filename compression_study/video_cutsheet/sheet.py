"""First-look page: board x encoder table, quality table, before/after stills, sample clips.

    python -m compression_study.video_cutsheet.sheet

Stills: every image is rendered by one function (Y'CbCr labelled limited-range BT.709 -> RGB PNG,
1280x720, lossless) at ONE fixed moment for every board: source frame 72 of the primary window
(t = 2.4 s; frame 24 at 10 fps, 36 at 15 fps, 72 at 30 fps), so before and after are the same
picture. Clips: an H.264 sample is the as-sent stream remuxed into MP4 (same bytes); MJPEG and AV1
samples get a near-lossless H.264 playback copy for phones (labelled). Builds the page with the
before-after-report skill script (--split), then adds the clip players.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .common import RUN, ref_path, q, sh
from .score import TAG, _input

HERE = Path(__file__).parent
SHEET = RUN / "sheet"
BUILD = Path.home() / ".claude/skills/before-after-report/scripts/build_report.py"
MOMENT_SRC = 72  # source frame of the primary window shown for every board (divisible by 2 and 3)
CAP = json.loads((HERE / "capability.json").read_text())


def rows() -> dict:
    out = {}
    for line in (RUN / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        out[(r["clip"], r["enc"], r["target_kb"])] = r
    return out


def still(src_args: str, chain: str, idx: int, out: Path) -> Path:
    sh(f"ffmpeg -v error -y {src_args} -vf {q(chain + f',select=eq(n\\,{idx}),format=rgb24')} -vsync 0 "
       f"-frames:v 1 {q(out)}")
    return out


def ref_still(clip: str, fps: int, out: Path) -> Path:
    idx = MOMENT_SRC * fps // 30
    return still(f"-i {q(ref_path(clip, fps))}", f"format=yuv420p,{TAG}", idx, out)


def enc_still(r: dict, out: Path) -> Path:
    idx = MOMENT_SRC * r["fps"] // 30
    dist = RUN / r["file"]
    return still(_input(dist, r["fps"]), f"scale=out_range=tv,format=yuv420p,{TAG}", idx, out)


def clip_mp4(r: dict | None, out: Path, ref: Path | None = None, fps: int | None = None) -> Path:
    """H.264: remux the as-sent stream (same bytes). Else: near-lossless H.264 playback copy."""
    if r is not None and r["file"].endswith(".h264"):
        sh(f"ffmpeg -v error -y -f h264 -framerate {r['fps']} -i {q(RUN / r['file'])} -c copy "
           f"-movflags +faststart {q(out)}")
        if frames(out) == r["frames"]:
            return out  # same bytes, every frame kept
        # B-frames in a raw stream lose frames in the MP4 remux: make a playback copy instead
    src = f"-i {q(ref)}" if ref is not None else _input(RUN / r["file"], r["fps"])
    rate = fps or r["fps"]
    # Re-time by frame index: a raw AV1 OBU stream carries no frame rate (ffmpeg would assume 25 fps
    # and then drop or repeat frames to reach -r).
    sh(f"ffmpeg -v error -y {src} -vf scale=out_range=tv,format=yuv420p,{TAG},settb=1/{rate},setpts=N "
       f"-c:v libx264 -preset medium "
       f"-crf 14 -pix_fmt yuv420p -r {rate} -colorspace bt709 -color_primaries bt709 -color_trc bt709 "
       f"-movflags +faststart {q(out)}")
    return out


def frames(p: Path) -> int:
    return int(sh(f"ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames "
                  f"-of csv=p=0 {q(p)}").stdout.strip())


def kb(n: int) -> str:
    return f"{n / 1000:.0f} KB" if n < 1_000_000 else f"{n / 1e6:.2f} MB"


def main() -> None:
    R = rows()
    win = json.loads((RUN / "windows.json").read_text())
    clip = win["primary"]
    img, clips = SHEET / "img", SHEET / "clips"
    img.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)
    before = ref_still(clip, 30, img / "before.png")
    for fps in (15, 10):  # the decimated references hold the same frame: check, don't assume
        other = ref_still(clip, fps, img / f"before_{fps}.png")
        if subprocess.run(["cmp", "-s", str(before), str(other)]).returncode != 0:
            raise SystemExit(f"reference frame differs between 30 and {fps} fps")

    def after(enc: str, size: int, label: str) -> dict | None:
        r = R.get((clip, enc, size))
        if not r or not r.get("ok"):
            return None
        p = enc_still(r, img / f"{enc}_{size}.png")
        w = [R.get((c, enc, size)) for c in ("rc_c3", "rc_c1", "rc_lead2")]
        worst = (f" / {min(x['vmaf_mean'] for x in w):.0f} worst of 3 clips" if all(x and x.get("ok") for x in w) else "")
        return {"label": f"{label} · {kb(r['bytes'])} · VMAF {r['vmaf_mean']:.0f} this clip{worst}",
                "image": str(p.relative_to(SHEET))}

    sec = []
    boards = [
        ("IMX708 on the Pi · 30 fps · 5 s",
         "The Pi's own hardware H.264 runs in real time; AV1 on the Pi takes minutes per clip but holds far more "
         "detail at the same size.",
         [("imx708_h264", 500, "H.264, Pi hardware (desk proxy)"), ("pi_av1_30", 500, "AV1 on the Pi"),
          ("imx708_h264", 1000, "H.264, Pi hardware (desk proxy)")]),
        ("N6 · 15 fps · 5 s",
         "Stock N6 firmware encodes only JPEG, and MJPEG cannot get under ~1.3 MB here. At 500 KB the options are "
         "the Pi encoding the N6's stream (works today) or the N6's own H.264 block (needs custom firmware).",
         [("pi_av1_15", 500, "N6 → Pi, AV1 (works today)"), ("n6_h264", 500, "N6 H.264, custom firmware (desk proxy)"),
          ("n6_mjpeg", 0, "N6 MJPEG, smallest setting")]),
        ("AE3 · 10 fps · 5 s",
         "The AE3 has no hardware encoder and today manages ~2 fps at this resolution, so every 720p / 10 fps "
         "picture here is hypothetical. Its MJPEG floor is ~0.8 MB.",
         [("ae3_mjpeg", 1000, "AE3 MJPEG"), ("ae3_mjpeg", 0, "AE3 MJPEG, smallest setting"),
          ("pi_av1_10", 500, "AE3 → Pi, AV1 (if the AE3 could deliver 720p10)")]),
    ]
    for title, note, alist in boards:
        afters = [a for a in (after(*x) for x in alist) if a]
        sec.append({"title": title, "note": note, "before": str(before.relative_to(SHEET)),
                    "before_label": "Source (Reef Check, 720p)", "afters": afters})

    # Tables
    cap_rows = [[c["board"], c["encoder"], c["status"], c["fps_720p"], c["where"], c["note"]] for c in CAP["rows"]]
    summ = json.loads((RUN / "summary.json").read_text())
    g = "moving camera (Reef Check, 3 windows)"
    names = {"imx708_h264": "IMX708 · H.264 Pi hw (proxy) · 30 fps", "pi_av1_30": "IMX708 · AV1 on the Pi · 30 fps",
             "n6_mjpeg": "N6 · MJPEG on-board · 15 fps", "n6_h264": "N6 · H.264 on-board (custom fw, proxy) · 15 fps",
             "pi_av1_15": "N6 → Pi · AV1 · 15 fps", "ae3_mjpeg": "AE3 · MJPEG on-board · 10 fps",
             "pi_av1_10": "AE3 → Pi · AV1 · 10 fps"}
    qrows = []
    for enc, nm in names.items():
        s = summ.get(g, {}).get(enc)
        if s:
            qrows.append([nm] + [c.split(" / ")[0] for c in s["cells"]] + [s["knee"]])
    tables = [
        {"title": "Which board can encode what at 720p",
         "note": "From board docs, firmware source and our earlier rig measurements; no builds. 'Custom work' = "
                 "a firmware build or new encoder code.", "table": {
             "columns": ["Board", "Encoder", "Today", "720p at rated fps?", "Where it runs", "Note"], "rows": cap_rows}},
        {"title": "Quality per 5 s clip, worst of 3 moving-camera clips",
         "note": "Desk emulation on the Mac: x264 'veryfast' stands in for hardware H.264 (likely flattering it), "
                 "libjpeg for MJPEG, the Pi's own SVT-AV1 recipe for AV1. VMAF (0–100; 70 ≈ good, 60 ≈ watchable) "
                 "on the WORST of the 3 moving-camera clips, each board against the source at its own frame rate. "
                 "The pictures below show the primary clip, so their VMAF is labelled 'this clip'.",
         "table": {"columns": ["Board · encoder · fps", "100 KB", "500 KB", "1 MB", "Knee"], "rows": qrows}},
        {"title": "Pi cost per 5 s clip (estimate, no Pi runs in this screen)",
         "note": "From the calibrated 10/15 study (rig #116) and the 10/02 real-Pi bench (PR #88). Real-Pi runs come "
                 "after pool test 2, if you ask.",
         "table": {"columns": ["Encode on the Pi", "Time", "Energy", "Basis"], "rows": CAP["cost_rows"]}},
    ]
    spec = {
        "title": "720p video screen",
        "eyebrow": f"Nereus Vision · E7 first look · desk emulation · {CAP['date']}",
        "lede": CAP["lede"],
        "inline_max_width": 800,
        "facts": CAP["facts"],
        "sections": tables + sec,
        "notes_title": "Read with care",
        "notes": CAP["notes"],
        "footer": CAP["footer"],
    }
    (SHEET / "spec.json").write_text(json.dumps(spec, indent=1))
    subprocess.run([sys.executable, str(BUILD), str(SHEET / "spec.json"), str(SHEET / "index.html"), "--split"],
                   check=True)

    # Sample clips (one per board at 500 KB where the board's path can reach it), injected before the notes
    picks = [("Source, 30 fps (lossless reference, playback copy)", None, ("ref", 30)),
             ("IMX708 · H.264 Pi hardware (desk proxy) · 500 KB · 30 fps", ("imx708_h264", 500), None),
             ("IMX708 · AV1 on the Pi · 500 KB · 30 fps", ("pi_av1_30", 500), None),
             ("N6 → Pi · AV1 · 500 KB · 15 fps", ("pi_av1_15", 500), None),
             ("N6 · H.264 on-board, custom firmware (desk proxy) · 500 KB · 15 fps", ("n6_h264", 500), None),
             ("AE3 · MJPEG on-board · 1 MB · 10 fps (500 KB not reachable)", ("ae3_mjpeg", 1000), None),
             ("AE3 → Pi · AV1 · 500 KB · 10 fps (hypothetical frame rate)", ("pi_av1_10", 500), None)]
    files = json.loads((SHEET / "index_files.json").read_text())
    blocks = []
    for label, key, refspec in picks:
        if refspec:
            out = clip_mp4(None, clips / "source_30fps.mp4", ref=ref_path(clip, 30), fps=30)
            sent = "playback copy"
        else:
            r = R.get((clip, *key))
            if not r or not r.get("ok"):
                continue
            out = clip_mp4(r, clips / f"{key[0]}_{key[1]}.mp4")
            exact = r["file"].endswith(".h264") and frames(out) == r["frames"] and \
                out.stat().st_size < r["bytes"] * 1.05
            sent = ("as-sent H.264 bytes in an MP4 wrapper" if exact
                    else f"playback copy; the as-sent file is {kb(r['bytes'])}")
        rel = f"clips/{out.name}"
        files[rel] = str(out)
        blocks.append(f'<figure><video src="{rel}" controls playsinline muted loop preload="metadata"></video>'
                      f'<figcaption><b>{label}</b><br><span>{sent} · {out.stat().st_size / 1e6:.1f} MB</span>'
                      f'</figcaption></figure>')
    css = ("<style>.clips .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}"
           ".clips video{width:100%;border-radius:6px;background:#000;display:block}"
           ".clips figure{margin:0}.clips figcaption{font-size:.85rem;margin-top:4px}"
           ".clips figcaption span{color:var(--ink2)}</style>")
    clip_html = (f'{css}<section class="clips"><header><h2>Sample clips (5 s, primary window)</h2>'
                 f'<p>Tap to play. The players loop and start muted. Sizes under each clip are what would be sent.</p>'
                 f'</header><div class="grid">{"".join(blocks)}</div></section>')
    page = (SHEET / "index.html").read_text()
    marker = next((m for m in ('<section class="notes">', "<footer>", "</main>") if m in page), None)
    if marker is None:
        raise SystemExit("no insertion point for the clips in the built page")
    page = page.replace(marker, clip_html + marker, 1)
    (SHEET / "index.html").write_text(page)
    (SHEET / "index_files.json").write_text(json.dumps(files, indent=1))
    total = sum(Path(p).stat().st_size for p in files.values()) + (SHEET / "index.html").stat().st_size
    print(f"page + {len(files)} files, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
