"""Typed client for the on-device Bhashini models service.

Talks to the systemd unit ``bhashini_models.service`` on
``http://localhost:11400`` (see CLAUDE.md): ``POST /asr``, ``POST /nmt``,
``POST /tts``, ``GET /health``. This module is the *only* place in
puriyudha that should know the wire shape of that service -- everything
else calls :class:`BhashiniClient`.

We do not have the Jetson (and so cannot verify this service for real)
until 5 Aug 2026. Point this client at ``services.mock_bhashini`` for dev
work, and at the real device with a one-line ``base_url`` change once it
exists.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

import requests

from puriyudha.clients.deadline import Deadline

DEFAULT_BASE_URL = "http://localhost:11400"

# --- timeout / retry policy (finding H2 from the verification pass) --------
#
# bhashini_models.service is a LOCALHOST systemd unit (CLAUDE.md), not a
# remote API -- this is the crux of why the old policy (5s per attempt x 3
# attempts, linear backoff -- worst case ~15s per call, ~60s for one
# session's four calls) was wrong, not just slow. A 5xx or dropped
# connection from a localhost service means the model is loading or the box
# is out of memory; repeatedly retrying over many seconds does not fix
# either condition, it just makes the device appear frozen to a patient
# standing at the counter (CLAUDE.md's 12-second end-to-end target).

#: Renamed from the old DEFAULT_TIMEOUT_SECONDS to make the semantics
#: unambiguous: this is the ceiling for ONE attempt, not the whole call and
#: certainly not the whole session -- see DEFAULT_SESSION_BUDGET_SECONDS
#: (puriyudha.clients.deadline) for the latter.
DEFAULT_PER_ATTEMPT_TIMEOUT_SECONDS = 5.0

#: Exactly one retry, not two: a second retry on a localhost service just
#: burns budget on a near-certain repeat of the same failure.
MAX_RETRIES = 1

#: A short FIXED backoff (not linear/exponential, and not scaled by attempt
#: number the way the old policy was) -- there is nothing to "back off"
#: from on localhost; this is purely a brief pause to let a transient
#: hiccup (e.g. the model finishing a load) clear before the one retry.
RETRY_BACKOFF_SECONDS = 0.25


class BhashiniError(Exception):
    """Base class for every error this client raises."""


class BhashiniConnectionError(BhashiniError):
    """Could not reach the service at all after all retries."""


class BhashiniHTTPError(BhashiniError):
    """The service responded with a non-2xx status code."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Bhashini service returned HTTP {status_code}: {body!r}")


class BhashiniResponseError(BhashiniError):
    """The service responded 2xx but the body was not JSON, or was missing
    a field this client requires."""


class BhashiniDeadlineExceeded(BhashiniError):
    """The shared session Deadline (see puriyudha.clients.deadline) was
    already exhausted before this call could even attempt a request.

    Deliberately distinct from BhashiniConnectionError / BhashiniHTTPError:
    those mean the service failed to respond; this means the *session* ran
    out of time, which is a different condition a caller should react to
    differently (degrade gracefully -- e.g. skip straight to a cached or
    simplified response -- rather than treat it as a one-off transport
    failure worth a generic error path)."""


# --- language-code normalisation --------------------------------------------
#
# UNVERIFIED AGAINST THE REAL DEVICE (no Jetson until 5 Aug 2026; see
# CLAUDE.md). Derived entirely from the one reference caller in the
# vendored platform,
# vendor/suno-sutra-sw/python/pocketinfer/applications/hear_the_world.py:
# it always lowercases ASR/TTS `language` (`self.settings["input_language"]
# = msg[4:].lower()`, compared against literal `'en'`), but passes the
# literal string "EN" (uppercase) as NMT's src_lang/tgt_lang specifically
# for English, while other languages (hi, ta) go through NMT lowercase.
# CLAUDE.md flags this exact asymmetry as "unverified... normalise at our
# boundary" -- this table is that one place. Confirm it against the real
# service on 5 Aug (see tools/verify_contract.py) and update only here.
#
# canonical (lowercase, what the rest of puriyudha uses) -> (asr/tts code, nmt code)
_LANGUAGE_CODES: dict = {
    "en": ("en", "EN"),
    "hi": ("hi", "hi"),
    "ta": ("ta", "ta"),
}


def _known_languages() -> str:
    return ", ".join(sorted(_LANGUAGE_CODES))


def normalise_asr_tts_language(code: str) -> str:
    """The `language` value ASR/TTS requests should carry, for canonical
    code `code` (e.g. "en", "hi", "ta")."""
    canon = code.strip().lower()
    if canon not in _LANGUAGE_CODES:
        raise ValueError(f"unknown language code {code!r}; known: {_known_languages()}")
    return _LANGUAGE_CODES[canon][0]


def normalise_nmt_language(code: str) -> str:
    """The `src_lang`/`tgt_lang` value NMT requests should carry, for
    canonical code `code`."""
    canon = code.strip().lower()
    if canon not in _LANGUAGE_CODES:
        raise ValueError(f"unknown language code {code!r}; known: {_known_languages()}")
    return _LANGUAGE_CODES[canon][1]


@dataclass
class _Recorder:
    """Writes every (request, response) pair to
    `<record_dir>/<endpoint>/<timestamp>.json`, for later replay by
    tools/verify_contract.py or a test fixture. See BhashiniClient's
    `record_dir` constructor argument."""

    record_dir: Path

    def record(self, method: str, path: str, request_body, status_code: int, response_body) -> None:
        endpoint = path.strip("/") or "health"
        out_dir = self.record_dir / endpoint
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        payload = {
            "method": method,
            "path": path,
            "request": request_body,
            "status_code": status_code,
            "response": response_body,
        }
        (out_dir / f"{timestamp}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))


class BhashiniClient:
    """Talks to a Bhashini-models-compatible service (the real
    ``bhashini_models.service``, or ``services.mock_bhashini`` in dev).

    Constructible against any base URL -- pointing this at the real device
    on 5 Aug is `BhashiniClient(base_url="http://<device>:11400")`, no
    other code changes.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_PER_ATTEMPT_TIMEOUT_SECONDS,
        retries: int = MAX_RETRIES,
        session=None,
        record_dir: Optional[Union[str, Path]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._session = session if session is not None else requests.Session()
        self._recorder = _Recorder(Path(record_dir)) if record_dir is not None else None
        #: Injectable so tests can assert on retry backoff without a real
        #: sleep -- see tests/test_bhashini_client.py's FakeClock.
        self._sleep = sleep

    def health(self, deadline: Optional[Deadline] = None) -> bool:
        """True if the service responded 200 to GET /health, False for any
        connection failure, non-200 status, or exceeded deadline. Never
        raises."""
        try:
            self._request("GET", "/health", None, deadline=deadline)
        except BhashiniError:
            return False
        return True

    def asr(self, wav_bytes: bytes, language: str, deadline: Optional[Deadline] = None) -> str:
        """Transcribe `wav_bytes` (a 16kHz 16-bit mono WAV, matching the
        board's audio recorder) in `language` (canonical code, e.g. "hi").
        Returns the recognised text.

        `deadline`, if given, is the shared per-session time budget (see
        puriyudha.clients.deadline.Deadline) -- this request's own timeout
        is capped to however much of it remains, and BhashiniDeadlineExceeded
        is raised instead of attempting a request once it is used up."""
        payload = {
            "language": normalise_asr_tts_language(language),
            "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
        }
        data = self._request("POST", "/asr", payload, deadline=deadline)
        if "text" not in data:
            raise BhashiniResponseError(f"/asr response missing 'text': {data!r}")
        return data["text"]

    def nmt(
        self, text: str, src_lang: str, tgt_lang: str, deadline: Optional[Deadline] = None
    ) -> str:
        """Translate `text` from `src_lang` to `tgt_lang` (canonical
        codes). Returns the translated text. `deadline`: see `asr()`."""
        payload = {
            "text": text,
            "src_lang": normalise_nmt_language(src_lang),
            "tgt_lang": normalise_nmt_language(tgt_lang),
        }
        data = self._request("POST", "/nmt", payload, deadline=deadline)
        if "translated_text" not in data:
            raise BhashiniResponseError(f"/nmt response missing 'translated_text': {data!r}")
        return data["translated_text"]

    def tts(self, text: str, language: str, deadline: Optional[Deadline] = None) -> bytes:
        """Synthesize `text` in `language` (canonical code). Returns raw
        WAV bytes. `deadline`: see `asr()`."""
        payload = {"text": text, "language": normalise_asr_tts_language(language)}
        data = self._request("POST", "/tts", payload, deadline=deadline)
        if "audio_base64" not in data:
            raise BhashiniResponseError(f"/tts response missing 'audio_base64': {data!r}")
        try:
            return base64.b64decode(data["audio_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise BhashiniResponseError(f"/tts 'audio_base64' did not decode: {exc}") from exc

    # --- transport -------------------------------------------------------

    def _request(
        self, method: str, path: str, json_body, deadline: Optional[Deadline] = None
    ) -> dict:
        url = f"{self.base_url}{path}"
        attempts = self.retries + 1
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            if deadline is not None and deadline.exceeded():
                raise BhashiniDeadlineExceeded(
                    f"session deadline exceeded before attempt {attempt}/{attempts} of {url}"
                )
            request_timeout = self.timeout if deadline is None else deadline.timeout_for(self.timeout)

            try:
                if method == "GET":
                    resp = self._session.get(url, timeout=request_timeout)
                else:
                    resp = self._session.post(url, json=json_body, timeout=request_timeout)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < attempts:
                    self._sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise BhashiniConnectionError(
                    f"could not reach {url} after {attempts} attempt(s): {exc}"
                ) from exc

            # 4xx is never retried (a client-side mistake -- bad payload,
            # unknown language -- retrying an unchanged request just wastes
            # budget on a guaranteed-identical failure). Only a 5xx (the
            # localhost model loading, or the box out of memory) gets the
            # one retry.
            if resp.status_code >= 500 and attempt < attempts:
                last_exc = BhashiniHTTPError(resp.status_code, resp.text)
                self._sleep(RETRY_BACKOFF_SECONDS)
                continue

            if resp.status_code != 200:
                if self._recorder:
                    self._recorder.record(method, path, json_body, resp.status_code, resp.text)
                raise BhashiniHTTPError(resp.status_code, resp.text)

            try:
                data = resp.json()
            except ValueError as exc:
                raise BhashiniResponseError(f"non-JSON response from {path}: {resp.text!r}") from exc

            if self._recorder:
                self._recorder.record(method, path, json_body, resp.status_code, data)
            return data

        # Unreachable in practice (the loop above always returns or
        # raises), but keeps type-checkers and the "no silent fallthrough"
        # rule happy.
        raise BhashiniConnectionError(f"could not reach {url}: {last_exc}")
