"""Guards against a repeat of the collection failure this module is named
after: tests/test_bhashini_client.py and tests/test_ollama_client.py used to
raise `ModuleNotFoundError: No module named 'tests._fake_http'` at
collection time (a `tests` package installed elsewhere on sys.path shadowed
this repo's own tests/ directory -- see tests/__init__.py and the
`pythonpath` setting in pyproject.toml for the fix). That failure mode --
"71 items / 2 errors / 1 skipped" -- is easy to miss in a long test run if
you only glance at the pass count. This file makes it impossible to miss:

1. Every tests/test_*.py file (other than test_vendor_import.py, which has
   its own documented three-way behaviour -- see that file) must be
   importable, on its own, right now.
2. A fresh `pytest --collect-only` run must report zero collection errors.
3. The suite must collect more than a floor number of tests, so a future
   change that silently drops most or all of a file's tests from
   collection still shows up as a visible failure here, instead of the
   suite quietly reporting fewer tests as if nothing happened.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: tests/test_vendor_import.py deliberately behaves differently depending on
#: whether pocketinfer is installed and PURIYUDHA_ALLOW_MISSING_VENDOR (see
#: that file): it can fail one test, skip the whole module, or run five
#: tests. All three are intended, documented behaviour, not a silent
#: collection failure, so it is exempt from the "plain importable" check
#: below (a module-level `pytest.skip(allow_module_level=True)` raises a
#: real exception on import, by design, that this file should not treat as
#: a regression).
VENDOR_IMPORT_FILE = "test_vendor_import.py"

#: The lowest number of collected tests we ever expect to see.
#:
#: This test always runs a fresh `pytest` via `sys.executable` -- i.e.
#: whichever interpreter is currently running the outer pytest session --
#: so it is self-consistent as long as that interpreter has puriyudha's
#: own declared dependencies installed (the documented, supported setup:
#: `scripts/setup_dev.sh`, or `pip install -e ".[dev]"` directly -- see
#: docs/dev_environment.md). On such an interpreter, with pocketinfer also
#: installed, this repo currently collects 403 tests. Without pocketinfer,
#: tests/test_vendor_import.py contributes one FAILING test instead of its
#: usual five by default (the run then exits non-zero, but collection
#: itself still succeeds), or zero items with the documented
#: PURIYUDHA_ALLOW_MISSING_VENDOR=1 opt-out -- either way, a handful fewer
#: than 403, not a cliff.
#:
#: An interpreter that is missing one of puriyudha's OTHER declared
#: dependencies (e.g. a bare system Python nothing has ever run `pip
#: install -e .` against) is a different, out-of-scope failure mode: a
#: loud, named `ModuleNotFoundError` collection ERROR for whichever test
#: file needs that package -- not a silent count drop, so it cannot be
#: confused with the regression this floor exists to catch, and is not
#: accounted for in the number below.
#:
#: Set with generous headroom below every supported-environment count
#: above so normal differences never trip this, while a real
#: silent-collection-drop regression (a file losing most or all of its
#: tests) still will.
MINIMUM_EXPECTED_TEST_COUNT = 100


def _test_files() -> list:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _importable_test_files() -> list:
    return [p for p in _test_files() if p.name != VENDOR_IMPORT_FILE]


@pytest.mark.parametrize("path", _importable_test_files(), ids=lambda p: p.name)
def test_test_file_is_importable(path: Path):
    """Directly imports the module and lets any exception fail this test by
    name -- this is the failure mode a `ModuleNotFoundError` at collection
    time produces, and pytest's own collection-error reporting for it is
    easy to miss in a long run. Parametrized per file so a regression names
    the exact broken file instead of a generic "collection failed"."""
    importlib.import_module(f"tests.{path.stem}")


def test_full_suite_collection_has_zero_errors():
    """Runs a fresh, isolated `pytest --collect-only` and asserts it exits
    0 -- the exact signature of the bug this file guards against was a
    non-zero exit ("Interrupted: 2 errors during collection", exit code 2).

    Checked via the process exit code, not by grepping the output for the
    word "error": several passing test *names* in this suite legitimately
    contain "error" (e.g. test_..._response_error), which would make a
    naive substring check false-positive on a perfectly healthy run.

    A subprocess (rather than pytest.main() in-process) is used deliberately
    so this is a genuinely independent collection pass, not one contaminated
    by whatever plugins/sys.modules state the *outer* run already has
    loaded -- which is exactly the kind of state a real regression could
    hide behind.
    """
    result = _run_collect_only()
    assert result.returncode == 0, (
        f"pytest --collect-only exited {result.returncode} (expected 0), "
        f"indicating collection error(s):\n{result.stdout}{result.stderr}"
    )


def test_collected_test_count_is_above_floor():
    result = _run_collect_only()
    output = result.stdout + result.stderr
    count = _parse_collected_count(output)
    assert count is not None, f"could not parse collected test count from pytest output:\n{output}"
    assert count > MINIMUM_EXPECTED_TEST_COUNT, (
        f"only {count} tests collected, expected more than {MINIMUM_EXPECTED_TEST_COUNT} "
        f"-- a file may be silently failing to contribute its tests again:\n{output}"
    )


def _run_collect_only() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=TESTS_DIR.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _parse_collected_count(output: str):
    """Extracts the leading integer from pytest's `--collect-only -q`
    summary line, e.g. "110 tests collected in 0.79s" or
    "1 test collected in 0.01s". Returns None if no such line is found."""
    for line in output.splitlines():
        line = line.strip()
        if "collected" in line and "error" not in line:
            head = line.split()[0]
            if head.isdigit():
                return int(head)
    return None
