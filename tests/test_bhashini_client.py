"""Tests for puriyudha.clients.bhashini.BhashiniClient.

Hermetic: every test uses tests/_fake_http.FakeSession, either replaying
the real corpus captured in tests/fixtures/contract/ (happy-path
behaviour) or an explicit in-test response queue (retry/error paths the
happy-path corpus doesn't cover). No test starts services.mock_bhashini or
touches the network.
"""
import json
import time
import wave
from io import BytesIO
from pathlib import Path

import pytest

from puriyudha.clients.bhashini import (
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    BhashiniClient,
    BhashiniConnectionError,
    BhashiniDeadlineExceeded,
    BhashiniHTTPError,
    BhashiniResponseError,
    normalise_asr_tts_language,
    normalise_nmt_language,
)
from puriyudha.clients.deadline import Deadline
from tests._fake_http import FakeSession, connection_error

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "contract"


def make_wav() -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x01" * 400)
    return buf.getvalue()


def make_client(**kwargs) -> BhashiniClient:
    """BhashiniClient with a no-op sleep by default. This suite must not
    take real retry-backoff delays (CLAUDE.md: tests are hermetic and this
    repo's tests must stay fast, not just network-free) -- a test that
    specifically wants to measure real elapsed time
    (test_worst_case_elapsed_time_for_fully_failing_call_is_bounded below)
    passes sleep= explicitly to opt back into the real one."""
    kwargs.setdefault("sleep", lambda seconds: None)
    return BhashiniClient(**kwargs)


class FakeClock:
    """A controllable monotonic-clock stand-in for Deadline (see
    puriyudha.clients.deadline.Deadline's `clock` argument), so deadline
    tests can simulate elapsed time deterministically with zero real
    sleeps."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# --- language-code normalisation ------------------------------------------

def test_asr_tts_language_is_always_lowercase_even_for_english():
    assert normalise_asr_tts_language("en") == "en"
    assert normalise_asr_tts_language("EN") == "en"
    assert normalise_asr_tts_language("hi") == "hi"
    assert normalise_asr_tts_language("ta") == "ta"


def test_nmt_language_is_uppercase_only_for_english():
    assert normalise_nmt_language("en") == "EN"
    assert normalise_nmt_language("hi") == "hi"
    assert normalise_nmt_language("ta") == "ta"


def test_unknown_language_code_raises_before_any_request():
    session = FakeSession({})
    client = BhashiniClient(session=session)
    with pytest.raises(ValueError, match="unknown language code"):
        client.asr(make_wav(), "fr")
    assert session.requests == []  # never attempted a request


# --- happy path, replaying the real recorded corpus -----------------------

def test_health_true_on_recorded_corpus():
    session = FakeSession.from_fixture_dir(FIXTURES_DIR)
    client = BhashiniClient(session=session)
    assert client.health() is True


def test_asr_returns_text_from_recorded_response():
    session = FakeSession.from_fixture_dir(FIXTURES_DIR)
    client = BhashiniClient(session=session)
    text = client.asr(make_wav(), "en")
    assert isinstance(text, str) and text


def test_nmt_sends_uppercase_en_and_lowercase_hi_at_the_wire():
    session = FakeSession.from_fixture_dir(FIXTURES_DIR)
    client = BhashiniClient(session=session)
    client.nmt("take one tablet after food", "en", "hi")
    sent = session.requests[-1]["json"]
    assert sent["src_lang"] == "EN"
    assert sent["tgt_lang"] == "hi"


def test_tts_returns_decoded_wav_bytes():
    session = FakeSession.from_fixture_dir(FIXTURES_DIR)
    client = BhashiniClient(session=session)
    wav_bytes = client.tts("hello", "en")
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnframes() > 0


# --- error / retry paths (explicit queues) --------------------------------

def test_health_returns_false_on_connection_failure():
    session = FakeSession({"health": [connection_error(), connection_error(), connection_error()]})
    client = make_client(session=session, retries=2)
    assert client.health() is False


def test_http_4xx_is_not_retried():
    session = FakeSession({"asr": [(400, {"error": "bad request"})]})
    client = make_client(session=session, retries=2)
    with pytest.raises(BhashiniHTTPError) as exc_info:
        client.asr(make_wav(), "en")
    assert exc_info.value.status_code == 400
    assert len(session.requests) == 1  # no retry for a client error


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 429])
def test_no_4xx_status_code_is_ever_retried(status_code):
    session = FakeSession({"asr": [(status_code, {"error": "client error"})]})
    client = make_client(session=session, retries=MAX_RETRIES)
    with pytest.raises(BhashiniHTTPError) as exc_info:
        client.asr(make_wav(), "en")
    assert exc_info.value.status_code == status_code
    assert len(session.requests) == 1


def test_5xx_is_retried_and_can_succeed():
    session = FakeSession(
        {"asr": [(503, {"error": "warming up"}), (200, {"text": "take one tablet"})]}
    )
    client = make_client(session=session, retries=2)
    text = client.asr(make_wav(), "en")
    assert text == "take one tablet"
    assert len(session.requests) == 2


def test_5xx_retries_are_bounded():
    session = FakeSession({"asr": [(503, {"error": "down"}), (503, {"error": "down"})]})
    client = make_client(session=session, retries=1)  # 2 total attempts
    with pytest.raises(BhashiniHTTPError):
        client.asr(make_wav(), "en")
    assert len(session.requests) == 2


def test_default_retries_is_exactly_one():
    """MAX_RETRIES (finding H2, part 3.1) is the default -- exactly one
    retry, not the old default of two."""
    session = FakeSession({"asr": [(503, {"error": "down"}), (503, {"error": "down"})]})
    client = make_client(session=session)  # no retries= override: uses MAX_RETRIES
    with pytest.raises(BhashiniHTTPError):
        client.asr(make_wav(), "en")
    assert len(session.requests) == MAX_RETRIES + 1


def test_connection_error_is_retried_and_can_succeed():
    session = FakeSession({"nmt": [connection_error(), (200, {"translated_text": "ok"})]})
    client = make_client(session=session, retries=2)
    result = client.nmt("hello", "en", "hi")
    assert result == "ok"


def test_connection_error_exhausts_retries():
    session = FakeSession({"nmt": [connection_error(), connection_error()]})
    client = make_client(session=session, retries=1)
    with pytest.raises(BhashiniConnectionError):
        client.nmt("hello", "en", "hi")


def test_worst_case_elapsed_time_for_fully_failing_nmt_call_is_bounded():
    """Documents the worst-case wall time for one fully-failing call under
    the new retry policy (finding H2): the old policy's worst case was
    ~15s for a single call (5s timeout x up to 3 attempts, linear
    backoff); the new one is bounded by MAX_RETRIES backoffs of
    RETRY_BACKOFF_SECONDS each. Deliberately uses the REAL sleep (not
    make_client's no-op fake) since this test's entire point is to measure
    real elapsed time -- the bound below is generous enough (well under a
    second, nowhere near the old ~5s suite-wide target) that this does not
    meaningfully slow the suite down.
    """
    session = FakeSession({"nmt": [connection_error(), connection_error()]})
    client = BhashiniClient(session=session)  # default retries=MAX_RETRIES, real time.sleep
    start = time.monotonic()
    with pytest.raises(BhashiniConnectionError):
        client.nmt("hello", "en", "hi")
    elapsed = time.monotonic() - start
    # documented bound: MAX_RETRIES backoffs of RETRY_BACKOFF_SECONDS each,
    # plus generous headroom for scheduling jitter.
    bound = MAX_RETRIES * RETRY_BACKOFF_SECONDS + 1.0
    assert elapsed < bound, f"fully-failing nmt() call took {elapsed:.3f}s, expected < {bound:.3f}s"


def test_missing_expected_field_raises_response_error():
    session = FakeSession({"asr": [(200, {"not_text": "oops"})]})
    client = make_client(session=session)
    with pytest.raises(BhashiniResponseError, match="missing 'text'"):
        client.asr(make_wav(), "en")


def test_non_json_body_raises_response_error():
    session = FakeSession({"tts": [(200, None)]})  # FakeResponse.json() will raise
    client = make_client(session=session)
    with pytest.raises(BhashiniResponseError, match="non-JSON response"):
        client.tts("hello", "en")


def test_malformed_tts_base64_raises_response_error():
    session = FakeSession({"tts": [(200, {"audio_base64": "not-valid-base64!!"})]})
    client = make_client(session=session)
    with pytest.raises(BhashiniResponseError, match="did not decode"):
        client.tts("hello", "en")


# --- Deadline (finding H2, part 3.2) -----------------------------------------

def test_no_deadline_behaves_as_before():
    """Passing no deadline at all (the default) uses self.timeout
    unchanged -- existing callers that never pass a deadline see no
    behaviour change."""
    session = FakeSession({"asr": [(200, {"text": "take one tablet"})]})
    client = make_client(session=session, timeout=5.0)
    client.asr(make_wav(), "en")
    assert session.requests[-1]["timeout"] == 5.0


def test_exhausted_deadline_raises_before_attempting_a_request():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    clock.advance(1.5)  # already past budget, with zero real sleep
    session = FakeSession({"asr": [(200, {"text": "should never be reached"})]})
    client = make_client(session=session)
    with pytest.raises(BhashiniDeadlineExceeded):
        client.asr(make_wav(), "en", deadline=deadline)
    assert session.requests == []  # never even attempted


def test_deadline_caps_request_timeout_to_remaining_budget():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=2.0, clock=clock)
    clock.advance(1.7)  # 0.3s left in the budget
    session = FakeSession({"asr": [(200, {"text": "ok"})]})
    client = make_client(session=session, timeout=5.0)  # per-call ceiling is larger
    client.asr(make_wav(), "en", deadline=deadline)
    assert session.requests[-1]["timeout"] == pytest.approx(0.3)


def test_deadline_does_not_widen_a_smaller_per_call_timeout():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=10.0, clock=clock)  # plenty of budget left
    session = FakeSession({"asr": [(200, {"text": "ok"})]})
    client = make_client(session=session, timeout=1.5)  # per-call ceiling is smaller
    client.asr(make_wav(), "en", deadline=deadline)
    assert session.requests[-1]["timeout"] == 1.5


def test_deadline_exceeded_between_attempts_stops_the_retry():
    """Simulates the budget running out during the first attempt's
    (simulated) network time, so the retry never fires."""
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)

    class AdvancingSession(FakeSession):
        def post(self, url, json=None, timeout=None):
            clock.advance(1.5)  # first attempt "took" longer than the budget
            return super().post(url, json=json, timeout=timeout)

    session = AdvancingSession({"asr": [connection_error(), (200, {"text": "should not be reached"})]})
    client = make_client(session=session)
    with pytest.raises(BhashiniDeadlineExceeded):
        client.asr(make_wav(), "en", deadline=deadline)
    assert len(session.requests) == 1  # first attempt happened, the retry did not


# --- record mode ------------------------------------------------------------

def test_record_dir_writes_a_fixture_file(tmp_path):
    session = FakeSession({"asr": [(200, {"text": "take one tablet"})]})
    client = BhashiniClient(session=session, record_dir=tmp_path)
    client.asr(make_wav(), "en")

    written = list((tmp_path / "asr").glob("*.json"))
    assert len(written) == 1
    record = json.loads(written[0].read_text())
    assert record["status_code"] == 200
    assert record["response"] == {"text": "take one tablet"}
    assert record["request"]["language"] == "en"


def test_record_dir_does_not_write_on_construction_alone(tmp_path):
    FakeSession({})
    BhashiniClient(record_dir=tmp_path)
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
