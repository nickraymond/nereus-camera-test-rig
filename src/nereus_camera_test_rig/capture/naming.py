"""Experiment and capture naming — Spec §12, §13.

One canonical timestamp format across the whole rig (the prior art used two
different ones — see prior_art_review.md). Names are deterministic given their
inputs so experiments never collide and are easy to sort/parse.

Timestamp convention: UTC, compact ISO basic form ``YYYYMMDDThhmmssZ``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# Conservative slug: lowercase alphanumerics and underscores only.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def utc_timestamp(when: datetime | None = None) -> str:
    """Return a compact UTC timestamp string.

    ``when`` must be timezone-aware if provided; a naive datetime is rejected so we
    never silently mislabel local time as UTC. Defaults to the current UTC time.
    """
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        raise ValueError("naive datetime not allowed; pass a tz-aware datetime")
    return when.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def slugify(text: str) -> str:
    """Normalize a label into a filesystem-safe slug (lowercase, underscores)."""
    slug = _SLUG_STRIP.sub("_", text.strip().lower()).strip("_")
    return slug


def experiment_id(experiment_type: str, when: datetime | None = None) -> str:
    """Build an experiment id, e.g. ``exp_20260714T180000Z_reference_card_above_water``."""
    stamp = utc_timestamp(when)
    slug = slugify(experiment_type)
    return f"exp_{stamp}_{slug}" if slug else f"exp_{stamp}"


def date_folder(when: datetime | None = None) -> str:
    """Return the ``YYYY-MM-DD`` date-partition folder name (Spec §13 layout)."""
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        raise ValueError("naive datetime not allowed; pass a tz-aware datetime")
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


def capture_filename(camera: str, kind: str, extension: str, when: datetime | None = None) -> str:
    """Build a capture filename, e.g. ``imx708_image_20260714T180000Z.jpg``."""
    ext = extension.lstrip(".")
    return f"{slugify(camera)}_{slugify(kind)}_{utc_timestamp(when)}.{ext}"
