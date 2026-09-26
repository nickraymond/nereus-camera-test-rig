"""Host tool: licence check for the shipped dependency closure — SPEC §20.

Walks the base + ``[color]`` dependencies declared in ``pyproject.toml`` through the
installed packages' own requirements, and checks every package against the reviewed table
in ``configs/licenses.yaml``. Fails (exit 1) on:

- a package in the shipped closure with no table entry, or an ``internal_only`` one;
- a GPL / LGPL / AGPL licence or classifier, or a table licence outside the allowlist;
- a declared licence that no longer matches the table (re-review after upgrades);
- a vendored shared library (``.so`` / ``.dylib`` / ``.dll`` that is not the package's own
  Python extension module) the entry doesn't cover.

``--shipped`` additionally runs each ``dev_only`` package's probe, proving a shipped install
replaced it (e.g. OpenCV built without FFmpeg, OQ-36). ``pyproject.toml`` is read directly,
not the installed dist-info, which can be stale in a git worktree.

Usage::

    python -m host_tools.license_check              # development environment
    python -m host_tools.license_check --shipped    # a Pi / backend image
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.metadata as md
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
TABLE = REPO / "configs" / "licenses.yaml"
SHIPPED_EXTRAS = ("color",)
INTERNAL_EXTRAS = ("tg7",)
NATIVE_SUFFIXES = (".so", ".dylib", ".dll", ".pyd")
# A package's own compiled Python extensions (foo.cpython-313-darwin.so, foo.abi3.so,
# foo.pyd) are its own code under its own licence; only *other* shared libraries are
# vendored third-party code (delocate/auditwheel put them in .dylibs/ or <pkg>.libs/).
EXTENSION_MODULE = re.compile(r"(\.(cpython|cp|pypy)[^/]*|\.abi3)\.(so|pyd)$|\.pyd$")
COPYLEFT = re.compile(r"\b(A?GPL|LGPL)|GNU (Affero |Lesser )?General Public", re.IGNORECASE)


@dataclass
class Dist:
    """What the check needs to know about one installed package."""

    name: str
    version: str
    declared: str  # License-Expression, else License, else licence classifiers
    classifiers: list[str]
    requires: list[str]
    native_files: list[str]  # vendored shared libraries, not the package's own extensions


@dataclass
class Report:
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)  # name, ver, lic, dec
    problems: list[str] = field(default_factory=list)
    not_installed: list[str] = field(default_factory=list)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_dist(name: str) -> Optional[Dist]:
    """Read one installed package from importlib.metadata; None if not installed."""
    try:
        d = md.distribution(name)
    except md.PackageNotFoundError:
        return None
    meta = d.metadata
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    declared = (meta.get("License-Expression") or (meta.get("License") or "").strip()
                or "; ".join(classifiers))
    native = [str(f) for f in (d.files or [])
              if str(f).endswith(NATIVE_SUFFIXES) and not EXTENSION_MODULE.search(str(f))]
    return Dist(meta["Name"], d.version, declared.splitlines()[0] if declared else "",
                classifiers, list(d.requires or []), native)


def declared_roots(pyproject: Path = PYPROJECT) -> tuple[list[str], list[str]]:
    """Requirement strings for the shipped roots and the internal (Mac-only) roots."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        sys.exit("license_check needs Python >= 3.11 (tomllib)")
    project = tomllib.loads(pyproject.read_text())["project"]
    extras = project.get("optional-dependencies", {})
    shipped = list(project.get("dependencies", []))
    for e in SHIPPED_EXTRAS:
        shipped += extras.get(e, [])
    internal = [r for e in INTERNAL_EXTRAS for r in extras.get(e, [])]
    return shipped, internal


def _requirement(spec: str):
    try:
        from packaging.requirements import Requirement
    except ImportError:  # pragma: no cover
        sys.exit("license_check needs `packaging` (pip install -e '.[dev]')")
    return Requirement(spec)


def walk(roots: list[str], lookup: Callable[[str], Optional[Dist]], report: Report) -> dict:
    """Installed closure of ``roots`` (extra-gated requirements skipped), keyed by name."""
    seen: dict[str, Dist] = {}
    queue = list(roots)
    while queue:
        req = _requirement(queue.pop())
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        key = canonical(req.name)
        if key in seen or key in report.not_installed:
            continue
        dist = lookup(req.name)
        if dist is None:
            report.not_installed.append(key)
            continue
        seen[key] = dist
        queue.extend(dist.requires)
    return seen


def check(
    lookup: Callable[[str], Optional[Dist]] = installed_dist,
    table: Optional[dict] = None,
    roots: Optional[tuple[list[str], list[str]]] = None,
    probes: Optional[dict[str, Callable[[], Optional[str]]]] = None,
    shipped: bool = False,
) -> Report:
    table = table if table is not None else yaml.safe_load(TABLE.read_text())
    shipped_roots, internal_roots = roots if roots is not None else declared_roots()
    probes = probes if probes is not None else PROBES
    allowed = set(table["allowed_licenses"])
    entries = {canonical(k): v for k, v in table["packages"].items()}
    report = Report()

    for spec in shipped_roots + internal_roots:
        if canonical(_requirement(spec).name) not in entries:
            report.problems.append(f"{spec}: declared in pyproject.toml but not in {TABLE.name}")

    for key, dist in sorted(walk(shipped_roots, lookup, report).items()):
        entry = entries.get(key)
        decision = entry["decision"] if entry else "UNLISTED"
        report.rows.append((dist.name, dist.version, dist.declared, decision))
        if entry is None:
            report.problems.append(f"{dist.name}: in the shipped closure but not reviewed")
            continue
        if decision not in ("allowed", "dev_only"):
            report.problems.append(f"{dist.name}: decision {decision!r} may not ship")
        if COPYLEFT.search(" ".join([dist.declared, *dist.classifiers])):
            report.problems.append(f"{dist.name}: copyleft licence ({dist.declared!r})")
        if dist.declared != entry["declared"]:
            report.problems.append(
                f"{dist.name}: declares {dist.declared!r}, table reviewed "
                f"{entry['declared']!r} — re-review"
            )
        off = sorted(set(entry["licenses"]) - allowed)
        if off:
            report.problems.append(f"{dist.name}: licences {off} not in allowed_licenses")
        patterns = entry.get("bundled") or []
        for f in dist.native_files:
            if not any(fnmatch.fnmatch(Path(f).name, p) for p in patterns):
                report.problems.append(f"{dist.name}: unreviewed bundled library {f}")
        if shipped and decision == "dev_only":
            probe = probes.get(entry.get("probe", ""))
            failure = probe() if probe else f"no probe {entry.get('probe')!r}"
            if failure:
                report.problems.append(f"{dist.name}: dev_only and {failure}")
    return report


def _cv2_without_ffmpeg() -> Optional[str]:
    """None if the installed OpenCV has no FFmpeg; otherwise why it fails (OQ-36)."""
    import cv2

    lines = [ln for ln in cv2.getBuildInformation().splitlines() if "FFMPEG:" in ln]
    if any(ln.split(":", 1)[1].strip().upper().startswith("YES") for ln in lines):
        return f"OpenCV {cv2.__version__} is built with FFmpeg (needs a GPL-free build, OQ-36)"
    return None


PROBES: dict[str, Callable[[], Optional[str]]] = {"cv2_without_ffmpeg": _cv2_without_ffmpeg}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Licence check (SPEC §20).")
    parser.add_argument("--shipped", action="store_true",
                        help="also run dev_only probes (for a Pi / backend image)")
    args = parser.parse_args(argv)
    report = check(shipped=args.shipped)
    for name, version, declared, decision in report.rows:
        print(f"{name:34} {version:12} {decision:14} {declared}")
    for key in report.not_installed:
        report.problems.append(f"{key}: declared but not installed — run `make install-color`")
    sys.stdout.flush()
    for problem in report.problems:
        print(f"FAIL  {problem}", file=sys.stderr)
    print(f"{len(report.rows)} packages checked, {len(report.problems)} problem(s)")
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
