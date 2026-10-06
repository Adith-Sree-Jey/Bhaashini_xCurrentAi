"""Typed client for the Ollama HTTP API (CLAUDE.md: "LLM runtime is
Ollama, not llama.cpp").

The one method the rest of puriyudha should use is
:meth:`OllamaClient.generate_json`: it asks Ollama for JSON-constrained
output, validates the result against a caller-supplied schema, and retries
once before raising. Per CLAUDE.md invariant #2, this client's output is
never trusted as a source of medical fact on its own -- callers
(:mod:`puriyudha.extract`) validate it against the offline formulary before
it goes anywhere near a MedicationOrder.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

from puriyudha.clients.deadline import Deadline

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
#: CLAUDE.md: "Available models: qwen3-vl:2b (vision), ministral-3:3B (text)."
DEFAULT_TEXT_MODEL = "ministral-3:3B"
DEFAULT_VISION_MODEL = "qwen3-vl:2b"
#: Renamed from the old DEFAULT_TIMEOUT_SECONDS (finding H2, part 3.4) to
#: make the semantics unambiguous: this is the ceiling for ONE generation
#: call, not the whole patient session -- see
#: puriyudha.clients.deadline.DEFAULT_SESSION_BUDGET_SECONDS for that.
#: Local LLM generation, even for a small model on a laptop, can take a
#: while -- much longer than the Bhashini service's per-attempt timeout.
#: Model inference is the single slowest stage in a patient session, so
#: when a shared Deadline is passed to generate_json(), it gets whatever
#: budget is left rather than this full ceiling -- see generate_json().
DEFAULT_PER_ATTEMPT_TIMEOUT_SECONDS = 60.0


class OllamaError(Exception):
    """Base class for every error this client raises."""


class OllamaConnectionError(OllamaError):
    """Could not reach the Ollama service."""


class OllamaHTTPError(OllamaError):
    """Ollama responded with a non-2xx status code."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Ollama returned HTTP {status_code}: {body!r}")


class OllamaInvalidJSONError(OllamaError):
    """The model's `response` text did not parse as JSON, even after one
    retry."""


class OllamaSchemaValidationError(OllamaError):
    """The model's JSON output parsed, but did not match the requested
    schema, even after one retry."""


class OllamaDeadlineExceeded(OllamaError):
    """The shared session Deadline (see puriyudha.clients.deadline) was
    already exhausted before this call could even attempt a generation.

    Deliberately distinct from OllamaConnectionError / OllamaHTTPError, for
    the same reason as BhashiniDeadlineExceeded
    (puriyudha.clients.bhashini): this means the *session* ran out of time,
    not that Ollama itself failed to respond -- model inference is the
    single slowest stage in a patient session, so this is the exception a
    caller is most likely to actually need to handle by degrading
    gracefully (e.g. falling back to the rules-only pipeline, CLAUDE.md's
    "Language-model assistance is an optional enhancement") rather than
    treating as a generic transport failure."""


# --- a small, hand-rolled JSON Schema subset validator -----------------------
#
# Deliberately not the `jsonschema` package: CLAUDE.md asks for minimal,
# aarch64-friendly dependencies and no heavyweight frameworks, and the
# schemas puriyudha needs here (flat-ish structured-extraction records)
# only need a small slice of JSON Schema. Supports `type` (object / string /
# number / integer / boolean / array / null), `properties`, `required`,
# `enum`, `items`, and `additionalProperties: false`. Raises
# OllamaSchemaValidationError naming the first mismatch found (does not
# collect every violation) -- good enough to decide "retry or not", which
# is all generate_json needs it for.

_TYPE_MAP: Dict[str, Any] = {
    "object": dict,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "null": type(None),
}


def _validate_schema(value: Any, schema: dict, path: str = "$") -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise OllamaSchemaValidationError(f"{path}: {value!r} not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        py_type = _TYPE_MAP.get(expected_type)
        if py_type is None:
            raise OllamaSchemaValidationError(f"{path}: unsupported schema type {expected_type!r}")
        # bool is a subclass of int in Python; only accept it where the
        # schema explicitly asked for a boolean.
        if expected_type != "boolean" and isinstance(value, bool):
            raise OllamaSchemaValidationError(f"{path}: expected {expected_type}, got bool")
        if not isinstance(value, py_type):
            raise OllamaSchemaValidationError(
                f"{path}: expected {expected_type}, got {type(value).__name__}"
            )

    if expected_type == "object" and isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise OllamaSchemaValidationError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise OllamaSchemaValidationError(f"{path}: unexpected propert{'y' if len(extra) == 1 else 'ies'} {extra}")
        for key, subschema in properties.items():
            if key in value:
                _validate_schema(value[key], subschema, f"{path}.{key}")

    if expected_type == "array" and isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema is not None:
            for i, item in enumerate(value):
                _validate_schema(item, items_schema, f"{path}[{i}]")


# --- tolerant JSON extraction for real 3B-model output -----------------------
#
# A small local model asked for JSON-constrained output still, in practice,
# sometimes wraps it in a ```json fenced code block, prefixes it with a
# prose preamble ("Sure! Here is the extracted record: {...}"), or -- more
# rarely -- emits two JSON objects concatenated with no separator. All three
# are repaired the same way: find the first `{` and scan forward tracking
# brace depth (respecting string literals and escapes, so a `}` or `{`
# inside a quoted string never miscounts) until depth returns to zero, and
# parse only that balanced substring. This is deliberately NOT a regex --
# CLAUDE.md asks for the LLM's output to never be trusted uncritically, and
# a "strip anything brace-shaped" regex would happily mangle a JSON value
# that legitimately contains `{`/`}` characters inside a string. A genuinely
# malformed response (a trailing comma, a truncated mid-string generation
# that hit the model's token limit) still fails to parse even after
# extraction, and falls through to the existing invalid-JSON handling
# unchanged.

def _extract_first_balanced_json_object(text: str) -> Optional[str]:
    """Returns the first balanced `{...}` substring of `text` (brace-depth
    tracking, string-literal aware), or None if `text` contains no
    complete, balanced JSON object."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None  # never balanced -- e.g. truncated mid-generation


def _parse_model_json(raw_text: str) -> Tuple[Optional[Any], bool]:
    """Parses `raw_text` as JSON. Returns `(value, was_repaired)`.

    Tries a direct `json.loads` first (the common case, no repair needed).
    If that fails, extracts the first balanced JSON object (see
    `_extract_first_balanced_json_object`) and tries again -- this is the
    one repair this client performs, and it covers a fenced code block, a
    prose preamble, and multiple concatenated objects identically, since
    the balanced scan always stops at the end of the *first* complete
    object regardless of what precedes or follows it.

    Returns `(None, False)` if neither attempt produces valid JSON (a
    trailing comma or a mid-string truncation both still fail to parse
    even from the extracted substring) -- the caller treats that as a
    genuinely malformed response, not a repair.
    """
    try:
        return json.loads(raw_text), False
    except json.JSONDecodeError:
        pass

    extracted = _extract_first_balanced_json_object(raw_text)
    if extracted is None:
        return None, False
    try:
        return json.loads(extracted), True
    except json.JSONDecodeError:
        return None, False


class OllamaClient:
    """Talks to a local Ollama daemon's HTTP API
    (``http://localhost:11434`` by default)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        text_model: str = DEFAULT_TEXT_MODEL,
        vision_model: str = DEFAULT_VISION_MODEL,
        timeout: float = DEFAULT_PER_ATTEMPT_TIMEOUT_SECONDS,
        session=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.text_model = text_model
        self.vision_model = vision_model
        self.timeout = timeout
        self._session = session if session is not None else requests.Session()

    def generate_json(
        self,
        prompt: str,
        schema: dict,
        images: Optional[List[bytes]] = None,
        model: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> dict:
        """Request JSON-constrained output from Ollama, validate it
        against `schema`, and retry once (a fresh generation call, not a
        resend of the same request) if the model's output is invalid JSON
        or does not match `schema`, before raising.

        `model` overrides the default model choice: `self.vision_model` if
        `images` is given (non-empty), else `self.text_model`. `images` is
        a list of raw image bytes (e.g. JPEG, matching
        `board.camera_frame_jpg()`'s output) -- this method base64-encodes
        them for the wire.

        `deadline`, if given, is the shared per-session time budget (see
        puriyudha.clients.deadline.Deadline) -- the SAME Deadline instance
        a caller also passes to BhashiniClient methods in the same session.
        Each generation attempt's own timeout is capped to however much of
        it remains, and `OllamaDeadlineExceeded` is raised instead of
        attempting a generation once it is used up (checked before *each*
        of the two attempts, so an exhausted deadline after attempt 1 skips
        the retry rather than attempting a generation call with no time
        left). Model inference is the single slowest stage in a session, so
        this is the deadline check most likely to actually fire in
        practice.

        Raises `OllamaInvalidJSONError` or `OllamaSchemaValidationError` if
        both attempts fail; `OllamaConnectionError` / `OllamaHTTPError` for
        transport-level failures (not retried -- those indicate Ollama
        itself is unreachable or misconfigured, not a one-off bad
        generation, so retrying silently would just hide the real
        problem).
        """
        chosen_model = model or (self.vision_model if images else self.text_model)
        payload: Dict[str, Any] = {
            "model": chosen_model,
            "prompt": prompt,
            "format": schema,
            "stream": False,
        }
        if images:
            payload["images"] = [base64.b64encode(img).decode("ascii") for img in images]

        last_error: Optional[OllamaError] = None
        for attempt in range(1, 3):  # one initial attempt + one retry
            if deadline is not None and deadline.exceeded():
                raise OllamaDeadlineExceeded(
                    f"session deadline exceeded before generate_json attempt {attempt}/2 "
                    f"(model={chosen_model!r})"
                )
            data = self._post_generate(payload, deadline=deadline)
            raw_text = data.get("response", "")
            parsed, repaired = _parse_model_json(raw_text)
            if parsed is None:
                last_error = OllamaInvalidJSONError(
                    f"attempt {attempt}/2: model response was not valid JSON, even after "
                    f"attempting to extract a balanced JSON object: {raw_text!r}"
                )
                continue
            if repaired:
                # A measurable signal about prompt quality (CLAUDE.md: track
                # this rate over time), not silently swallowed.
                logger.warning(
                    "OllamaClient.generate_json: model response for model=%r was not "
                    "directly valid JSON; repaired by extracting the first balanced "
                    "JSON object (handles a fenced ```json block, a prose preamble, "
                    "or concatenated objects). Raw response: %r",
                    chosen_model,
                    raw_text,
                )
            try:
                _validate_schema(parsed, schema)
            except OllamaSchemaValidationError as exc:
                last_error = exc
                continue
            return parsed

        assert last_error is not None
        raise last_error

    def _post_generate(self, payload: dict, deadline: Optional[Deadline] = None) -> dict:
        url = f"{self.base_url}/api/generate"
        request_timeout = self.timeout if deadline is None else deadline.timeout_for(self.timeout)
        try:
            resp = self._session.post(url, json=payload, timeout=request_timeout)
        except requests.exceptions.RequestException as exc:
            raise OllamaConnectionError(f"could not reach {url}: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaHTTPError(resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError as exc:
            raise OllamaError(f"non-JSON response from {url}: {resp.text!r}") from exc
