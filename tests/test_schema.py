"""Tests for puriyudha.schema: range validation, JSON round-tripping, and
enum enforcement.

Hermetic: no network, no Ollama, no real Bhashini service -- everything
here is pure in-memory dataclass construction and (de)serialization.
"""
import json
from datetime import date

import pytest

import puriyudha.schema as schema_module
from puriyudha.schema import (
    Confidence,
    ComprehensionSlot,
    ConfirmedOrder,
    DEFAULT_CONFIDENCE_THRESHOLD,
    FoodRelation,
    Frequency,
    MedicationOrder,
    NeedsHumanConfirmation,
    PatientUnderstanding,
    Slot,
    SlotUnderstanding,
    Source,
    validate,
)

# Reaching into a leading-underscore name is deliberate here: these tests
# exist specifically to prove ConfirmedOrder's private-constructor-token
# enforcement, which by definition requires the real token in-hand to
# demonstrate what "the real one" looks like against forged alternatives.
from puriyudha.schema import _CONFIRMED_ORDER_TOKEN


def make_order(**overrides) -> MedicationOrder:
    """A structurally valid, high-confidence MedicationOrder, with any
    field overridable for a specific test."""
    kwargs = dict(
        drug_generic="paracetamol",
        drug_display="Paracetamol 500mg",
        strength="500mg",
        form="tablet",
        dose_qty=1.0,
        route="oral",
        frequency=Frequency(times_per_day=2, slots=[Slot.MORNING, Slot.NIGHT]),
        food_relation=FoodRelation.AFTER,
        duration_days=5,
        prn=False,
        cautions=["may cause drowsiness"],
        followup_date=date(2026, 8, 4),
        source=Source.SIG_PARSER,
        confidence=Confidence(overall=0.95, per_field={"drug_generic": 0.98}),
    )
    kwargs.update(overrides)
    return MedicationOrder(**kwargs)


# --- baseline / construction --------------------------------------------------

def test_valid_order_constructs():
    order = make_order()
    assert order.drug_generic == "paracetamol"
    assert order.frequency.times_per_day == 2
    assert order.confidence.overall == 0.95


def test_string_fields_are_stripped():
    order = make_order(drug_generic="  paracetamol  ")
    assert order.drug_generic == "paracetamol"


# --- enum enforcement ----------------------------------------------------------

def test_food_relation_accepts_all_valid_members():
    for value in ("before", "after", "with", "any"):
        order = make_order(food_relation=value)
        assert order.food_relation is FoodRelation(value)


def test_food_relation_rejects_invalid_value():
    with pytest.raises(ValueError, match="food_relation"):
        make_order(food_relation="breakfast")


def test_slot_accepts_all_valid_members():
    for value in ("morning", "noon", "evening", "night"):
        freq = Frequency(times_per_day=1, slots=[value])
        assert freq.slots == [Slot(value)]


def test_slot_rejects_invalid_value():
    with pytest.raises(ValueError):
        Frequency(times_per_day=1, slots=["afternoon"])


def test_source_rejects_invalid_value():
    with pytest.raises(ValueError, match="source"):
        make_order(source="pharmacist_guess")


def test_source_accepts_all_valid_members():
    for value in ("sig_parser", "llm_fallback", "ocr", "manual"):
        order = make_order(source=value)
        assert order.source is Source(value)


def test_comprehension_slot_rejects_invalid_value():
    with pytest.raises(ValueError):
        SlotUnderstanding(slot="side_effects", patient_said="", correct=False)


# --- out-of-range rejection: dose / frequency / duration / confidence --------

@pytest.mark.parametrize("bad_dose", [0, -1, -0.5, 50.1, 100])
def test_dose_qty_out_of_range_rejected(bad_dose):
    with pytest.raises(ValueError, match="dose_qty"):
        make_order(dose_qty=bad_dose)


def test_dose_qty_at_bounds_accepted():
    assert make_order(dose_qty=50.0).dose_qty == 50.0
    assert make_order(dose_qty=0.5).dose_qty == 0.5


def test_dose_qty_wrong_type_rejected():
    with pytest.raises(ValueError, match="dose_qty"):
        make_order(dose_qty="one tablet")


@pytest.mark.parametrize("bad_times", [0, -1, 7, 100])
def test_times_per_day_out_of_range_rejected(bad_times):
    with pytest.raises(ValueError, match="times_per_day"):
        Frequency(times_per_day=bad_times)


def test_times_per_day_bounds_accepted():
    assert Frequency(times_per_day=1).times_per_day == 1
    assert Frequency(times_per_day=6).times_per_day == 6


def test_frequency_slots_length_must_match_times_per_day():
    with pytest.raises(ValueError, match="times_per_day"):
        Frequency(times_per_day=3, slots=[Slot.MORNING, Slot.NIGHT])


def test_frequency_slots_reject_duplicates():
    with pytest.raises(ValueError, match="repeat"):
        Frequency(times_per_day=2, slots=[Slot.MORNING, Slot.MORNING])


def test_frequency_allows_empty_slots_regardless_of_times_per_day():
    freq = Frequency(times_per_day=3, slots=[])
    assert freq.slots == []


@pytest.mark.parametrize("bad_duration", [0, -5, 366, 10_000])
def test_duration_days_out_of_range_rejected(bad_duration):
    with pytest.raises(ValueError, match="duration_days"):
        make_order(duration_days=bad_duration)


def test_duration_days_none_is_allowed():
    order = make_order(duration_days=None)
    assert order.duration_days is None


def test_duration_days_bounds_accepted():
    assert make_order(duration_days=1).duration_days == 1
    assert make_order(duration_days=365).duration_days == 365


@pytest.mark.parametrize("bad_conf", [-0.01, 1.01, -1, 2, float("nan")])
def test_confidence_overall_out_of_range_rejected(bad_conf):
    # NaN never satisfies 0.0 <= value <= 1.0 either, so it raises the same way.
    with pytest.raises(ValueError, match="confidence.overall"):
        Confidence(overall=bad_conf)


def test_confidence_per_field_out_of_range_rejected():
    with pytest.raises(ValueError, match="per_field"):
        Confidence(overall=0.9, per_field={"drug_generic": 1.5})


def test_confidence_bounds_accepted():
    conf = Confidence(overall=0.0, per_field={"x": 1.0})
    assert conf.overall == 0.0
    assert conf.per_field["x"] == 1.0


def test_prn_wrong_type_rejected():
    with pytest.raises(ValueError, match="prn"):
        make_order(prn="yes")


def test_followup_date_bad_string_rejected():
    with pytest.raises(ValueError, match="followup_date"):
        make_order(followup_date="not-a-date")


def test_followup_date_accepts_iso_string():
    order = make_order(followup_date="2026-08-04")
    assert order.followup_date == date(2026, 8, 4)


def test_followup_date_none_is_allowed():
    assert make_order(followup_date=None).followup_date is None


def test_cautions_reject_blank_entries():
    with pytest.raises(ValueError, match="cautions"):
        make_order(cautions=["  "])


def test_needs_human_confirmation_rejects_more_than_top_3_candidates():
    with pytest.raises(ValueError, match="top 3"):
        NeedsHumanConfirmation(
            field_name="drug_generic",
            candidates=["a", "b", "c", "d"],
            reason="ambiguous OCR",
        )


def test_needs_human_confirmation_accepts_up_to_3_candidates():
    nhc = NeedsHumanConfirmation(
        field_name="drug_generic", candidates=["a", "b", "c"], reason="ambiguous OCR"
    )
    assert nhc.candidates == ["a", "b", "c"]


def test_patient_understanding_rejects_empty_slots():
    with pytest.raises(ValueError, match="slots"):
        PatientUnderstanding(order=make_order(), slots=[])


def test_patient_understanding_rejects_duplicate_slot():
    order = make_order()
    with pytest.raises(ValueError, match="duplicate"):
        PatientUnderstanding(
            order=order,
            slots=[
                SlotUnderstanding(slot=ComprehensionSlot.DRUG, patient_said="paracetamol", correct=True),
                SlotUnderstanding(slot=ComprehensionSlot.DRUG, patient_said="paracetamol", correct=True),
            ],
        )


# --- JSON / dict round-tripping ------------------------------------------------

def test_medication_order_round_trips_through_json():
    order = make_order()
    restored = MedicationOrder.from_json(order.to_json())
    assert restored == order


def test_medication_order_round_trips_through_dict():
    order = make_order()
    restored = MedicationOrder.from_dict(order.to_dict())
    assert restored == order


def test_medication_order_to_dict_is_plain_json_serializable():
    order = make_order()
    # json.dumps must not need a custom encoder: enums/date are already
    # reduced to str in to_dict().
    raw = json.dumps(order.to_dict())
    assert json.loads(raw)["food_relation"] == "after"
    assert json.loads(raw)["followup_date"] == "2026-08-04"


def test_medication_order_with_none_fields_round_trips():
    order = make_order(duration_days=None, followup_date=None)
    restored = MedicationOrder.from_json(order.to_json())
    assert restored == order
    assert restored.duration_days is None
    assert restored.followup_date is None


def test_needs_human_confirmation_round_trips_through_json():
    nhc = NeedsHumanConfirmation(
        field_name="drug_generic",
        candidates=["paracetamol", "acetaminophen"],
        reason="overall confidence 0.40 is below the 0.60 threshold",
        confidence=0.40,
    )
    restored = NeedsHumanConfirmation.from_json(nhc.to_json())
    assert restored == nhc


def test_patient_understanding_round_trips_through_json():
    pu = PatientUnderstanding(
        order=make_order(),
        slots=[
            SlotUnderstanding(slot=ComprehensionSlot.DRUG, patient_said="paracetamol", correct=True),
            SlotUnderstanding(slot=ComprehensionSlot.DOSE, patient_said="two tablets", correct=False, confidence=0.7),
        ],
        attempt=2,
    )
    restored = PatientUnderstanding.from_json(pu.to_json())
    assert restored == pu
    assert restored.all_correct is False
    assert restored.incorrect_slots == [ComprehensionSlot.DOSE]


# --- validate() / confidence threshold -----------------------------------------

def test_validate_returns_confirmed_order_when_confident():
    order = make_order(confidence=Confidence(overall=0.95))
    result = validate(order)
    assert isinstance(result, ConfirmedOrder)
    assert result.order is order


def test_validate_returns_needs_human_confirmation_when_overall_low():
    order = make_order(
        drug_generic="amlodipine",
        confidence=Confidence(overall=0.40),
    )
    result = validate(order)
    assert isinstance(result, NeedsHumanConfirmation)
    assert result.field_name == "drug_generic"
    assert result.candidates == ["amlodipine"]
    assert result.confidence == 0.40


def test_validate_uses_threshold_boundary_inclusively():
    order = make_order(confidence=Confidence(overall=DEFAULT_CONFIDENCE_THRESHOLD))
    # exactly at threshold is confident enough (only *below* refuses)
    result = validate(order)
    assert isinstance(result, ConfirmedOrder)
    assert result.order is order


def test_validate_passes_through_explicit_candidates():
    order = make_order(confidence=Confidence(overall=0.1))
    result = validate(order, candidates=["metformin", "metoprolol", "metronidazole", "extra"])
    assert isinstance(result, NeedsHumanConfirmation)
    assert result.candidates == ["metformin", "metoprolol", "metronidazole"]


def test_validate_flags_worst_low_confidence_field():
    order = make_order(
        confidence=Confidence(
            overall=0.9,
            per_field={"dose_qty": 0.3, "route": 0.5, "drug_generic": 0.95},
        )
    )
    result = validate(order)
    assert isinstance(result, NeedsHumanConfirmation)
    assert result.field_name == "dose_qty"
    assert result.confidence == 0.3


def test_validate_custom_threshold():
    order = make_order(confidence=Confidence(overall=0.5))
    confident_result = validate(order, threshold=0.4)
    assert isinstance(confident_result, ConfirmedOrder)
    assert confident_result.order is order
    assert isinstance(validate(order, threshold=0.6), NeedsHumanConfirmation)


def test_validate_rejects_non_medication_order():
    with pytest.raises(TypeError):
        validate("not an order")


# --- ConfirmedOrder (finding H3) ---------------------------------------------

def test_confirmed_order_cannot_be_constructed_without_the_real_token():
    order = make_order(confidence=Confidence(overall=0.95))
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        ConfirmedOrder(order, _token=object())  # a forged, wrong sentinel


def test_confirmed_order_cannot_be_constructed_with_no_token_argument():
    order = make_order(confidence=Confidence(overall=0.95))
    with pytest.raises(TypeError):
        ConfirmedOrder(order)  # missing the required _token argument entirely


def test_confirmed_order_construction_with_the_real_token_still_validates_its_payload():
    """Even a caller that has legitimately obtained the real token (i.e.
    validate() itself, or a test reaching into it the way this one does)
    still gets structural validation on `order` -- the token only proves
    "this came from the right place", not "skip every other check"."""
    with pytest.raises(TypeError, match="must be a MedicationOrder"):
        ConfirmedOrder("not an order", _token=_CONFIRMED_ORDER_TOKEN)


def test_confirmed_order_constructed_via_the_real_token_succeeds():
    order = make_order(confidence=Confidence(overall=0.95))
    confirmed = ConfirmedOrder(order, _token=_CONFIRMED_ORDER_TOKEN)
    assert confirmed.order is order


def test_confirmed_order_to_dict_matches_the_wrapped_order():
    order = make_order(confidence=Confidence(overall=0.95))
    confirmed = validate(order)
    assert confirmed.to_dict() == order.to_dict()


def test_confirmed_order_to_json_matches_the_wrapped_order():
    order = make_order(confidence=Confidence(overall=0.95))
    confirmed = validate(order)
    assert confirmed.to_json() == order.to_json()


def test_low_confidence_order_never_yields_a_confirmed_order():
    """A NeedsHumanConfirmation result must never carry, wrap, or be
    confused for a ConfirmedOrder -- the whole point of finding H3."""
    order = make_order(confidence=Confidence(overall=0.1))
    result = validate(order)
    assert not isinstance(result, ConfirmedOrder)
    assert isinstance(result, NeedsHumanConfirmation)


def test_low_confidence_field_never_yields_a_confirmed_order():
    order = make_order(
        confidence=Confidence(overall=0.9, per_field={"dose_qty": 0.1})
    )
    result = validate(order)
    assert not isinstance(result, ConfirmedOrder)
    assert isinstance(result, NeedsHumanConfirmation)


def test_confidence_threshold_is_a_single_named_constant_not_a_repeated_literal():
    """The confidence threshold (CLAUDE.md invariant #3) must live in
    exactly one place, DEFAULT_CONFIDENCE_THRESHOLD -- never a bare literal
    copy-pasted into a comparison elsewhere in this module. Parses
    puriyudha/schema.py's own AST (not a text grep, which would also match
    the literal inside a string or comment) and counts every numeric
    constant equal to the threshold's value; there must be exactly one,
    the assignment that defines the constant itself.
    """
    import ast
    import inspect

    source = inspect.getsource(schema_module)
    tree = ast.parse(source)

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, float)
        and node.value == DEFAULT_CONFIDENCE_THRESHOLD
    ]
    assert len(matches) == 1, (
        f"expected exactly one literal {DEFAULT_CONFIDENCE_THRESHOLD!r} in "
        f"puriyudha/schema.py (the DEFAULT_CONFIDENCE_THRESHOLD assignment "
        f"itself), found {len(matches)} -- every other use must reference "
        "the named constant, not repeat the literal"
    )
