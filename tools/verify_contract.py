#!/usr/bin/env python3
"""Replay a recorded Bhashini contract corpus against any base URL and
report field-level differences.

Built for one specific moment: on 6 Aug 2026, run this against the real
device and find out, in under two minutes, exactly where reality diverges
from what tests/fixtures/contract/ assumed (captured against
services.mock_bhashini, not the real device -- see
tests/fixtures/contract/README.md).

Usage:

    python -m tools.verify_contract --base-url http://<device>:11400

Deliberately talks HTTP directly (not through BhashiniClient): this tool's
job is to check what is actually on the wire, including whatever language
codes were recorded, not to re-apply puriyudha/clients/bhashini.py's own
casing normalisation on top.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import requests

DEFAULT_BASE_URL = "http://localhost:11400"
DEFAULT_FIXTURES_DIR = Path("tests/fixtures/contract")
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Request fields whose exact casing is unverified against the real device
#: (see puriyudha/clients/bhashini.py's _LANGUAGE_CODES) -- always called
#: out in a diff report, even if nothing else differs.
LANGUAGE_FIELDS = ("language", "src_lang", "tgt_lang")


class ContractDiff:
    def __init__(self, record_path: Path, endpoint: str):
        self.record_path = record_path
        self.endpoint = endpoint
        self.messages: list = []

    def add(self, message: str) -> None:
        self.messages.append(message)

    @property
    def ok(self) -> bool:
        return not self.messages


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _compare_shapes(recorded: dict, live: dict, diff: ContractDiff) -> None:
    recorded_keys = set(recorded) if isinstance(recorded, dict) else set()
    live_keys = set(live) if isinstance(live, dict) else set()

    for key in sorted(recorded_keys - live_keys):
        diff.add(f"response missing field {key!r} (present when recorded)")
    for key in sorted(live_keys - recorded_keys):
        diff.add(f"response has new field {key!r} (absent when recorded)")
    for key in sorted(recorded_keys & live_keys):
        rt, lt = _type_name(recorded[key]), _type_name(live[key])
        if rt != lt:
            diff.add(f"response field {key!r} changed type: recorded {rt}, live {lt}")


def _replay_one(base_url: str, record: dict, timeout: float) -> "tuple[Optional[int], object, Optional[str]]":
    """Returns (status_code, json_or_none, error_message)."""
    url = base_url.rstrip("/") + record["path"]
    try:
        if record["method"] == "GET":
            resp = requests.get(url, timeout=timeout)
        else:
            resp = requests.post(url, json=record["request"], timeout=timeout)
    except requests.exceptions.RequestException as exc:
        return None, None, f"could not reach {url}: {exc}"
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body, None


def verify(base_url: str, fixtures_dir: Path, timeout: float) -> "tuple[list, int]":
    diffs = []
    total = 0
    endpoint_dirs = sorted(p for p in fixtures_dir.iterdir() if p.is_dir())
    for endpoint_dir in endpoint_dirs:
        endpoint = endpoint_dir.name
        for record_path in sorted(endpoint_dir.glob("*.json")):
            total += 1
            record = json.loads(record_path.read_text())
            diff = ContractDiff(record_path, endpoint)

            status_code, live_body, error = _replay_one(base_url, record, timeout)
            if error is not None:
                diff.add(error)
                diffs.append(diff)
                continue

            if status_code != record["status_code"]:
                diff.add(f"status code changed: recorded {record['status_code']}, live {status_code}")

            if isinstance(record["response"], dict) or isinstance(live_body, dict):
                _compare_shapes(
                    record["response"] if isinstance(record["response"], dict) else {},
                    live_body if isinstance(live_body, dict) else {},
                    diff,
                )

            lang_fields = {
                k: v for k, v in (record.get("request") or {}).items() if k in LANGUAGE_FIELDS
            }
            if lang_fields and not diff.ok:
                diff.add(f"language codes sent (unverified casing): {lang_fields}")

            if not diff.ok:
                diffs.append(diff)

    return diffs, total


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"default: {DEFAULT_BASE_URL}")
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR, help=f"default: {DEFAULT_FIXTURES_DIR}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.fixtures_dir.is_dir():
        print(f"ERROR: fixtures dir not found: {args.fixtures_dir}", file=sys.stderr)
        return 2

    start = time.monotonic()
    diffs, total = verify(args.base_url, args.fixtures_dir, args.timeout)
    elapsed = time.monotonic() - start

    for diff in diffs:
        print(f"[DIFF] {diff.endpoint} <- {diff.record_path.name}")
        for message in diff.messages:
            print(f"        {message}")

    ok_count = total - len(diffs)
    print(f"\n{ok_count}/{total} recorded calls matched {args.base_url} ({elapsed:.1f}s)")
    if diffs:
        print(f"{len(diffs)} call(s) diverged -- see [DIFF] lines above.")
        return 1
    print("No divergence found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
