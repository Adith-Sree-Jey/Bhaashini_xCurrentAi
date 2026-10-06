"""Part 6.1: this repo already lives under a path containing spaces
(this machine's checkout is under ".../Adith Sree Jey/Downloads/...") --
which is precisely how the tests/__init__.py + tests/_fake_http.py import
collision (Part 1) was first reproduced. Every test in this suite already
runs from inside that path, on every run -- but that is an environmental
fact about this one machine, not a regression test that travels with the
repo. This copies the hermetic subset of the repo needed to run its own
test suite (puriyudha/, services/, tests/, pyproject.toml -- deliberately
NOT vendor/, which is unrelated to path-spacing and would make this slow)
into a FRESH temporary directory whose name also contains a space, and
re-runs pytest there, so a future path-handling regression that only
breaks on spaces (an unquoted shell command, a naive f"{a}/{b}" instead of
Path(a) / b, etc.) shows up as a failure here -- on any machine, not only
ones whose checkout happens to already have a space in it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Just enough of the repo to run its own hermetic test suite standalone.
#: Excludes vendor/ (huge, needs pocketinfer already installed in this
#: interpreter's site-packages regardless of cwd -- not path-space-specific
#: and would make this test slow for no extra coverage), data/, docs/,
#: patches/, scripts/ (not on any test import path).
COPY_ITEMS = ("pyproject.toml", "puriyudha", "services", "tests")

#: THIS_FILE_NAME is excluded from the copy deliberately: this test's own
#: body copies tests/ and spawns `pytest` on the copy. If the copy also
#: contained this file, the nested pytest run would collect and execute
#: this exact test again -- which would copy tests/ *again* and spawn
#: *another* nested pytest, recursing until the process tree explodes
#: (this was reproduced while writing this test: 20+ nested python
#: processes before the recursion was killed by hand). Excluding this one
#: file breaks the cycle while still exercising everything else the
#: suite's path-handling actually depends on.
THIS_FILE_NAME = Path(__file__).name


def _ignore_for_copy(directory: str, names: "list[str]") -> "set[str]":
    ignored = set(shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")(directory, names))
    if THIS_FILE_NAME in names:
        ignored.add(THIS_FILE_NAME)
    return ignored


def test_suite_passes_from_a_path_containing_a_space(tmp_path):
    target = tmp_path / "has a space in the directory name"
    target.mkdir()

    for name in COPY_ITEMS:
        src = REPO_ROOT / name
        dst = target / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=_ignore_for_copy)
        else:
            shutil.copy2(src, dst)

    assert not (target / "tests" / THIS_FILE_NAME).exists(), (
        f"{THIS_FILE_NAME} must not be present in the copy (see module docstring: "
        "prevents unbounded recursive self-invocation)"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"pytest exited {result.returncode} when run from a path containing "
        f"a space ({target}):\n{output}"
    )
    assert "passed" in output, f"expected a 'N passed' summary line, got:\n{output}"
