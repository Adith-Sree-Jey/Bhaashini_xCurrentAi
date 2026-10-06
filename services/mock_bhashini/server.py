"""HTTP server implementing the upstream Bhashini models contract on port
11400, for local dev only.

Endpoints, exactly as documented in CLAUDE.md ("Bhashini models are NOT a
Python library"):

    POST /asr     {"language": str, "audio_base64": str} -> {"text": str}
    POST /nmt     {"text": str, "src_lang": str, "tgt_lang": str}
                  -> {"translated_text": str}
    POST /tts     {"text": str, "language": str} -> {"audio_base64": str} (WAV)
    GET  /health  -> {"status": "ok"}

CLAUDE.md documents the request shape for every endpoint and the response
shape for /nmt and /tts; the /asr *response* shape is not spelled out
there. This mock's /asr response is `{"text": str}`, matching the one
field the reference app actually reads
(`vendor/suno-sutra-sw/python/pocketinfer/models/asr.py`'s caller does
`asr_result['text']`) -- treat any other field as this mock's own
invention, not a verified part of the contract.

Backed by services.mock_bhashini.fake_backends: no real ASR/NMT/TTS model,
no network, no model download. Quality does not matter here; only
request/response *shape* fidelity does.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from services.mock_bhashini.fake_backends import (
    ASR_POOLS,
    FakeAsrError,
    FakeNmtError,
    FakeTtsError,
    fake_asr_transcribe,
    fake_nmt_translate,
    fake_tts_synthesize,
)

logger = logging.getLogger("mock_bhashini")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11400
#: Which fake_asr_transcribe() phrase pool this server draws from by
#: default. "mixed" (clean + messy combined) is deliberate: the default path
#: through this mock must be the hostile one, matching what real Bhashini
#: ASR output actually looks like (see fake_backends.py's _MESSY_PHRASES).
DEFAULT_ASR_POOL = "mixed"

#: Rough, made-up-but-plausible per-endpoint latency (mean seconds, jitter
#: seconds), only applied when --latency is passed. ASR and TTS involve an
#: actual model pass on real hardware and so are slower than NMT; /health
#: is instant. These numbers are for exercising our own timing/logging
#: code, not a claim about the real device's performance.
LATENCY_PROFILE_SECONDS = {
    "/health": (0.0, 0.0),
    "/asr": (0.8, 0.2),
    "/nmt": (0.15, 0.05),
    "/tts": (0.6, 0.15),
}


class MockBhashiniHandler(BaseHTTPRequestHandler):
    server_version = "MockBhashini/0.1"

    # Set by MockBhashiniServer.__init__ on every request handler instance.
    latency_enabled: bool = False
    latency_scale: float = 1.0
    failure_rate: float = 0.0
    asr_pool: str = DEFAULT_ASR_POOL
    rng: random.Random = random.Random()

    def log_message(self, format_str, *args):  # noqa: A003 - stdlib signature
        logger.info("%s - %s", self.address_string(), format_str % args)

    # --- helpers -------------------------------------------------------

    def _inject_latency(self, path: str) -> None:
        if not self.latency_enabled:
            return
        mean, jitter = LATENCY_PROFILE_SECONDS.get(path, (0.0, 0.0))
        if mean <= 0.0 and jitter <= 0.0:
            return
        delay = max(0.0, self.rng.gauss(mean, jitter)) * self.latency_scale
        time.sleep(delay)

    def _maybe_inject_failure(self) -> bool:
        """Returns True (and has already written a response) if a
        synthetic failure was injected for this request."""
        if self.failure_rate <= 0.0 or self.rng.random() >= self.failure_rate:
            return False
        self._send_json(
            503,
            {"error": "mock_bhashini: injected synthetic failure (--failure-rate)"},
        )
        return True

    def _read_json_body(self) -> Optional[dict]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            self._send_json(400, {"error": "empty request body"})
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"error": "request body must be a JSON object"})
            return None
        return data

    def _require_fields(self, data: dict, fields: "tuple[str, ...]") -> bool:
        missing = [f for f in fields if f not in data]
        if missing:
            self._send_json(400, {"error": f"missing required field(s): {missing}"})
            return False
        return True

    def _send_json(self, status_code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # --- routes ----------------------------------------------------------

    def do_GET(self):  # noqa: N802 - stdlib method name
        if self.path != "/health":
            self._send_json(404, {"error": f"unknown path {self.path!r}"})
            return
        self._inject_latency("/health")
        if self._maybe_inject_failure():
            return
        self._send_json(200, {"status": "ok"})

    def do_POST(self):  # noqa: N802 - stdlib method name
        handlers = {"/asr": self._handle_asr, "/nmt": self._handle_nmt, "/tts": self._handle_tts}
        handler = handlers.get(self.path)
        if handler is None:
            self._send_json(404, {"error": f"unknown path {self.path!r}"})
            return
        self._inject_latency(self.path)
        if self._maybe_inject_failure():
            return
        data = self._read_json_body()
        if data is None:
            return
        handler(data)

    def _handle_asr(self, data: dict) -> None:
        if not self._require_fields(data, ("language", "audio_base64")):
            return
        try:
            wav_bytes = base64.b64decode(data["audio_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            self._send_json(400, {"error": f"invalid audio_base64: {exc}"})
            return
        try:
            text = fake_asr_transcribe(wav_bytes, data["language"], pool=self.asr_pool)
        except FakeAsrError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"text": text})

    def _handle_nmt(self, data: dict) -> None:
        if not self._require_fields(data, ("text", "src_lang", "tgt_lang")):
            return
        try:
            translated = fake_nmt_translate(data["text"], data["src_lang"], data["tgt_lang"])
        except FakeNmtError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"translated_text": translated})

    def _handle_tts(self, data: dict) -> None:
        if not self._require_fields(data, ("text", "language")):
            return
        try:
            wav_bytes = fake_tts_synthesize(data["text"], data["language"])
        except FakeTtsError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        self._send_json(200, {"audio_base64": audio_b64})


class MockBhashiniServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        latency: bool = False,
        latency_scale: float = 1.0,
        failure_rate: float = 0.0,
        asr_pool: str = DEFAULT_ASR_POOL,
        seed: Optional[int] = None,
    ):
        if asr_pool not in ASR_POOLS:
            raise ValueError(f"asr_pool must be one of {ASR_POOLS}, got {asr_pool!r}")
        handler_cls = type(
            "ConfiguredMockBhashiniHandler",
            (MockBhashiniHandler,),
            {
                "latency_enabled": latency,
                "latency_scale": latency_scale,
                "failure_rate": failure_rate,
                "asr_pool": asr_pool,
                "rng": random.Random(seed),
            },
        )
        super().__init__((host, port), handler_cls)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.mock_bhashini",
        description="Dev-only stand-in for the on-device Bhashini models service (see CLAUDE.md).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"default: {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default: {DEFAULT_PORT}")
    parser.add_argument(
        "--latency",
        action="store_true",
        help="inject realistic per-endpoint delays (see LATENCY_PROFILE_SECONDS) so timing/logging code is exercised",
    )
    parser.add_argument(
        "--latency-scale",
        type=float,
        default=1.0,
        help="multiply the built-in latency profile by this factor (default: 1.0)",
    )
    parser.add_argument(
        "--failure-rate",
        type=float,
        default=0.0,
        help="probability in [0, 1] that any given request gets a synthetic 503, to exercise retry/refusal paths (default: 0.0)",
    )
    parser.add_argument(
        "--messy",
        choices=ASR_POOLS,
        default=DEFAULT_ASR_POOL,
        help=(
            "which fake_asr_transcribe() phrase pool /asr draws from: 'clean' "
            "(tidy, grammatical -- rare in the field), 'messy' (no punctuation, "
            "filler, stutters, dropped words, code-switching, truncation, "
            f"silence -- what real ASR output looks like), or 'mixed' (both, "
            f"the default: {DEFAULT_ASR_POOL!r})"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the latency-jitter / failure-injection RNG for reproducible runs",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(name)s %(message)s")

    if not (0.0 <= args.failure_rate <= 1.0):
        logger.error("--failure-rate must be between 0.0 and 1.0, got %s", args.failure_rate)
        return 1

    server = MockBhashiniServer(
        host=args.host,
        port=args.port,
        latency=args.latency,
        latency_scale=args.latency_scale,
        failure_rate=args.failure_rate,
        asr_pool=args.messy,
        seed=args.seed,
    )
    logger.info(
        "mock_bhashini listening on http://%s:%d (latency=%s, failure_rate=%s, messy=%s)",
        args.host,
        args.port,
        args.latency,
        args.failure_rate,
        args.messy,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()
    return 0
