"""Fake ASR / NMT / TTS backends for the mock Bhashini service.

Contract fidelity, not quality: none of these do real speech recognition,
translation, or synthesis. Each is deterministic (same input always
produces the same output) and needs no ML model, model download, or
network access, so `python -m services.mock_bhashini` starts instantly and
`tools/capture_bhashini_contract.py` produces a reproducible fixture
corpus.
"""
from __future__ import annotations

import hashlib
import math
import struct
import wave
from io import BytesIO

#: Languages the mock (and puriyudha) actually support -- see CLAUDE.md
#: "Language scope".
SUPPORTED_LANGUAGES = ("en", "hi", "ta")

# A handful of clean, tidy, fully-punctuated canned phrases per language, so
# fake_asr_transcribe() can return something that reads like a textbook
# transcript when a caller explicitly asks for that (pool="clean"). Real
# Bhashini ASR output will essentially never look like this -- see
# _MESSY_PHRASES below, which is what callers get by default.
_CLEAN_PHRASES = {
    "en": [
        "take one tablet after food",
        "when should I take this medicine",
        "does this cause drowsiness",
    ],
    "hi": [
        "khana ke baad ek goli lijiye",
        "yeh dawai kab leni hai",
        "kya isse neend aati hai",
    ],
    "ta": [
        "unavukku pinnal oru maathirai edunga",
        "intha marunthu eppo edukanum",
        "idhu urakkam tharuma",
    ],
}

# Messy, hostile transcripts -- what real Bhashini ASR output actually looks
# like: no punctuation, filler words, stutters, dropped tokens, code-switched
# languages, numerals spelled inconsistently, truncation, and silence. Every
# category below has at least two examples per language (finding H1 from the
# verification pass: everything downstream, the sig parser in T3 especially,
# was at risk of being built and tested only against input that cannot occur
# in the field).
#
# UNVERIFIED AGAINST THE REAL DEVICE (no Jetson until 5 Aug 2026; see
# CLAUDE.md and docs/ASSUMPTIONS.md): we do not yet know whether the
# on-device Bhashini ASR emits Hindi/Tamil in native script or as a Latin
# transliteration, so both are represented below for "hi" and "ta". Confirm
# on 5 Aug and prune whichever turns out to be wrong.
_MESSY_PHRASES = {
    "en": [
        # (a) no punctuation, inconsistent casing
        "morning one tablet night one tablet after food five days",
        "TAKE one TABLET twice a day WITH food for a WEEK",
        # (b) filler and hesitation
        "so uh one tablet in the morning and um one at night",
        "take um like one tablet after food i think twice a day",
        # (c) repeated or stuttered words
        "take take one tablet after after food",
        "one one tablet in the the morning and night",
        # (d) dropped words (a key token simply missing -- no dose or no timing)
        "one tablet morning night food five days",
        "take tablet after food for days",
        # (e) code-switching mid-sentence, in Latin script as ASR would emit
        # it (here: a bilingual pharmacist's English shading into Tamil/Hindi
        # -- the mirror image of the Tamil-English and Hindi-English
        # switching in the "ta"/"hi" pools below)
        "take onnu tablet in the morning after saapadu",
        "take ek tablet subah aur raat khana ke baad",
        # (f) numerals as words and vice versa, mixed in the same utterance
        "take 1 tablet two times a day for 5 days",
        "take one tablet 2 times a day for five days",
        # (g) truncated utterance, stops mid-word
        "take one tab",
        "morning one tablet after fo",
        # (h) empty / whitespace-only transcript (real ASR returns this on silence)
        "",
        "   ",
    ],
    "hi": [
        # (a) no punctuation, inconsistent casing (native Devanagari has no
        # casing distinction, so this is shown once in transliteration where
        # casing is meaningful, and once in native script showing the
        # equivalent "no punctuation" problem)
        "khana ke baad ek goli subah raat paanch din",
        "KHANA ke baad EK goli SUBAH aur RAAT ko paanch din",
        # (b) filler and hesitation
        "matlab ek goli subah mein aur phir raat ko bhi ek",
        "मतलब वो एक गोली सुबह और फिर रात को भी एक",
        # (c) repeated or stuttered words
        "ek ek goli subah subah lijiye khana ke baad",
        "एक गोली सुबह सुबह और रात रात को खाने के बाद",
        # (d) dropped words (a key token simply missing)
        "ek goli subah raat khana din",
        "गोली सुबह रात खाने के बाद",
        # (e) Hindi-English code-switching mid-sentence, in Latin script as
        # ASR would emit it
        "subah one tablet raat ko ek tablet khana ke baad five days",
        "morning mein ek goli aur night mein bhi one tablet paanch din tak",
        # (f) numerals as words and vice versa, mixed in the same utterance
        "1 goli subah aur ek goli raat ko paanch din tak",
        "एक गोली सुबह और 1 गोली रात को 5 din tak",
        # (g) truncated utterance, stops mid-word
        "khana ke baad ek go",
        "सुबह एक गोली रात को खाने के",
        # (h) empty / whitespace-only transcript (real ASR returns this on silence)
        "",
        "   ",
    ],
    "ta": [
        # (a) no punctuation, inconsistent casing (again shown once in
        # transliteration, where casing is meaningful, and once in native
        # script for the "no punctuation" half of the problem)
        "kaalai oru maathirai iravu oru maathirai saapadu pinnaadi ainthu naatkal",
        "KAALAI oru MAATHIRAI iravu ORU maathirai SAAPADU pinnaadi ainthu naatkal",
        # (b) filler and hesitation
        "appadi na oru maathirai kaalaila innoru iravula edukanum",
        "அப்படி என்னா ஒரு மாத்திரை காலையில வேற ஒண்ணு இரவுல",
        # (c) repeated or stuttered words
        "edunga edunga oru maathirai saapadu pinnaadi pinnaadi",
        "ஒரு ஒரு மாத்திரை காலையில காலையில எடுங்க",
        # (d) dropped words (a key token simply missing)
        "oru maathirai kaalai iravu saapadu naatkal",
        "மாத்திரை காலையில் இரவில் சாப்பாட்டுக்கு பிறகு",
        # (e) Tamil-English code-switching mid-sentence, in Latin script as
        # ASR would emit it
        "morning la onnu night la onnu saapadu ku appuram five days",
        "night ku appuram rendu maathirai kaalai la onnu five naatkal",
        # (f) numerals as words and vice versa, mixed in the same utterance
        "1 maathirai kaalai and oru maathirai iravu 5 naatkal ku",
        "ஒரு மாத்திரை காலை மற்றும் 1 மாத்திரை இரவு 5 நாட்கள்",
        # (g) truncated utterance, stops mid-word
        "kaalai oru maathi",
        "இரவில் ஒரு மாத்திரை சாப்பாட்",
        # (h) empty / whitespace-only transcript (real ASR returns this on silence)
        "",
        "   ",
    ],
}

#: Valid values for fake_asr_transcribe's `pool` argument and the mock
#: server's `--messy` flag. "mixed" (clean + messy combined) is the honest
#: default for anything that isn't an explicit, deliberate test of one pool
#: or the other -- see fake_asr_transcribe's docstring.
ASR_POOLS = ("clean", "messy", "mixed")


def messy_phrases(language: str) -> list:
    """The full messy-transcript fixture pool for `language`, in fixed
    order. A public, stable way for other modules' tests (notably
    tests/test_sig.py, which exercises the rules parser against every
    phrase in this pool -- CLAUDE.md invariant #6, sig is the only
    extraction path) to enumerate every fixture deterministically, instead
    of hash-sampling via fake_asr_transcribe() (which only surfaces
    whichever phrase a given WAV's hash happens to select) or reaching
    into the underscore-prefixed _MESSY_PHRASES directly."""
    if language not in SUPPORTED_LANGUAGES:
        raise FakeAsrError(f"unsupported language {language!r}")
    return list(_MESSY_PHRASES[language])


def clean_phrases(language: str) -> list:
    """The full clean-transcript fixture pool for `language` -- see
    messy_phrases()."""
    if language not in SUPPORTED_LANGUAGES:
        raise FakeAsrError(f"unsupported language {language!r}")
    return list(_CLEAN_PHRASES[language])


class FakeAsrError(ValueError):
    """The mock ASR backend could not accept the given audio."""


def fake_asr_transcribe(wav_bytes: bytes, language: str, pool: str = "mixed") -> str:
    """Return a deterministic pseudo-transcription for `wav_bytes`.

    Validates that `wav_bytes` at least parses as a WAV file (contract
    fidelity: a real ASR service would reject garbage audio too), then
    picks a canned phrase using a hash of the audio bytes, so the same
    recording always "transcribes" the same way.

    `pool` selects which phrase set to draw from: "clean" (tidy,
    grammatical, fully-punctuated -- rare in the field), "messy" (no
    punctuation, filler, stutters, dropped words, code-switching, mixed
    numeral styles, truncation, or silence -- what real ASR output actually
    looks like), or "mixed" (both, combined -- the default, and what the
    mock server uses unless started with a narrower --messy mode). Tests
    that specifically need clean, easy input should pass pool="clean"
    explicitly rather than relying on this default.
    """
    if language not in SUPPORTED_LANGUAGES:
        raise FakeAsrError(f"unsupported language {language!r}")
    if pool not in ASR_POOLS:
        raise FakeAsrError(f"unknown pool {pool!r}; expected one of {ASR_POOLS}")
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wf:
            if wf.getnframes() == 0:
                raise FakeAsrError("audio contains zero frames")
    except wave.Error as exc:
        raise FakeAsrError(f"not a valid WAV file: {exc}") from exc

    phrases = _phrase_pool(language, pool)
    digest = hashlib.sha256(wav_bytes).digest()
    return phrases[digest[0] % len(phrases)]


def _phrase_pool(language: str, pool: str) -> list:
    if pool == "clean":
        return _CLEAN_PHRASES[language]
    if pool == "messy":
        return _MESSY_PHRASES[language]
    return _CLEAN_PHRASES[language] + _MESSY_PHRASES[language]


# A tiny dictionary of canned phrase-pairs, keyed by (src, tgt, source
# text). Anything not in the dictionary falls back to a clearly-fake
# passthrough marker rather than attempting real translation.
_NMT_DICTIONARY = {
    ("en", "hi", "take one tablet after food"): "khana ke baad ek goli lijiye",
    ("en", "ta", "take one tablet after food"): "unavukku pinnal oru maathirai edunga",
    ("hi", "en", "khana ke baad ek goli lijiye"): "take one tablet after food",
    ("ta", "en", "unavukku pinnal oru maathirai edunga"): "take one tablet after food",
}


class FakeNmtError(ValueError):
    """The mock NMT backend could not accept the given request."""


def fake_nmt_translate(text: str, src_lang: str, tgt_lang: str) -> str:
    """Dictionary lookup with a deterministic passthrough fallback.

    Real translation quality is irrelevant here -- this only needs to give
    callers *some* string back so retry/merge/verify code has something
    to exercise. `src_lang`/`tgt_lang` are taken exactly as given (this
    mock does not itself normalise casing -- that is the client's job at
    our boundary, and part of what tools/verify_contract.py checks).
    """
    if not text:
        raise FakeNmtError("text must not be empty")
    key = (src_lang.strip().lower(), tgt_lang.strip().lower(), text)
    if key in _NMT_DICTIONARY:
        return _NMT_DICTIONARY[key]
    return f"[{tgt_lang}] {text}"


class FakeTtsError(ValueError):
    """The mock TTS backend could not accept the given request."""

#: Seconds of synthesized audio per character of input text.
_TTS_SECONDS_PER_CHAR = 0.06
_TTS_MIN_SECONDS = 0.3
_TTS_MAX_SECONDS = 5.0
_TTS_SAMPLE_RATE = 16_000
_TTS_TONE_HZ = 220.0


def fake_tts_synthesize(text: str, language: str) -> bytes:
    """Return a valid mono 16-bit PCM WAV (a plain sine tone), whose
    duration scales with `len(text)`, encoded as real WAV bytes.

    Any code that does `wave.open(...)` on this output -- exactly what
    `puriyudha.clients.bhashini.BhashiniClient.tts()` callers will do --
    gets a genuinely parseable file, which is the point: this exercises
    the real WAV-handling path even though the "speech" is a tone.
    """
    if language not in SUPPORTED_LANGUAGES:
        raise FakeTtsError(f"unsupported language {language!r}")
    if not text:
        raise FakeTtsError("text must not be empty")

    duration = min(_TTS_MAX_SECONDS, max(_TTS_MIN_SECONDS, len(text) * _TTS_SECONDS_PER_CHAR))
    n_samples = int(duration * _TTS_SAMPLE_RATE)
    amplitude = 8000
    samples = [
        int(amplitude * math.sin(2 * math.pi * _TTS_TONE_HZ * (i / _TTS_SAMPLE_RATE)))
        for i in range(n_samples)
    ]
    frames = struct.pack(f"<{n_samples}h", *samples)

    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_TTS_SAMPLE_RATE)
        wf.writeframes(frames)
    return buf.getvalue()
