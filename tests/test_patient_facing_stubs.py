"""Proves finding H3's fix holds for every current patient-facing stub:
render/ and issue/ (CLAUDE.md: "anything that produces speech, a screen
frame, or a QR payload") take a ConfirmedOrder, never a bare
MedicationOrder, and reject a bare MedicationOrder loudly at runtime rather
than silently accepting it.

Every function under test here is a T5+ stub that only ever raises
NotImplementedError once past its own guard -- these tests exist to prove
the guard runs (and runs FIRST, before the NotImplementedError), not to
test any actual rendering/issuing behaviour, which does not exist yet.
"""
import inspect
import typing

import pytest

import puriyudha.issue as issue_module
import puriyudha.render as render_module
from puriyudha.schema import (
    Confidence,
    ConfirmedOrder,
    FoodRelation,
    Frequency,
    MedicationOrder,
    Slot,
    Source,
    validate,
)

PATIENT_FACING_FUNCTIONS = [
    (render_module.render_explanation, {"language": "ta"}),
    (render_module.render_screen_frame, {"language": "hi"}),
    (issue_module.issue_qr_payload, {}),
    (issue_module.issue_audio_artifact, {"language": "ta"}),
]


def make_confirmed_order() -> ConfirmedOrder:
    order = MedicationOrder(
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
        cautions=[],
        followup_date=None,
        source=Source.SIG_PARSER,
        confidence=Confidence(overall=0.95),
    )
    result = validate(order)
    assert isinstance(result, ConfirmedOrder)  # sanity: this helper must actually confirm
    return result


@pytest.mark.parametrize("fn, extra_kwargs", PATIENT_FACING_FUNCTIONS, ids=lambda v: getattr(v, "__name__", str(v)))
def test_patient_facing_stub_takes_confirmed_order_in_its_signature(fn, extra_kwargs):
    """Static check: the first parameter must be annotated ConfirmedOrder,
    not MedicationOrder or unannotated -- this is the "in code" half of
    CLAUDE.md's rule (the runtime guard below is the other half).

    Uses typing.get_type_hints (not raw inspect.signature annotations)
    because render/__init__.py and issue/__init__.py both use `from
    __future__ import annotations`, under which every annotation is a
    string at runtime until resolved -- get_type_hints does that
    resolution, so this compares against the real ConfirmedOrder class,
    not the string "ConfirmedOrder"."""
    params = list(inspect.signature(fn).parameters.values())
    assert params, f"{fn.__qualname__} takes no parameters at all"
    first_name = params[0].name
    hints = typing.get_type_hints(fn)
    assert hints.get(first_name) is ConfirmedOrder, (
        f"{fn.__qualname__}'s first parameter ({first_name!r}) must be annotated "
        f"ConfirmedOrder, got {hints.get(first_name)!r}"
    )


@pytest.mark.parametrize("fn, extra_kwargs", PATIENT_FACING_FUNCTIONS, ids=lambda v: getattr(v, "__name__", str(v)))
def test_patient_facing_stub_rejects_a_bare_medication_order(fn, extra_kwargs):
    """A bare MedicationOrder (never passed through validate()) must be
    rejected with a TypeError naming the problem -- not silently accepted,
    and not failing with some unrelated AttributeError deep inside the
    stub's own (not-yet-implemented) logic."""
    bare_order = MedicationOrder(
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
        cautions=[],
        followup_date=None,
        source=Source.SIG_PARSER,
        confidence=Confidence(overall=0.95),  # high confidence, still a bare order
    )
    with pytest.raises(TypeError, match="ConfirmedOrder"):
        fn(bare_order, **extra_kwargs)


@pytest.mark.parametrize("fn, extra_kwargs", PATIENT_FACING_FUNCTIONS, ids=lambda v: getattr(v, "__name__", str(v)))
def test_patient_facing_stub_rejects_arbitrary_non_order_values(fn, extra_kwargs):
    for bad_value in (None, "a string", 42, {"drug_generic": "paracetamol"}):
        with pytest.raises(TypeError, match="ConfirmedOrder"):
            fn(bad_value, **extra_kwargs)


@pytest.mark.parametrize("fn, extra_kwargs", PATIENT_FACING_FUNCTIONS, ids=lambda v: getattr(v, "__name__", str(v)))
def test_patient_facing_stub_accepts_a_real_confirmed_order_then_raises_not_implemented(fn, extra_kwargs):
    """A genuine ConfirmedOrder clears the guard -- these stubs are not
    yet implemented, so they raise NotImplementedError next, but they must
    get PAST the type guard first (proving the guard doesn't reject
    everything indiscriminately)."""
    confirmed = make_confirmed_order()
    with pytest.raises(NotImplementedError):
        fn(confirmed, **extra_kwargs)
