"""Measure a printed card's truth beside a reference chart (card V3 c1, Nick 2026-10-05).

    python -m host_tools.card_truth configs/calibration/sessions/v3c1_truth_dry_<date>.yaml \
        [--root .] [--dry-run]

Thin CLI over ``nereus_camera_test_rig.color.card_truth``: prints the chart fit's held-out
ΔE00, the card values and the evenness, writes overlays + a JSON report under
``results/card_truth/<session>/``, and (unless ``--dry-run``) the ``measured:`` block into the
card YAML. Procedure: docs/v3_card_truth_capture.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.config import load_yaml


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--root", type=Path, default=Path("."), help="repo root (paths in the session)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not touch the card YAML")
    a = ap.parse_args(argv)
    s = load_yaml(a.session)
    out = a.root / "results" / "card_truth" / s["session"]
    res = T.measure(s, a.root, overlay_dir=out)
    date = s.get("date_utc") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    block = T.measured_block(res, s, date)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "measured_block.yaml").write_text(block)
    ft = res["fit"]
    print(f"frames used {res['n_frames']}/{len(res['samples'])}; "
          f"chart patches {res['chart_patches_used']}; card patches {res['card_patches']}")
    for m, st in ft["loo_de2000"].items():
        print(f"  held-out dE00 ({m}): median {st['median']}  p90 {st['p90']}  max {st['max']}"
              + ("   <- used" if m == ft["model"] else ""))
    print(f"  flat-field spread (p95/p5 - 1): {res['flat_spread_pct_p95_p5']} %"
          if res["flat_frames"] else "  no flat-field frames (light falloff not corrected)")
    if res["out_of_srgb"]:
        print(f"  outside sRGB (clipped in values, exact in lab_d50): {res['out_of_srgb']}")
    if res["over_range"]:
        print("  WARNING over the 300 cap (light uneven or card brighter than the chart): "
              f"{res['over_range']}")
    for s_ in res["samples"]:
        if "error" in s_ or "error" in s_.get("chart", {}):
            print(f"  skipped {s_['file']}: {s_.get('error') or s_['chart']['error']}")
    print(f"overlays + report: {out}")
    if a.dry_run:
        print(block)
        return 0
    T.write_block(a.root / s["card"], block)
    print(f"wrote {block.split(':', 1)[0]}: into {s['card']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
