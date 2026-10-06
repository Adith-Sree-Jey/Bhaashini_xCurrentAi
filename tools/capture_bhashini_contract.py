#!/usr/bin/env python3
"""Exercise BhashiniClient against a real base URL and (optionally) record
every request/response pair as a fixture corpus.

Usage:

    # sanity-check connectivity only, records nothing
    python -m tools.capture_bhashini_contract --base-url http://localhost:18400

    # capture a fixture corpus (used to build tests/fixtures/contract/ and,
    # on 6 Aug, to capture what the *real* device actually does)
    python -m tools.capture_bhashini_contract --base-url http://<device>:11400 --record

Run this against services.mock_bhashini during development, and against
the real device once it exists (CLAUDE.md: not before 5 Aug 2026). Pair
`--record` output with tools/verify_contract.py to check whether a
previously recorded corpus still matches whatever `--base-url` you point
this at.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from io import BytesIO
from pathlib import Path

from puriyudha.clients.bhashini import (
    BhashiniClient,
    BhashiniError,
    DEFAULT_BASE_URL,
)

DEFAULT_OUT_DIR = Path("tests/fixtures/contract")
LANGUAGES = ("en", "hi", "ta")
SAMPLE_PHRASES = {
    "en": "take one tablet after food",
    "hi": "khana ke baad ek goli lijiye",
    "ta": "unavukku pinnal oru maathirai edunga",
}


def _make_sample_wav(seconds: float = 0.5, rate: int = 16000, hz: float = 440.0) -> bytes:
    """A tiny synthetic WAV (a plain tone) to use as ASR input. Contract
    capture only needs *some* valid 16kHz 16-bit mono WAV; it does not need
    real speech."""
    n = int(seconds * rate)
    samples = [int(6000 * math.sin(2 * math.pi * hz * (i / rate))) for i in range(n)]
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
    return buf.getvalue()


def run(base_url: str, record_dir: "Path | None") -> int:
    client = BhashiniClient(base_url=base_url, record_dir=record_dir)
    sample_wav = _make_sample_wav()

    calls = []

    def call(label, fn):
        try:
            result = fn()
            calls.append((label, "OK", result))
        except BhashiniError as exc:
            calls.append((label, "FAILED", str(exc)))

    call("GET /health", client.health)
    for lang in LANGUAGES:
        call(f"POST /asr [{lang}]", lambda lang=lang: client.asr(sample_wav, lang))
    for src in LANGUAGES:
        for tgt in LANGUAGES:
            if src == tgt:
                continue
            call(
                f"POST /nmt [{src}->{tgt}]",
                lambda src=src, tgt=tgt: client.nmt(SAMPLE_PHRASES[src], src, tgt),
            )
    for lang in LANGUAGES:
        call(f"POST /tts [{lang}]", lambda lang=lang: client.tts(SAMPLE_PHRASES[lang], lang))

    failures = 0
    for label, status, detail in calls:
        print(f"[{status:6}] {label}: {detail!r:.80}")
        if status != "OK":
            failures += 1

    print(f"\n{len(calls) - failures}/{len(calls)} calls succeeded against {base_url}")
    if record_dir is not None:
        print(f"recorded to {record_dir / '<endpoint>' / '<timestamp>.json'}")
    return 1 if failures else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"default: {DEFAULT_BASE_URL}")
    parser.add_argument(
        "--record",
        action="store_true",
        help="write every request/response pair under --out-dir (default: off, dry run)",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help=f"default: {DEFAULT_OUT_DIR}")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(args.base_url, args.out_dir if args.record else None)


if __name__ == "__main__":
    sys.exit(main())
