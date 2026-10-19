"""The Wed card's W1 inputs as one committed file (QE 008 R4 on rig #133), read by every stage.

The values are set on the Wed card before any data (spec rev 13 / 16 / 17) and must not be typed in
after the dive. They live in one JSON file committed to the rig repo (like the box file); every
stage checks it is committed and unchanged, and its sha256 goes into the scan manifest, the box
file, detections.json and RESULTS. Schema:

  {"source": "Wed card <path> @ <sha> (who, when)",
   "partb": {"openmv_n6": "420" | "444", "openmv_ae3": "420" | "444"},  # part-B "holds" chroma
   "first_bin": {"openmv_n6": 1.0, ...},  # optional; bin centre >= 1.0 from the 1.0 m fit check
   "guard_cameras": ["openmv_ae3", ...],  # rev 16: cameras whose Wed mode can't be mapped
   "detector": {"model_id": ..., "revision": ..., "threshold": 0.3}}   # rev 17, must equal DETECTOR

The detector is pinned here (rev 17, PE 005, Sun 10/18 9:43 PM): OWLv2 at the only cached snapshot,
threshold 0.3 = edge_ml/urchin/pilot.py CAND_SCORES[1] @ 4d6f7d9. `detect` keeps every box down to
DETECT_FLOOR; W1 decides at the threshold, and 0.05 / 0.5 are reported beside it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from edge_ml.pool2 import wscore

DETECTOR = {"model_id": "google/owlv2-base-patch16-ensemble",
            "revision": "cfd3195ba4ea9592eec887ded089f4c08eff231d", "threshold": 0.3}
DETECT_FLOOR = 0.05                 # rev 17 report-only chain; detect saves every box >= this
THRESHOLDS = (0.05, 0.3, 0.5)       # hits are computed at each; W1 decides at DETECTOR threshold
CHROMAS = ("420", "444")
BOARDS = ("openmv_n6", "openmv_ae3")
CAMERAS = ("imx708",) + BOARDS


def tkey(t: float) -> str:
    """A threshold as a dict key: 0.3 -> '0.3'."""
    return f"{t:g}"


class CardValuesError(SystemExit):
    pass


@dataclass(frozen=True)
class CardValues:
    path: Path
    sha256: str
    source: str
    partb: dict
    first_bin: dict
    guard_cameras: tuple
    detector: dict

    def record(self) -> dict:
        return {"path": str(self.path), "sha256": self.sha256, "source": self.source,
                "partb": self.partb, "first_bin": self.first_bin,
                "guard_cameras": list(self.guard_cameras), "detector": self.detector}


def load(path: str | Path) -> CardValues:
    """Parse and check the card-values file (commit state is checked by the caller)."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise CardValuesError(f"{path}: not JSON ({exc})") from exc
    where = str(path)
    if not str(d.get("source") or "").strip():
        raise CardValuesError(f"{where}: source (the Wed card and its sha) is required")
    partb = d.get("partb") or {}
    if set(partb) != set(BOARDS) or any(str(v) not in CHROMAS for v in partb.values()):
        raise CardValuesError(f"{where}: partb must give each of {BOARDS} one of {CHROMAS}, "
                              f"got {partb!r}")
    first = {}
    for cam, v in (d.get("first_bin") or {}).items():
        if cam not in CAMERAS:
            raise CardValuesError(f"{where}: first_bin: unknown camera {cam!r}")
        if not isinstance(v, (int, float)) or float(v) not in wscore.BIN_CENTRES \
                or float(v) < wscore.FIRST_BIN:
            raise CardValuesError(f"{where}: first_bin {cam}={v!r}: must be a bin centre >= "
                                  f"{wscore.FIRST_BIN} m (rev 13: the chain starts at 1.0 m)")
        first[cam] = float(v)
    guard = tuple(d.get("guard_cameras") or ())
    bad = [c for c in guard if c not in CAMERAS]
    if bad:
        raise CardValuesError(f"{where}: guard_cameras: unknown camera(s) {bad}")
    det = d.get("detector") or {}
    if det != DETECTOR:
        raise CardValuesError(f"{where}: detector {det!r} is not the rev 17 pin {DETECTOR!r}")
    return CardValues(path=path, sha256=hashlib.sha256(raw).hexdigest(),
                      source=str(d["source"]), partb={k: str(v) for k, v in partb.items()},
                      first_bin={c: first.get(c, wscore.FIRST_BIN) for c in CAMERAS},
                      guard_cameras=guard, detector=dict(det))
