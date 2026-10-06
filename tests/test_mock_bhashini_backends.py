"""Unit tests for services.mock_bhashini.fake_backends.

Pure function calls only -- no HTTP, no sockets, no server process. The
HTTP layer itself (services/mock_bhashini/server.py) is exercised manually
(see docs/dev_environment.md-style acceptance: `python -m
services.mock_bhashini` + curl), not under pytest, since CLAUDE.md forbids
tests from touching the network and binding even a loopback socket counts.
"""
import wave
from io import BytesIO

import pytest

from services.mock_bhashini.fake_backends import (
    ASR_POOLS,
    SUPPORTED_LANGUAGES,
    FakeAsrError,
    FakeNmtError,
    FakeTtsError,
    _CLEAN_PHRASES,
    _MESSY_PHRASES,
    fake_asr_transcribe,
    fake_nmt_translate,
    fake_tts_synthesize,
)


def make_wav(n_frames=800, rate=16000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x01" * n_frames)
    return buf.getvalue()


def transcribe(wav_bytes: bytes, language: str, pool: str = "messy") -> str:
    """Thin wrapper defaulting every call in this file to pool="messy"
    (finding H1, part 2.3): the default path under test must be the hostile
    one, matching what real Bhashini ASR output actually looks like. A test
    that specifically needs clean, grammatical input passes pool="clean"
    explicitly (fake_asr_transcribe's own default, "mixed", is what the mock
    *server* uses -- see services/mock_bhashini/server.py)."""
    return fake_asr_transcribe(wav_bytes, language, pool=pool)


# --- fake_asr_transcribe -----------------------------------------------------

def test_asr_returns_a_string_for_each_supported_language():
    # Explicitly clean: this test only cares that *some* non-empty string
    # comes back, which the messy pool cannot promise (it deliberately
    # includes empty/whitespace-only "silence" transcripts -- see
    # test_asr_messy_pool_can_return_empty_transcript below).
    wav_bytes = make_wav()
    for lang in SUPPORTED_LANGUAGES:
        text = transcribe(wav_bytes, lang, pool="clean")
        assert isinstance(text, str) and text


def test_asr_is_deterministic_for_the_same_input():
    wav_bytes = make_wav()
    first = transcribe(wav_bytes, "en")
    second = transcribe(wav_bytes, "en")
    assert first == second


def test_asr_rejects_unsupported_language():
    with pytest.raises(FakeAsrError, match="unsupported language"):
        transcribe(make_wav(), "fr")


def test_asr_rejects_non_wav_bytes():
    with pytest.raises(FakeAsrError, match="not a valid WAV"):
        transcribe(b"not a wav file at all", "en")


def test_asr_rejects_zero_frame_wav():
    with pytest.raises(FakeAsrError, match="zero frames"):
        transcribe(make_wav(n_frames=0), "en")


def test_asr_rejects_unknown_pool():
    with pytest.raises(FakeAsrError, match="unknown pool"):
        fake_asr_transcribe(make_wav(), "en", pool="grammatical-only-please")


# --- messy phrase pools (finding H1) -----------------------------------------

#: 8 documented categories x >= 2 examples each -- see fake_backends.py's
#: _MESSY_PHRASES module docstring for what each category is.
MINIMUM_MESSY_EXAMPLES_PER_LANGUAGE = 16


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_messy_pool_has_minimum_examples_per_language(language):
    assert len(_MESSY_PHRASES[language]) >= MINIMUM_MESSY_EXAMPLES_PER_LANGUAGE


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_clean_and_messy_pools_both_exist_for_every_language(language):
    assert _CLEAN_PHRASES[language]
    assert _MESSY_PHRASES[language]


def test_asr_messy_pool_can_return_empty_transcript():
    """Real ASR returns an empty (or whitespace-only) transcript on
    silence -- category (h). Confirms it is actually reachable, not just
    present in the list."""
    found_empty = any(
        transcribe(make_wav(n_frames=n), "en", pool="messy").strip() == ""
        for n in range(1, 4000, 37)
    )
    assert found_empty, "no (wav-length, digest) combination in the sampled range hit an empty messy transcript"


def test_asr_clean_pool_never_returns_empty_transcript():
    for n in range(1, 4000, 37):
        assert transcribe(make_wav(n_frames=n), "en", pool="clean").strip() != ""


def test_asr_mixed_pool_can_draw_from_either_clean_or_messy():
    """pool="mixed" (fake_asr_transcribe's own default, and the mock
    server's default -- CLAUDE.md "Refuse rather than guess" aside, the
    *dev tooling* default should be the realistic one) is the union of both
    pools, so across enough distinct inputs it must eventually return a
    phrase that only exists in _CLEAN_PHRASES and one that only exists in
    _MESSY_PHRASES."""
    clean_only = set(_CLEAN_PHRASES["en"])
    messy_only = set(_MESSY_PHRASES["en"])
    seen = {transcribe(make_wav(n_frames=n), "en", pool="mixed") for n in range(1, 4000, 37)}
    assert seen & clean_only, "mixed pool never produced a clean-only phrase in the sampled range"
    assert seen & messy_only, "mixed pool never produced a messy-only phrase in the sampled range"


def test_asr_pool_default_is_mixed():
    """fake_asr_transcribe's own default (distinct from this test file's
    pool="messy" convenience default -- see `transcribe()` above) is
    "mixed", matching the mock server's default (services/mock_bhashini/
    server.py DEFAULT_ASR_POOL)."""
    wav_bytes = make_wav()
    for lang in SUPPORTED_LANGUAGES:
        assert fake_asr_transcribe(wav_bytes, lang) == fake_asr_transcribe(wav_bytes, lang, pool="mixed")


def test_asr_pools_are_valid_pool_names():
    assert set(ASR_POOLS) == {"clean", "messy", "mixed"}


# --- fake_nmt_translate -----------------------------------------------------

def test_nmt_dictionary_hit():
    result = fake_nmt_translate("take one tablet after food", "en", "hi")
    assert result == "khana ke baad ek goli lijiye"


def test_nmt_falls_back_to_passthrough_marker_for_unknown_phrase():
    result = fake_nmt_translate("an unrecognised sentence", "en", "ta")
    assert result == "[ta] an unrecognised sentence"


def test_nmt_rejects_empty_text():
    with pytest.raises(FakeNmtError, match="must not be empty"):
        fake_nmt_translate("", "en", "hi")


# --- fake_tts_synthesize -----------------------------------------------------

def test_tts_returns_a_valid_wav():
    wav_bytes = fake_tts_synthesize("hello there", "en")
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() > 0


def test_tts_duration_scales_with_text_length():
    short_wav = fake_tts_synthesize("hi", "en")
    long_wav = fake_tts_synthesize("a much longer sentence than the short one", "en")
    with wave.open(BytesIO(short_wav), "rb") as wf:
        short_frames = wf.getnframes()
    with wave.open(BytesIO(long_wav), "rb") as wf:
        long_frames = wf.getnframes()
    assert long_frames > short_frames


def test_tts_rejects_unsupported_language():
    with pytest.raises(FakeTtsError, match="unsupported language"):
        fake_tts_synthesize("hello", "fr")


def test_tts_rejects_empty_text():
    with pytest.raises(FakeTtsError, match="must not be empty"):
        fake_tts_synthesize("", "en")
