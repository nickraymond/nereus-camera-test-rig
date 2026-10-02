"""Mac "ceiling" encodes — what the same budgets buy with slow, best-effort encoders.

    python -m compression_study.video.mac_encode --sources <data>/sources --out <data>/enc_mac

None of these run on the rig devices; they bound what a better encoder (more CPU, or the
N6's own H.264 block once OpenMV exposes it) could reach. Same sources, budgets, one key frame
per clip and rate target (budget × 8 / duration) as ``pi_encode.py``.

- ``h264_x264``  x264 preset medium, 2-pass      (GPL: Mac benchmark only, §20 study rule)
- ``h265_x265``  x265 preset medium, 2-pass      (GPL: Mac benchmark only)
- ``av1_svt_p4`` SVT-AV1 3.1.2 preset 4, 1-pass VBR (BSD; the Pi runs 2.3.0)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

GOP = "600"


def encode(enc: str, src: Path, br: int, out: Path) -> float:
    t0 = time.time()
    base = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src)]
    env = dict(os.environ, SVT_LOG="1")
    if enc == "av1_svt_p4":
        subprocess.run(
            [
                *base,
                "-c:v",
                "libsvtav1",
                "-preset",
                "4",
                "-b:v",
                str(br),
                "-g",
                GOP,
                "-an",
                str(out),
            ],
            check=True,
            env=env,
        )
    else:
        lib, extra = {
            "h264_x264": ("libx264", []),
            "h265_x265": ("libx265", ["-tag:v", "hvc1", "-x265-params", "log-level=error"]),
        }[enc]
        with tempfile.TemporaryDirectory() as td:
            pl = str(Path(td) / "pass")
            common = [
                "-c:v",
                lib,
                "-preset",
                "medium",
                "-b:v",
                str(br),
                "-g",
                GOP,
                "-passlogfile",
                pl,
                *extra,
                "-an",
            ]
            subprocess.run(
                [*base, *common, "-pass", "1", "-f", "mp4", os.devnull], check=True, cwd=td
            )
            subprocess.run([*base, *common, "-pass", "2", str(out)], check=True, cwd=td)
    return time.time() - t0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--encoders", nargs="*", default=["h264_x264", "h265_x265", "av1_svt_p4"])
    ap.add_argument("--budgets", nargs="*", type=int, default=[50, 100, 200, 400, 800, 1600])
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args(argv)
    meta = json.loads((a.sources / "sources.json").read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    with (a.out / "jobs.jsonl").open("a") as log:
        for stem in a.only or [k for k in meta if not k.startswith("_")]:
            m = meta[stem]
            (a.out / stem).mkdir(exist_ok=True)
            for enc in a.encoders:
                for kb in a.budgets:
                    out = a.out / stem / f"{enc}_{kb}k.mp4"
                    if out.exists():
                        continue
                    br = int(kb * 1000 * 8 / m["duration_s"])
                    wall = encode(enc, a.sources / m["file"], br, out)
                    rec = {
                        "kind": "encode",
                        "source": stem,
                        "encoder": enc,
                        "budget_kB": kb,
                        "target_bps": br,
                        "frames": m["frames"],
                        "duration_s": round(m["duration_s"], 3),
                        "file": str(out.relative_to(a.out)),
                        "size_B": out.stat().st_size,
                        "wall_s": round(wall, 1),
                        "status": "ok",
                        "host": "mac-m1max",
                    }
                    log.write(json.dumps(rec) + "\n")
                    log.flush()
                    print(stem, enc, kb, out.stat().st_size, round(wall, 1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
