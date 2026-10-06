"""Tests for puriyudha.clients.ollama.OllamaClient.

Hermetic: every test uses tests/_fake_http.FakeSession loaded with the
hand-crafted fixtures under tests/fixtures/ollama/ (see that directory's
README for why they're hand-crafted rather than captured). No test starts
a real Ollama daemon or touches the network.
"""
import json
from pathlib import Path

import pytest

from puriyudha.clients.deadline import Deadline
from puriyudha.clients.ollama import (
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    OllamaClient,
    OllamaConnectionError,
    OllamaDeadlineExceeded,
    OllamaHTTPError,
    OllamaInvalidJSONError,
    OllamaSchemaValidationError,
    _extract_first_balanced_json_object,
    _parse_model_json,
    _validate_schema,
)
from tests._fake_http import FakeSession, connection_error


class FakeClock:
    """A controllable monotonic-clock stand-in for Deadline -- see
    tests/test_bhashini_client.py's identical helper. Duplicated rather
    than shared: it is a few lines, and keeping it local avoids inventing a
    cross-file test-utility module for one four-line class."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ollama"

TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "drug_generic": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["drug_generic"],
}

VISION_SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# --- happy path --------------------------------------------------------------

def test_generate_json_text_model_success():
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session)

    result = client.generate_json("extract the drug name", TEXT_SCHEMA)

    assert result == {"drug_generic": "paracetamol", "confidence": 0.92}
    sent_payload = session.requests[-1]["json"]
    assert sent_payload["model"] == DEFAULT_TEXT_MODEL
    assert sent_payload["format"] == TEXT_SCHEMA
    assert sent_payload["stream"] is False
    assert "images" not in sent_payload


def test_generate_json_defaults_to_vision_model_when_images_given():
    session = FakeSession({"generate": [(200, load_fixture("generate_vision_valid.json"))]})
    client = OllamaClient(session=session)

    result = client.generate_json("describe the image", VISION_SCHEMA, images=[b"\xff\xd8fakejpeg"])

    assert result == {"caption": "a blister strip of white tablets"}
    sent_payload = session.requests[-1]["json"]
    assert sent_payload["model"] == DEFAULT_VISION_MODEL
    assert sent_payload["images"] == ["/9hmYWtlanBlZw=="]  # base64 of b"\xff\xd8fakejpeg"


def test_generate_json_explicit_model_overrides_default():
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session)
    client.generate_json("prompt", TEXT_SCHEMA, model="ministral-3:8B")
    assert session.requests[-1]["json"]["model"] == "ministral-3:8B"


# --- retry-once-on-invalid-JSON ----------------------------------------------

def test_retries_once_after_invalid_json_then_succeeds():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_invalid_json.json")),
                (200, load_fixture("generate_text_valid.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result == {"drug_generic": "paracetamol", "confidence": 0.92}
    assert len(session.requests) == 2


def test_raises_after_invalid_json_on_both_attempts():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_invalid_json.json")),
                (200, load_fixture("generate_invalid_json.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    with pytest.raises(OllamaInvalidJSONError):
        client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert len(session.requests) == 2  # exactly one retry, not more


# --- retry-once-on-schema-mismatch -------------------------------------------

def test_retries_once_after_schema_mismatch_then_succeeds():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_schema_mismatch.json")),
                (200, load_fixture("generate_text_valid.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result["drug_generic"] == "paracetamol"
    assert len(session.requests) == 2


def test_raises_after_schema_mismatch_on_both_attempts():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_schema_mismatch.json")),
                (200, load_fixture("generate_schema_mismatch.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    with pytest.raises(OllamaSchemaValidationError, match="drug_generic"):
        client.generate_json("extract the drug name", TEXT_SCHEMA)


# --- tolerant JSON extraction for real 3B-model failure modes (finding H1) --

def test_generate_json_repairs_fenced_code_block():
    session = FakeSession({"generate": [(200, load_fixture("generate_fenced_json_block.json"))]})
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result == {"drug_generic": "paracetamol", "confidence": 0.88}
    assert len(session.requests) == 1  # repaired on the first attempt, no retry needed


def test_generate_json_repairs_prose_preamble():
    session = FakeSession({"generate": [(200, load_fixture("generate_json_with_prose_preamble.json"))]})
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result == {"drug_generic": "paracetamol", "confidence": 0.81}
    assert len(session.requests) == 1


def test_generate_json_repairs_concatenated_objects_by_taking_the_first():
    session = FakeSession({"generate": [(200, load_fixture("generate_concatenated_json_objects.json"))]})
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result == {"drug_generic": "paracetamol", "confidence": 0.77}
    assert len(session.requests) == 1


def test_generate_json_logs_warning_when_repair_happens(caplog):
    session = FakeSession({"generate": [(200, load_fixture("generate_fenced_json_block.json"))]})
    client = OllamaClient(session=session)
    with caplog.at_level("WARNING", logger="puriyudha.clients.ollama"):
        client.generate_json("extract the drug name", TEXT_SCHEMA)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "repaired" in warnings[0].message.lower()


def test_generate_json_does_not_log_warning_on_clean_response(caplog):
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session)
    with caplog.at_level("WARNING", logger="puriyudha.clients.ollama"):
        client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_generate_json_trailing_comma_is_genuinely_malformed_not_repaired():
    """Extraction finds a balanced `{...}` substring here, but it still
    isn't valid JSON (the trailing comma), so this must still raise rather
    than silently claiming a repair."""
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_json_trailing_comma.json")),
                (200, load_fixture("generate_json_trailing_comma.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    with pytest.raises(OllamaInvalidJSONError):
        client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert len(session.requests) == 2  # exactly one retry, same as any other invalid JSON


def test_generate_json_truncated_mid_string_is_genuinely_malformed_not_repaired():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_json_truncated_mid_string.json")),
                (200, load_fixture("generate_json_truncated_mid_string.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    with pytest.raises(OllamaInvalidJSONError):
        client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert len(session.requests) == 2


def test_generate_json_recovers_after_a_repairable_failure_then_a_clean_response():
    session = FakeSession(
        {
            "generate": [
                (200, load_fixture("generate_json_with_prose_preamble.json")),
                (200, load_fixture("generate_text_valid.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    result = client.generate_json("extract the drug name", TEXT_SCHEMA)
    assert result == {"drug_generic": "paracetamol", "confidence": 0.81}
    assert len(session.requests) == 1  # the first attempt was already repairable, no retry


# --- _extract_first_balanced_json_object / _parse_model_json, directly ------

def test_extract_first_balanced_json_object_skips_preamble():
    assert _extract_first_balanced_json_object('noise {"a": 1} trailing') == '{"a": 1}'


def test_extract_first_balanced_json_object_stops_after_first_object():
    assert _extract_first_balanced_json_object('{"a": 1}{"b": 2}') == '{"a": 1}'


def test_extract_first_balanced_json_object_ignores_braces_inside_strings():
    text = '{"a": "contains a } brace", "b": 2}'
    assert _extract_first_balanced_json_object(text) == text


def test_extract_first_balanced_json_object_handles_nested_objects():
    text = '{"a": {"nested": 1}, "b": 2}'
    assert _extract_first_balanced_json_object(text) == text


def test_extract_first_balanced_json_object_returns_none_when_never_balanced():
    assert _extract_first_balanced_json_object('{"a": "unterminated') is None


def test_extract_first_balanced_json_object_returns_none_with_no_brace_at_all():
    assert _extract_first_balanced_json_object("no json here") is None


def test_parse_model_json_direct_hit_is_not_marked_repaired():
    value, repaired = _parse_model_json('{"a": 1}')
    assert value == {"a": 1}
    assert repaired is False


def test_parse_model_json_extraction_hit_is_marked_repaired():
    value, repaired = _parse_model_json('prose then {"a": 1} more prose')
    assert value == {"a": 1}
    assert repaired is True


def test_parse_model_json_genuinely_malformed_returns_none_not_repaired():
    value, repaired = _parse_model_json('{"a": 1,}')
    assert value is None
    assert repaired is False


# --- Deadline (finding H2, part 3.4) -----------------------------------------

def test_no_deadline_behaves_as_before():
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session, timeout=60.0)
    client.generate_json("prompt", TEXT_SCHEMA)
    assert session.requests[-1]["timeout"] == 60.0


def test_exhausted_deadline_raises_before_attempting_a_generation():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    clock.advance(1.5)  # already past budget, zero real sleep
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session)
    with pytest.raises(OllamaDeadlineExceeded):
        client.generate_json("prompt", TEXT_SCHEMA, deadline=deadline)
    assert session.requests == []  # never even attempted


def test_deadline_caps_request_timeout_to_remaining_budget():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=5.0, clock=clock)
    clock.advance(4.7)  # 0.3s left
    session = FakeSession({"generate": [(200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session, timeout=60.0)  # per-call ceiling is much larger
    client.generate_json("prompt", TEXT_SCHEMA, deadline=deadline)
    assert session.requests[-1]["timeout"] == pytest.approx(0.3)


def test_deadline_exceeded_between_attempts_stops_the_retry():
    """Model inference is the slowest stage (module docstring) -- this
    simulates the budget running out during the first (invalid-JSON)
    attempt's simulated generation time, so the retry attempt never fires,
    and OllamaDeadlineExceeded is raised instead of a generic
    OllamaInvalidJSONError."""
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)

    class AdvancingSession(FakeSession):
        def post(self, url, json=None, timeout=None):
            clock.advance(1.5)
            return super().post(url, json=json, timeout=timeout)

    session = AdvancingSession(
        {
            "generate": [
                (200, load_fixture("generate_invalid_json.json")),
                (200, load_fixture("generate_text_valid.json")),
            ]
        }
    )
    client = OllamaClient(session=session)
    with pytest.raises(OllamaDeadlineExceeded):
        client.generate_json("prompt", TEXT_SCHEMA, deadline=deadline)
    assert len(session.requests) == 1  # first attempt happened, the retry did not


# --- transport-level errors are not retried ----------------------------------

def test_http_error_is_not_retried():
    session = FakeSession({"generate": [(500, {"error": "model not loaded"})]})
    client = OllamaClient(session=session)
    with pytest.raises(OllamaHTTPError) as exc_info:
        client.generate_json("prompt", TEXT_SCHEMA)
    assert exc_info.value.status_code == 500
    assert len(session.requests) == 1


def test_connection_error_is_not_retried():
    session = FakeSession({"generate": [connection_error(), (200, load_fixture("generate_text_valid.json"))]})
    client = OllamaClient(session=session)
    with pytest.raises(OllamaConnectionError):
        client.generate_json("prompt", TEXT_SCHEMA)
    assert len(session.requests) == 1


# --- the hand-rolled schema validator, directly ------------------------------

def test_validate_schema_accepts_matching_value():
    _validate_schema({"drug_generic": "x", "confidence": 0.5}, TEXT_SCHEMA)  # no raise


def test_validate_schema_rejects_missing_required_field():
    with pytest.raises(OllamaSchemaValidationError, match="drug_generic"):
        _validate_schema({"confidence": 0.5}, TEXT_SCHEMA)


def test_validate_schema_rejects_wrong_type():
    with pytest.raises(OllamaSchemaValidationError, match="expected string"):
        _validate_schema({"drug_generic": 123}, TEXT_SCHEMA)


def test_validate_schema_bool_is_not_accepted_as_number():
    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    with pytest.raises(OllamaSchemaValidationError, match="got bool"):
        _validate_schema({"n": True}, schema)


def test_validate_schema_enum():
    schema = {"type": "string", "enum": ["morning", "night"]}
    _validate_schema("morning", schema)
    with pytest.raises(OllamaSchemaValidationError, match="enum"):
        _validate_schema("afternoon", schema)


def test_validate_schema_additional_properties_false():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    _validate_schema({"a": "x"}, schema)
    with pytest.raises(OllamaSchemaValidationError, match="unexpected"):
        _validate_schema({"a": "x", "b": "y"}, schema)


def test_validate_schema_nested_array_of_objects():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"slot": {"type": "string", "enum": ["morning", "night"]}},
            "required": ["slot"],
        },
    }
    _validate_schema([{"slot": "morning"}, {"slot": "night"}], schema)
    with pytest.raises(OllamaSchemaValidationError, match="enum"):
        _validate_schema([{"slot": "morning"}, {"slot": "afternoon"}], schema)
