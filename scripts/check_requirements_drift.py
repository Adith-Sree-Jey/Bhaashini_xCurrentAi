#!/usr/bin/env python3
"""Warn if the vendored upstream requirements.txt has drifted out from under
our x86/ARM triage.

vendor/suno-sutra-sw/python/requirements.txt is upstream's file (pinned via
git submodule) and is never edited here. constraints/x86-dev-requirements.txt
and constraints/arm_only_packages.txt each carry a manually-triaged subset of
its package names -- the split scripts/setup_dev.sh uses to install
pocketinfer without pulling ARM/CircuitPython-only packages onto an x86
laptop (see docs/dev_environment.md).

This script re-parses the upstream file every run and compares its package
names against the union of both triaged lists. If upstream adds (or renames)
a dependency, it will not appear in either list, and this script prints a
warning naming it -- so the gap gets noticed and triaged deliberately,
instead of pocketinfer silently failing to import at some later, more
confusing point.

Exit code is always 0 (a warning, not a build failure) unless --strict is
passed, in which case an untriaged package causes exit code 1. CI callers
that want this enforced should pass --strict; scripts/setup_dev.sh does not,
so an interactive dev setup is never blocked by it.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_REQUIREMENTS = REPO_ROOT / "vendor" / "suno-sutra-sw" / "python" / "requirements.txt"
TRIAGE_FILES = (
    REPO_ROOT / "constraints" / "x86-dev-requirements.txt",
    REPO_ROOT / "constraints" / "arm_only_packages.txt",
)

# Splits a requirement line's package name off from any version specifier,
# environment marker, or extras. Good enough for this file's simple,
# unpinned-or-==-pinned, no-extras style; it is not a general requirements
# parser.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize(name: str) -> str:
    """PEP 503-style normalisation, so Adafruit-Blinka == adafruit_blinka."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_package_names(path: Path) -> "set[str]":
    names = set()
    if not path.exists():
        return names
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _NAME_RE.match(line)
        if match:
            names.add(normalize(match.group(1)))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any untriaged package is found (default: warn only).",
    )
    args = parser.parse_args()

    if not UPSTREAM_REQUIREMENTS.exists():
        print(
            f"NOTE: {UPSTREAM_REQUIREMENTS} not found -- submodule not "
            "checked out yet? Run: git submodule update --init",
            file=sys.stderr,
        )
        return 0

    upstream = read_package_names(UPSTREAM_REQUIREMENTS)
    triaged = set()
    for f in TRIAGE_FILES:
        triaged |= read_package_names(f)

    untriaged = sorted(upstream - triaged)
    if not untriaged:
        print("check_requirements_drift: OK, no untriaged upstream packages.")
        return 0

    print(
        "WARNING: vendor/suno-sutra-sw/python/requirements.txt lists "
        "package(s) not triaged in constraints/x86-dev-requirements.txt or "
        "constraints/arm_only_packages.txt:",
        file=sys.stderr,
    )
    for name in untriaged:
        print(f"  - {name}", file=sys.stderr)
    print(
        "Add each one to whichever file matches its platform "
        "(constraints/x86-dev-requirements.txt if it installs/imports fine "
        "on x86, constraints/arm_only_packages.txt if it's ARM/CircuitPython"
        "-only), then re-run scripts/setup_dev.sh.",
        file=sys.stderr,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
