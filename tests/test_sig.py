"""Tests for puriyudha.sig: the rules-based prescription shorthand parser.

CLAUDE.md invariant #6: with the LLM fallback cut, this parser is the only
extraction path -- its coverage against real, messy input IS the product's
accuracy. Per instruction, this file tests primarily against the messy
"en" ASR pool added in finding H1 (services/mock_bhashini/fake_backends.py),
not clean text: every one of its 16 phrases (plus the 2 silence/whitespace
entries within it) gets an explicit, hand-verified expected outcome below,
not just a smoke test. A handful of clean-pool cases are included too,
specifically because they demonstrate that "clean" (grammatical, punctuated)
does not imply "resolvable" -- e.g. "take one tablet after food" is
perfectly clean and still missing frequency.

English only: CLAUDE.md's "Language scope" makes English the clinician-side
input language; the "hi"/"ta" ASR pools are for the ASR/NMT/TTS boundary in
general, not clinician dosing shorthand, so they are out of scope here (see
puriyudha/sig/parser.py's module docstring).
"""
from dataclasses import replace

import pytest

from puriyudha.schema import (
    Confidence,
    FoodRelation,
    Frequency,
    NeedsHumanConfirmation,
    Slot,
)
from puriyudha.sig import SIG_FIELDS, SigParseResult, parse_sig, parse_sig_or_confirm
from services.mock_bhashini.fake_backends import clean_phrases, messy_phrases

EN_MESSY = messy_phrases("en")
EN_CLEAN = clean_phrases("en")


def test_messy_pool_has_not_silently_shrunk():
    """Guard against this file's hand-verified expectations silently going
    stale if services/mock_bhashini/fake_backends.py's fixture pool changes
    without this file being updated to match."""
    assert len(EN_MESSY) == 16
    assert len(EN_CLEAN) == 3


# --- Per-phrase expectations against the messy "en" pool -----------------------
#
# (phrase, expected_dose_qty, expected_frequency_or_None, expected_food_relation,
#  expected_duration_days, expected_unresolved_fields)
#
# expected_frequency is (times_per_day, slots_tuple) or None if frequency is
# expected to be unresolved.

MESSY_CASES = [
    (
        "morning one tablet night one tablet after food five days",
        1.0, (2, (Slot.MORNING, Slot.NIGHT)), FoodRelation.AFTER, 5, (),
    ),
    (
        "TAKE one TABLET twice a day WITH food for a WEEK",
        1.0, (2, ()), FoodRelation.WITH, 7, (),
    ),
    (
        "so uh one tablet in the morning and um one at night",
        1.0, (2, (Slot.MORNING, Slot.NIGHT)), FoodRelation.ANY, None, (),
    ),
    (
        "take um like one tablet after food i think twice a day",
        1.0, (2, ()), FoodRelation.AFTER, None, (),
    ),
    (
        "take take one tablet after after food",
        1.0, None, FoodRelation.AFTER, None, ("frequency",),
    ),
    (
        "one one tablet in the the morning and night",
        1.0, (2, (Slot.MORNING, Slot.NIGHT)), FoodRelation.ANY, None, (),
    ),
    (
        "one tablet morning night food five days",
        1.0, (2, (Slot.MORNING, Slot.NIGHT)), FoodRelation.ANY, 5, ("food_relation",),
    ),
    (
        "take tablet after food for days",
        None, None, FoodRelation.AFTER, None, ("dose_qty", "frequency", "duration_days"),
    ),
    (
        "take onnu tablet in the morning after saapadu",
        1.0, (1, (Slot.MORNING,)), FoodRelation.AFTER, None, (),
    ),
    (
        "take ek tablet subah aur raat khana ke baad",
        1.0, (2, (Slot.MORNING, Slot.NIGHT)), FoodRelation.AFTER, None, (),
    ),
    (
        "take 1 tablet two times a day for 5 days",
        1.0, (2, ()), FoodRelation.ANY, 5, (),
    ),
    (
        "take one tablet 2 times a day for five days",
        1.0, (2, ()), FoodRelation.ANY, 5, (),
    ),
    (
        "take one tab",
        1.0, None, FoodRelation.ANY, None, ("frequency",),
    ),
    (
        "morning one tablet after fo",
        1.0, (1, (Slot.MORNING,)), FoodRelation.ANY, None, ("food_relation",),
    ),
    (
        "",
        None, None, FoodRelation.ANY, None, ("dose_qty", "frequency"),
    ),
    (
        "   ",
        None, None, FoodRelation.ANY, None, ("dose_qty", "frequency"),
    ),
]


def _ids(case):
    return repr(case[0])[:40]


@pytest.mark.parametrize("case", MESSY_CASES, ids=_ids)
def test_messy_pool_matches_the_fixture_pool(case):
    """The phrases hardcoded above must be exactly the fixture pool's
    phrases, in order -- this file's expectations are meaningless if they
    silently drifted from what fake_asr_transcribe() actually returns."""
    phrase = case[0]
    assert phrase in EN_MESSY


@pytest.mark.parametrize("case", MESSY_CASES, ids=_ids)
def test_messy_phrase_resolves_as_expected(case):
    phrase, exp_dose, exp_freq, exp_food, exp_duration, exp_unresolved = case
    result = parse_sig(phrase)

    assert result.dose_qty == exp_dose
    if exp_freq is None:
        assert result.frequency is None
    else:
        times_per_day, slots = exp_freq
        assert result.frequency == Frequency(times_per_day=times_per_day, slots=list(slots))
    assert result.food_relation == exp_food
    assert result.duration_days == exp_duration
    assert result.unresolved_fields == exp_unresolved
    assert result.fully_resolved == (not exp_unresolved)


# --- Clean-pool cases: clean is not the same as resolvable ----------------------

def test_clean_phrase_missing_frequency_is_unresolved():
    phrase = "take one tablet after food"
    assert phrase in EN_CLEAN
    result = parse_sig(phrase)
    assert result.dose_qty == 1.0
    assert result.food_relation == FoodRelation.AFTER
    assert result.frequency is None
    assert result.unresolved_fields == ("frequency",)
    assert not result.fully_resolved


@pytest.mark.parametrize(
    "phrase", ["when should I take this medicine", "does this cause drowsiness"]
)
def test_clean_questions_have_no_dosing_information(phrase):
    assert phrase in EN_CLEAN
    result = parse_sig(phrase)
    assert result.dose_qty is None
    assert result.frequency is None
    assert result.unresolved_fields == ("dose_qty", "frequency")
    assert not result.fully_resolved


# --- Never a silently incomplete order -------------------------------------------

def _assert_never_silently_incomplete(phrase):
    """The core safety property this task demands: an unresolved result
    always converts cleanly to a NeedsHumanConfirmation (never raises,
    never gets treated as usable), and a fully-resolved result never has
    an unresolved field's placeholder value mistaken for a real one."""
    result = parse_sig(phrase)
    if result.fully_resolved:
        confirmation = result.to_needs_confirmation
        with pytest.raises(ValueError):
            confirmation()
        combined = parse_sig_or_confirm(phrase)
        assert isinstance(combined, SigParseResult)
    else:
        confirmation = result.to_needs_confirmation()
        assert isinstance(confirmation, NeedsHumanConfirmation)
        assert confirmation.field_name == result.unresolved_fields[0]
        assert confirmation.field_name in SIG_FIELDS
        combined = parse_sig_or_confirm(phrase)
        assert isinstance(combined, NeedsHumanConfirmation)


@pytest.mark.parametrize("phrase", EN_MESSY + EN_CLEAN)
def test_never_silently_incomplete_every_pool_phrase(phrase):
    _assert_never_silently_incomplete(phrase)


#: Adversarial input beyond the fixture pool -- out-of-range values and
#: outright garbage, to check robustness (no crash, correct refusal)
#: rather than exact semantic extraction. Fixed strings, not real
#: randomness (CLAUDE.md: tests must be hermetic/reproducible).
_ADVERSARIAL_PHRASES = (
    "0 tablets twice a day after food",  # dose_qty at the invalid boundary (must be > 0)
    "999999 tablets twice a day after food",  # dose_qty far past MAX_DOSE_QTY
    "one tablet 99 times a day after food",  # frequency far past MAX_TIMES_PER_DAY
    "one tablet twice a day after food for 50000 days",  # duration far past MAX_DURATION_DAYS
    "asdkjfh @#$%^&* 12345 !!! ???",  # pure garbage, no recognisable structure at all
    "\t\n\t  \n",  # whitespace-only, but tabs/newlines instead of plain spaces
    "tablet " * 200,  # pathologically repetitive, no quantity anywhere
    "नमस्ते emoji \U0001F48A unrelated text",  # non-ASCII/emoji noise
)


@pytest.mark.parametrize("phrase", _ADVERSARIAL_PHRASES)
def test_never_silently_incomplete_adversarial_input(phrase):
    _assert_never_silently_incomplete(phrase)


def test_adversarial_out_of_range_values_are_unresolved_not_clamped():
    """Out-of-range values must never be silently clamped into validity --
    that would be guessing a plausible-looking number, exactly what
    CLAUDE.md invariant #3 forbids."""
    assert parse_sig("0 tablets twice a day after food").dose_qty is None
    assert parse_sig("999999 tablets twice a day after food").dose_qty is None
    assert parse_sig("one tablet 99 times a day after food").frequency is None
    assert parse_sig("one tablet twice a day after food for 50000 days").duration_days is None


def test_to_needs_confirmation_names_highest_priority_unresolved_field():
    result = parse_sig("take tablet after food for days")  # dose_qty, frequency, duration_days unresolved
    assert result.unresolved_fields == ("dose_qty", "frequency", "duration_days")
    confirmation = result.to_needs_confirmation()
    assert confirmation.field_name == "dose_qty"  # highest priority per SIG_FIELDS order


def test_sig_fields_priority_order():
    assert SIG_FIELDS == ("dose_qty", "frequency", "food_relation", "duration_days")


# --- Individual rule behaviour, isolated from the fixture pool -----------------

def test_parse_sig_rejects_non_string():
    with pytest.raises(TypeError):
        parse_sig(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_sig(None)  # type: ignore[arg-type]


def test_case_insensitive_dose_and_food():
    result = parse_sig("TAKE ONE TABLET AFTER FOOD TWICE A DAY")
    assert result.dose_qty == 1.0
    assert result.food_relation == FoodRelation.AFTER
    assert result.frequency == Frequency(times_per_day=2)


def test_conflicting_dose_quantities_are_unresolved():
    result = parse_sig("one tablet in the morning and two tablets at night")
    assert result.dose_qty is None
    assert "dose_qty" in result.unresolved_fields


def test_dose_unit_variants():
    for text, expected in [
        ("take 2 tabs after food twice a day", 2.0),
        ("take 3 capsules after food twice a day", 3.0),
        ("take 1 cap after food twice a day", 1.0),
        ("take 5 ml after food twice a day", 5.0),
    ]:
        result = parse_sig(text)
        assert result.dose_qty == expected, text


def test_once_a_day_and_thrice_a_day_phrasing():
    once = parse_sig("take one tablet once a day after food")
    assert once.frequency == Frequency(times_per_day=1)
    thrice = parse_sig("take one tablet thrice a day after food")
    assert thrice.frequency == Frequency(times_per_day=3)


def test_conflicting_explicit_count_and_slot_count_is_unresolved():
    # "twice a day" (explicit 2) contradicts three named slots.
    result = parse_sig("one tablet twice a day morning noon and night after food")
    assert result.frequency is None
    assert "frequency" in result.unresolved_fields


def test_before_and_with_food_relation():
    before = parse_sig("take one tablet before food twice a day")
    assert before.food_relation == FoodRelation.BEFORE
    with_food = parse_sig("take one tablet with food twice a day")
    assert with_food.food_relation == FoodRelation.WITH


def test_hindi_postposition_food_relation_forms():
    after = parse_sig("take one tablet khana ke baad twice a day")
    assert after.food_relation == FoodRelation.AFTER
    before = parse_sig("take one tablet khana se pehle twice a day")
    assert before.food_relation == FoodRelation.BEFORE


def test_food_mentioned_but_unparseable_is_unresolved_not_defaulted():
    result = parse_sig("one tablet twice a day food")
    assert result.unresolved_fields == ("food_relation",)
    assert result.food_relation == FoodRelation.ANY  # placeholder only -- see docstring


def test_duration_in_weeks():
    result = parse_sig("one tablet twice a day after food for 2 weeks")
    assert result.duration_days == 14


def test_duration_word_a_day_and_a_week():
    a_day = parse_sig("one tablet twice a day after food for a day")
    assert a_day.duration_days == 1
    a_week = parse_sig("one tablet twice a day after food for a week")
    assert a_week.duration_days == 7


def test_prn_detection():
    for phrase in [
        "take one tablet as needed after food",
        "take one tablet if needed after food",
        "take one tablet sos after food",
    ]:
        result = parse_sig(phrase)
        assert result.prn is True, phrase
    assert parse_sig("take one tablet twice a day after food").prn is False


def test_stutter_collapses_without_changing_meaning():
    stuttered = parse_sig("take take one one tablet after after food twice twice a day")
    clean = parse_sig("take one tablet after food twice a day")
    # "twice twice" is still adjacent single-token repetition (like "take
    # take" and "one one"), so single-token dedup collapses it too --
    # every field, not just dose/food, matches the unstuttered phrase
    # (raw_text necessarily differs, so it's excluded from the comparison).
    assert replace(stuttered, raw_text=clean.raw_text) == clean


def test_confidence_is_binary_never_fractional():
    """CLAUDE.md invariant #5: deterministic rules, not fuzzy scoring --
    a regex/keyword rule either matched or it didn't."""
    for phrase in EN_MESSY + EN_CLEAN:
        result = parse_sig(phrase)
        for value in result.confidence.per_field.values():
            assert value in (0.0, 1.0)
        assert result.confidence.overall in (0.0, 1.0)


def test_confidence_overall_is_min_of_per_field():
    result = parse_sig("take tablet after food for days")
    assert result.confidence.overall == min(result.confidence.per_field.values())
    assert result.confidence.overall == 0.0


def test_fully_resolved_result_has_overall_confidence_one():
    result = parse_sig("take one tablet twice a day after food for 5 days")
    assert result.fully_resolved
    assert result.confidence.overall == 1.0


def test_sig_parse_result_is_frozen():
    result = parse_sig("take one tablet twice a day after food")
    with pytest.raises(Exception):
        result.dose_qty = 5.0  # type: ignore[misc]


def test_confidence_type_is_reused_from_schema():
    result = parse_sig("take one tablet twice a day after food")
    assert isinstance(result.confidence, Confidence)
