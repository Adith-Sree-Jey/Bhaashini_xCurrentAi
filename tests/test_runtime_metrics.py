"""Tests for puriyudha.runtime.metrics -- the slot-level F1 delta plumbing
T7's evaluation harness will use to compare llm_enabled=True vs False runs
(CLAUDE.md invariant #6, part 5.3).
"""
import pytest

from puriyudha.runtime.metrics import (
    PipelineRunMetrics,
    SlotPRF1,
    llm_enabled_f1_delta,
    slot_prf1,
)
from puriyudha.schema import ComprehensionSlot


# --- slot_prf1 ----------------------------------------------------------------

def test_slot_prf1_perfect_score():
    precision, recall, f1 = slot_prf1(true_positives=10, false_positives=0, false_negatives=0)
    assert precision == 1.0
    assert recall == 1.0
    assert f1 == 1.0


def test_slot_prf1_zero_true_positives():
    precision, recall, f1 = slot_prf1(true_positives=0, false_positives=5, false_negatives=5)
    assert precision == 0.0
    assert recall == 0.0
    assert f1 == 0.0


def test_slot_prf1_no_examples_at_all_returns_zeros_not_an_error():
    precision, recall, f1 = slot_prf1(true_positives=0, false_positives=0, false_negatives=0)
    assert (precision, recall, f1) == (0.0, 0.0, 0.0)


def test_slot_prf1_known_values():
    # tp=3, fp=1 -> precision=0.75; tp=3, fn=2 -> recall=0.6
    precision, recall, f1 = slot_prf1(true_positives=3, false_positives=1, false_negatives=2)
    assert precision == pytest.approx(0.75)
    assert recall == pytest.approx(0.6)
    assert f1 == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))


def test_slot_prf1_rejects_negative_counts():
    with pytest.raises(ValueError):
        slot_prf1(true_positives=-1, false_positives=0, false_negatives=0)


# --- SlotPRF1 -------------------------------------------------------------------

def test_slot_prf1_from_counts_matches_the_bare_function():
    obj = SlotPRF1.from_counts(ComprehensionSlot.DOSE, true_positives=3, false_positives=1, false_negatives=2)
    precision, recall, f1 = slot_prf1(3, 1, 2)
    assert obj.slot == ComprehensionSlot.DOSE
    assert obj.precision == precision
    assert obj.recall == recall
    assert obj.f1 == f1


# --- PipelineRunMetrics.macro_f1 -----------------------------------------------

def test_macro_f1_with_no_slots_is_zero():
    metrics = PipelineRunMetrics(llm_enabled=False)
    assert metrics.macro_f1 == 0.0


def test_macro_f1_is_unweighted_mean_across_slots():
    metrics = PipelineRunMetrics(
        llm_enabled=True,
        per_slot={
            ComprehensionSlot.DRUG: SlotPRF1(ComprehensionSlot.DRUG, 1.0, 1.0, 1.0),
            ComprehensionSlot.DOSE: SlotPRF1(ComprehensionSlot.DOSE, 0.0, 0.0, 0.0),
        },
    )
    assert metrics.macro_f1 == pytest.approx(0.5)


# --- llm_enabled_f1_delta -------------------------------------------------------

def test_delta_requires_correct_llm_enabled_flags():
    with_llm = PipelineRunMetrics(llm_enabled=True)
    without_llm = PipelineRunMetrics(llm_enabled=False)
    llm_enabled_f1_delta(with_llm, without_llm)  # no raise

    with pytest.raises(ValueError):
        llm_enabled_f1_delta(without_llm, with_llm)  # swapped
    with pytest.raises(ValueError):
        llm_enabled_f1_delta(with_llm, with_llm)  # both True


def test_delta_reports_positive_when_llm_helps():
    with_llm = PipelineRunMetrics(
        llm_enabled=True,
        per_slot={ComprehensionSlot.DRUG: SlotPRF1(ComprehensionSlot.DRUG, 1.0, 1.0, 1.0)},
    )
    without_llm = PipelineRunMetrics(
        llm_enabled=False,
        per_slot={ComprehensionSlot.DRUG: SlotPRF1(ComprehensionSlot.DRUG, 0.5, 0.5, 0.5)},
    )
    delta = llm_enabled_f1_delta(with_llm, without_llm)
    assert delta["drug"] == pytest.approx(0.5)
    assert delta["macro_f1"] == pytest.approx(0.5)


def test_delta_reports_negative_when_llm_hurts():
    """CLAUDE.md invariant #2: the LLM is never trusted as a source of
    truth on its own -- a negative delta is a legitimate outcome, not a
    bug in this function."""
    with_llm = PipelineRunMetrics(
        llm_enabled=True,
        per_slot={ComprehensionSlot.DRUG: SlotPRF1(ComprehensionSlot.DRUG, 0.4, 0.4, 0.4)},
    )
    without_llm = PipelineRunMetrics(
        llm_enabled=False,
        per_slot={ComprehensionSlot.DRUG: SlotPRF1(ComprehensionSlot.DRUG, 0.9, 0.9, 0.9)},
    )
    delta = llm_enabled_f1_delta(with_llm, without_llm)
    assert delta["drug"] == pytest.approx(-0.5)


def test_delta_handles_a_slot_scored_in_only_one_run():
    with_llm = PipelineRunMetrics(
        llm_enabled=True,
        per_slot={ComprehensionSlot.CAUTIONS: SlotPRF1(ComprehensionSlot.CAUTIONS, 0.8, 0.8, 0.8)},
    )
    without_llm = PipelineRunMetrics(llm_enabled=False, per_slot={})
    delta = llm_enabled_f1_delta(with_llm, without_llm)
    assert delta["cautions"] == pytest.approx(0.8)
