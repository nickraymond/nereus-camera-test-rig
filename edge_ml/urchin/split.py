"""Sampling and the held-out split for the urchin study (PREREG "Q1 measure", "Held-out split").

Pure Python + numpy. A still is identified by its file name; its video, time and group come
from stills.csv (extract_stills.py) plus a group map {video: group} written by hand from the
dataset's folder names / Reef Check's notes, frozen in git (groups_reefcheck.json).

    python -m edge_ml.urchin.split q1-sample   <stills.csv> <q1_sample.txt>
    python -m edge_ml.urchin.split screen-list <stills.csv> <groups.json> <group_screen.csv>
    python -m edge_ml.urchin.split folds       <stills.csv> <groups.json> <group_screen.csv> <out>

Order (PREREG): fill and commit group_screen.csv (yes / no / unsure) BEFORE running `folds`,
which is the first step that computes group hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GAP_S = 30.0  # PREREG: discarded between a video's train and val/test parts
VAL_FRACTION = 0.2
SAME_DIVE_TRAIN = 0.7


@dataclass(frozen=True)
class Still:
    name: str
    video: str
    t_s: float


def proportional_counts(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder allocation of n draws over strata, proportional to size."""
    total = sum(sizes.values())
    if n >= total:
        return dict(sizes)
    quotas = {k: n * v / total for k, v in sizes.items()}
    out = {k: math.floor(q) for k, q in quotas.items()}
    for k in sorted(quotas, key=lambda k: (-(quotas[k] - out[k]), k))[: n - sum(out.values())]:
        out[k] += 1
    return out


def proportional_sample(stills: list[Still], n: int, seed: int = 0) -> list[Still]:
    """Seeded sample of n stills, drawn in proportion to each video's stills."""
    by_video: dict[str, list[Still]] = defaultdict(list)
    for s in sorted(stills, key=lambda s: (s.video, s.t_s)):
        by_video[s.video].append(s)
    counts = proportional_counts({v: len(ss) for v, ss in by_video.items()}, n)
    rng = np.random.default_rng(seed)
    out = []
    for v in sorted(by_video):
        idx = rng.choice(len(by_video[v]), counts[v], replace=False)
        out += [by_video[v][i] for i in sorted(idx)]
    return out


def evenly_spaced(stills: list[Still], k: int = 40) -> list[Still]:
    """Up to k stills evenly spaced in time order (all of them if fewer). PREREG screen."""
    ordered = sorted(stills, key=lambda s: (s.video, s.t_s))
    if len(ordered) <= k:
        return ordered
    idx = np.unique(np.round(np.linspace(0, len(ordered) - 1, k)).astype(int))
    return [ordered[i] for i in idx]


def safe_name(group: str) -> str:
    """Group name as a directory name: exFAT (the data SSD) forbids | and friends."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in group)


def name_hash(group: str) -> str:
    return hashlib.sha256(group.encode()).hexdigest()


@dataclass
class Fold:
    name: str
    train: list[Still] = field(default_factory=list)
    val: list[Still] = field(default_factory=list)
    test: list[Still] = field(default_factory=list)
    test_groups: list[str] = field(default_factory=list)
    mode: str = ""  # "held_out_groups" | "leave_one_group_out" | "same_dive"

    def info(self, groups: dict[str, str]) -> dict:
        """fold.json content: what evaluate.py needs to know about this fold."""
        return {
            "name": self.name,
            "mode": self.mode,
            "test_groups": self.test_groups,
            "train_groups": sorted({groups[s.video] for s in self.train}),
            "val_groups": sorted({groups[s.video] for s in self.val}),
            "n": {"train": len(self.train), "val": len(self.val), "test": len(self.test)},
        }


def time_split(stills: list[Still], fraction_first: float) -> tuple[list[Still], list[Still]]:
    """Per video: first `fraction_first` of its duration vs the rest, GAP_S discarded between."""
    first, last = [], []
    by_video: dict[str, list[Still]] = defaultdict(list)
    for s in stills:
        by_video[s.video].append(s)
    for ss in by_video.values():
        t0, t1 = min(s.t_s for s in ss), max(s.t_s for s in ss)
        cut = t0 + fraction_first * (t1 - t0)
        first += [s for s in ss if s.t_s < cut - GAP_S]
        last += [s for s in ss if s.t_s >= cut]
    return sorted(first, key=_key), sorted(last, key=_key)


def _key(s: Still) -> tuple[str, float]:
    return (s.video, s.t_s)


def train_val(
    stills: list[Still], groups: dict[str, str], eligible: set[str]
) -> tuple[list[Still], list[Still]]:
    """PREREG validation: the lowest-hash *eligible* training group only if >= 3 eligible
    training groups remain (B keeps >= 2 with urchins), else the last 20 % of each training
    video after GAP_S."""
    elig = sorted({groups[s.video] for s in stills} & eligible, key=name_hash)
    if len(elig) >= 3:
        val_group = elig[0]
        return (
            [s for s in stills if groups[s.video] != val_group],
            [s for s in stills if groups[s.video] == val_group],
        )
    return time_split(stills, 1 - VAL_FRACTION)


def make_folds(stills: list[Still], groups: dict[str, str], eligible: set[str]) -> list[Fold]:
    """PREREG held-out split. groups maps video → group; eligible = groups that may be tested."""
    all_groups = sorted({groups[s.video] for s in stills})
    elig = sorted((g for g in all_groups if g in eligible), key=name_hash)
    if not elig:
        raise ValueError("no eligible group: nothing can be held out")
    if len(elig) >= 4:
        test_groups = [elig[i] for i in range(max(2, math.ceil(len(elig) / 3)))]
        plan = [("held_out", test_groups, "held_out_groups")]
    elif len(elig) >= 2:
        plan = [(f"logo_{safe_name(g)}", [g], "leave_one_group_out") for g in elig]
    else:
        plan = []
    folds = []
    for name, test_groups, mode in plan:
        train_all = [s for s in stills if groups[s.video] not in test_groups]
        train, val = train_val(train_all, groups, eligible)
        test = [s for s in stills if groups[s.video] in test_groups]
        folds.append(Fold(name, train, val, sorted(test, key=_key), test_groups, mode))
    if not plan:  # one eligible group: same-dive time split (outcome "inconclusive")
        g = elig[0]
        in_g = [s for s in stills if groups[s.video] == g]
        first, test = time_split(in_g, SAME_DIVE_TRAIN)
        train_all = first + [s for s in stills if groups[s.video] != g]
        train, val = time_split(train_all, 1 - VAL_FRACTION)  # always, in the first 70 %
        folds.append(Fold("same_dive", train, val, test, [g], "same_dive"))
    return folds


CAPS = {"train": 300, "val": 100, "test": 150}  # PREREG caps
SCREEN_K = 40
PREREG_PATH = Path(__file__).with_name("PREREG.md")
SCREEN_VALUES = {"yes", "no", "unsure"}


def read_stills_csv(path: Path) -> list[Still]:
    """Kept stills from extract_stills.py's stills.csv; name = '<video stem>/<still>'."""
    out = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["status"] == "kept":
                stem = Path(row["video"]).stem
                out.append(Still(f"{stem}/{row['still']}", stem, float(row["t_s"])))
    return out


def eligible_from_screen(path: Path, groups: set[str]) -> set[str]:
    """Groups with any 'yes' or 'unsure' (PREREG: unsure counts as present). Fails on blanks."""
    seen, elig = set(), set()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            v = row["purple_urchin"].strip().lower()
            if v not in SCREEN_VALUES:
                raise ValueError(f"{path}: {row['still']}: purple_urchin={v!r}, want yes/no/unsure")
            seen.add(row["group"])
            if v in ("yes", "unsure"):
                elig.add(row["group"])
    if missing := groups - seen:
        raise ValueError(f"{path}: groups not screened: {sorted(missing)}")
    return elig


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_folds(
    folds: list[Fold], groups: dict[str, str], out_dir: Path, inputs: dict[str, str] | None = None
) -> None:
    """Per fold: capped train/val/test lists + fold.json (Fold.info, caps and input SHA-256s).

    Fails before writing anything if a held-out group has no test stills after the cap."""
    capped_folds = []
    for fold in folds:
        before = {"train": len(fold.train), "val": len(fold.val), "test": len(fold.test)}
        for part in ("train", "val", "test"):
            setattr(fold, part, proportional_sample(getattr(fold, part), CAPS[part], seed=0))
        if empty := [
            g for g in fold.test_groups if not any(groups[x.video] == g for x in fold.test)
        ]:
            raise ValueError(
                f"fold {fold.name}: held-out group(s) {empty} have 0 test stills "
                f"after the cap of {CAPS['test']}"
            )
        capped_folds.append((fold, before))
    for fold, before in capped_folds:
        d = out_dir / fold.name
        d.mkdir(parents=True, exist_ok=False)
        for part in ("train", "val", "test"):
            (d / f"{part}.txt").write_text("".join(f"{s.name}\n" for s in getattr(fold, part)))
        info = fold.info(groups)
        info["n_before_caps"] = before
        info["caps"] = CAPS
        info["inputs_sha256"] = inputs or {}
        (d / "fold.json").write_text(json.dumps(info, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("q1-sample", help="100 stills (seed 0) for the Q1 usability review")
    q.add_argument("stills_csv", type=Path)
    q.add_argument("out", type=Path)
    s = sub.add_parser("screen-list", help="per-group screen rows to fill (alphabetical order)")
    s.add_argument("stills_csv", type=Path)
    s.add_argument("groups", type=Path)
    s.add_argument("out", type=Path)
    fo = sub.add_parser("folds", help="eligible groups from the screen → capped folds")
    fo.add_argument("stills_csv", type=Path)
    fo.add_argument("groups", type=Path)
    fo.add_argument("screen", type=Path)
    fo.add_argument("out_dir", type=Path)
    args = ap.parse_args(argv)
    stills = read_stills_csv(args.stills_csv)
    if args.cmd == "q1-sample":
        args.out.write_text("".join(f"{x.name}\n" for x in proportional_sample(stills, 100, 0)))
        return 0
    groups = json.loads(args.groups.read_text())
    if missing := {x.video for x in stills} - set(groups):
        raise ValueError(f"videos missing from the group map: {sorted(missing)}")
    if args.cmd == "screen-list":
        with args.out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["group", "still", "purple_urchin", "notes"])
            for g in sorted(set(groups.values())):  # alphabetical, never hash order
                for x in evenly_spaced([x for x in stills if groups[x.video] == g], SCREEN_K):
                    w.writerow([g, x.name, "", ""])
        return 0
    groups_sha = sha256_file(args.groups)
    if groups_sha not in PREREG_PATH.read_text():
        raise ValueError(f"{args.groups}: SHA-256 {groups_sha} is not the map frozen in PREREG.md")
    eligible = eligible_from_screen(args.screen, {groups[x.video] for x in stills})
    folds = make_folds(stills, groups, eligible)
    inputs = {
        "groups": groups_sha,
        "group_screen": sha256_file(args.screen),
        "stills_csv": sha256_file(args.stills_csv),
    }
    write_folds(folds, groups, args.out_dir, inputs)
    for fold in folds:
        print(json.dumps(fold.info(groups)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
