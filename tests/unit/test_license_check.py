"""Licence policy check (SPEC §20): the real table covers pyproject, the real environment
passes, and each failure rule fires on fake packages."""

from __future__ import annotations

import pytest
from host_tools import license_check as lc

TABLE = {
    "allowed_licenses": ["MIT", "BSD-3-Clause"],
    "packages": {
        "good": {"declared": "MIT", "licenses": ["MIT"], "decision": "allowed", "bundled": []},
        "dep": {"declared": "BSD-3-Clause", "licenses": ["BSD-3-Clause"],
                "decision": "allowed", "bundled": ["libfoo*.so"]},
        "cvlike": {"declared": "Apache 2.0", "licenses": ["MIT"], "decision": "dev_only",
                   "bundled": ["*"], "probe": "fake_probe"},
        "rawlike": {"declared": "MIT", "licenses": ["MIT"], "decision": "internal_only",
                    "bundled": ["*"]},
    },
}


def dist(name, declared="MIT", classifiers=(), requires=(), native=()):
    return lc.Dist(name, "1.0", declared, list(classifiers), list(requires), list(native))


def run(dists, shipped_roots, internal_roots=(), shipped=False, probe_result=None):
    lookup = {d.name: d for d in dists}.get
    return lc.check(
        lookup=lookup,
        table=TABLE,
        roots=(list(shipped_roots), list(internal_roots)),
        probes={"fake_probe": lambda: probe_result},
        shipped=shipped,
    )


def test_clean_closure_passes_and_walks_transitive_requirements():
    report = run(
        [dist("good", requires=["dep>=1", 'unlisted-extra; extra == "plot"']),
         dist("dep", declared="BSD-3-Clause", native=["dep.libs/libfoo-1.2.so"])],
        ["good"],
    )
    assert report.problems == []
    assert [r[0] for r in report.rows] == ["dep", "good"]  # extra-gated dep skipped


@pytest.mark.parametrize(
    "dists, roots, message",
    [
        ([dist("mystery")], ["mystery"], "not in licenses.yaml"),
        ([dist("good", requires=["mystery"]), dist("mystery")], ["good"],
         "in the shipped closure but not reviewed"),
        ([dist("good", classifiers=["License :: OSI Approved :: GNU General Public "
                                    "License v2 (GPLv2)"])], ["good"], "copyleft"),
        ([dist("good", declared="LGPL-2.1-or-later")], ["good"], "copyleft"),
        ([dist("good", declared="MIT OR Apache-2.0")], ["good"], "re-review"),
        ([dist("good", requires=["dep"]),
          dist("dep", declared="BSD-3-Clause", native=["dep.libs/libx264.so"])],
         ["good"], "unreviewed bundled library dep.libs/libx264.so"),
        ([dist("good", requires=["rawlike"]), dist("rawlike")], ["good"],
         "'internal_only' may not ship"),
    ],
)
def test_policy_violations_fail(dists, roots, message):
    report = run(dists, roots)
    assert any(message in p for p in report.problems), report.problems


def test_dev_only_passes_in_dev_but_needs_its_probe_when_shipped():
    cv = dist("cvlike", declared="Apache 2.0", native=["cv2/.dylibs/libx264.dylib"])
    assert run([cv], ["cvlike"]).problems == []
    assert run([cv], ["cvlike"], shipped=True, probe_result=None).problems == []
    failed = run([cv], ["cvlike"], shipped=True, probe_result="built with FFmpeg")
    assert failed.problems == ["cvlike: dev_only and built with FFmpeg"]


def test_missing_install_is_reported_not_crashed():
    report = run([], ["good"])
    assert report.not_installed == ["good"] and report.rows == []


def test_own_extension_modules_are_not_vendored_libraries():
    for own in ("yaml/_yaml.cpython-313-darwin.so", "cv2/cv2.abi3.so", "np/_core.cp313-win.pyd"):
        assert lc.EXTENSION_MODULE.search(own), own
    for vendored in ("cv2/.dylibs/libx264.164.dylib", "numpy.libs/libscipy_openblas64_.so"):
        assert not lc.EXTENSION_MODULE.search(vendored), vendored


def test_every_declared_dependency_is_in_the_real_table():
    shipped, internal = lc.declared_roots()
    report = lc.check(lookup=lambda name: None, roots=(shipped, internal))
    assert [p for p in report.problems if "not in licenses.yaml" in p] == []


def test_real_environment_passes_the_dev_check():
    report = lc.check()
    if report.not_installed:
        pytest.skip(f"[color] extra not installed: {report.not_installed} (make install-color)")
    assert report.problems == []
