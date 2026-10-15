"""Sampling and the held-out split for the urchin study (PREREG "Q1 measure", "Held-out split").

Pure Python + numpy. A still is identified by its file name; its video, time and group come
from stills.csv (extract_stills.py) plus a group map {video: group} written by hand from the
dataset's folder names / Reef Check's notes and committed with the run's MANIFEST.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field

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


def train_val(stills: list[Still], groups: dict[str, str]) -> tuple[list[Still], list[Still]]:
    """PREREG validation: one training group (lowest hash) if >= 3 training groups, else the
    last 20 % of each training video with GAP_S discarded before it."""
    tg = sorted({groups[s.video] for s in stills}, key=name_hash)
    if len(tg) >= 3:
        val_group = tg[0]
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
        plan = [(f"logo_{g}", [g], "leave_one_group_out") for g in elig]
    else:
        plan = []
    folds = []
    for name, test_groups, mode in plan:
        train_all = [s for s in stills if groups[s.video] not in test_groups]
        train, val = train_val(train_all, groups)
        test = [s for s in stills if groups[s.video] in test_groups]
        folds.append(Fold(name, train, val, sorted(test, key=_key), test_groups, mode))
    if not plan:  # one eligible group: same-dive time split (outcome "inconclusive")
        g = elig[0]
        in_g = [s for s in stills if groups[s.video] == g]
        first, test = time_split(in_g, SAME_DIVE_TRAIN)
        train_all = first + [s for s in stills if groups[s.video] != g]
        train, val = train_val(train_all, groups)
        folds.append(Fold("same_dive", train, val, test, [g], "same_dive"))
    return folds
