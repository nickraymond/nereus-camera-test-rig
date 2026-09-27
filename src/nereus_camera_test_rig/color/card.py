"""Reference-card definition loaded from ``configs/cards/<card>.yaml`` — SPEC §20.

The card is config, not code: tag layout, canonical geometry, patch boxes and truth values
all come from the YAML, so card V3 is a new file rather than a code change. Loading
validates the structure and fails loudly with the file and field at fault (CLAUDE.md §17).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import ConfigError, load_yaml

CORNERS = ("tl", "tr", "bl", "br")
GROUPS = ("grey", "color")


class CardError(ConfigError):
    """Raised when a card YAML is missing a field or is internally inconsistent."""


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in canonical pixels."""

    x: int
    y: int
    w: int
    h: int

    def contains(self, other: "Box") -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.x + other.w <= self.x + self.w
            and other.y + other.h <= self.y + self.h
        )


@dataclass(frozen=True)
class Patch:
    id: str
    group: str  # "grey" | "color"
    label: str
    box: Box
    truth: tuple[int, int, int]


@dataclass(frozen=True)
class SubPatch:
    id: str
    parent: str
    box: Box


@dataclass(frozen=True)
class Tag:
    id: int
    center: tuple[float, float]  # canonical px
    edge: tuple[float, float]  # canonical px along x, y (canonical px are not square)


@dataclass(frozen=True)
class Card:
    card_id: str
    path: Path
    tag_family: str
    corner_map: dict[str, int]
    canonical_w: int
    canonical_h: int
    expand_x: float
    expand_y: float
    tags: dict[int, Tag]
    physical_mm: dict[str, Optional[float]]
    physical_source: str
    truth_source: str
    patches: tuple[Patch, ...]
    sub_patches: tuple[SubPatch, ...]

    @property
    def physically_measured(self) -> bool:
        """False while any physical dimension is unknown (distances would be provisional)."""
        return all(v is not None for v in self.physical_mm.values())

    def patch(self, patch_id: str) -> Patch:
        for p in self.patches:
            if p.id == patch_id:
                return p
        raise KeyError(f"{self.card_id}: no patch {patch_id!r}")

    def group(self, group: str) -> tuple[Patch, ...]:
        return tuple(p for p in self.patches if p.group == group)


def _get(data: dict, key: str, where: str) -> Any:
    if key not in data:
        raise CardError(f"{where}: missing field {key!r}")
    return data[key]


def _box(raw: Any, where: str, canvas: Box) -> Box:
    if not (isinstance(raw, list) and len(raw) == 4 and all(isinstance(v, int) for v in raw)):
        raise CardError(f"{where}: box must be [x, y, w, h] integers, got {raw!r}")
    box = Box(*raw)
    if box.w <= 0 or box.h <= 0 or not canvas.contains(box):
        raise CardError(f"{where}: box {raw} is empty or outside the canonical frame")
    return box


def load_card(path: str | Path) -> Card:
    """Load and validate a card YAML. Raises ``CardError`` on any structural problem."""
    p = Path(path)
    data = load_yaml(p)
    where = str(p)

    canonical = _get(data, "canonical", where)
    canvas = Box(0, 0, int(_get(canonical, "width", where)), int(_get(canonical, "height", where)))

    april = _get(data, "apriltag", where)
    corner_map = {str(k): int(v) for k, v in _get(april, "corner_map", where).items()}
    if sorted(corner_map) != sorted(CORNERS):
        raise CardError(
            f"{where}: corner_map must name exactly {CORNERS}, got {sorted(corner_map)}"
        )

    tags: dict[int, Tag] = {}
    for tid, raw in _get(data, "tags", where).items():
        center, edge = _get(raw, "center", where), _get(raw, "edge", where)
        tags[int(tid)] = Tag(int(tid), (float(center[0]), float(center[1])),
                             (float(edge[0]), float(edge[1])))
    missing = sorted(set(corner_map.values()) - set(tags))
    if missing:
        raise CardError(f"{where}: corner_map tags {missing} have no entry under 'tags'")

    patches: list[Patch] = []
    for i, raw in enumerate(_get(data, "patches", where)):
        pw = f"{where}: patches[{i}]"
        group = _get(raw, "group", pw)
        if group not in GROUPS:
            raise CardError(f"{pw}: group must be one of {GROUPS}, got {group!r}")
        truth = _get(raw, "truth", pw)
        if not (len(truth) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in truth)):
            raise CardError(f"{pw}: truth must be three 0-255 integers, got {truth!r}")
        patches.append(Patch(str(_get(raw, "id", pw)), group, str(raw.get("label", "")),
                             _box(_get(raw, "box", pw), pw, canvas), tuple(truth)))
    ids = [p.id for p in patches]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CardError(f"{where}: duplicate patch ids {dupes}")

    by_id = {p.id: p for p in patches}
    subs: list[SubPatch] = []
    for i, raw in enumerate(data.get("sub_patches") or []):
        sw = f"{where}: sub_patches[{i}]"
        parent = str(_get(raw, "parent", sw))
        if parent not in by_id:
            raise CardError(f"{sw}: unknown parent patch {parent!r}")
        box = _box(_get(raw, "box", sw), sw, canvas)
        if not by_id[parent].box.contains(box):
            raise CardError(f"{sw}: box {list(vars(box).values())} is outside parent {parent!r}")
        subs.append(SubPatch(str(_get(raw, "id", sw)), parent, box))

    truth = _get(data, "truth", where)
    physical = dict(_get(data, "physical_mm", where))
    physical_source = str(physical.pop("source", "unspecified"))
    return Card(
        card_id=str(_get(data, "card_id", where)),
        path=p,
        tag_family=str(_get(april, "family", where)),
        corner_map=corner_map,
        canonical_w=canvas.w,
        canonical_h=canvas.h,
        expand_x=float(_get(canonical, "expand_x", where)),
        expand_y=float(_get(canonical, "expand_y", where)),
        tags=tags,
        physical_mm={k: (None if v is None else float(v)) for k, v in physical.items()},
        physical_source=physical_source,
        truth_source=str(_get(truth, "source", where)),
        patches=tuple(patches),
        sub_patches=tuple(subs),
    )
