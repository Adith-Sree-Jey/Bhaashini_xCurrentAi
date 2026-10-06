"""Metrics plumbing for comparing a pipeline run with the LLM fallback
enabled against one with it disabled.

Part 5.3 of the T4-cut architecture change: with the LLM extractor off the
critical path by default (see :mod:`puriyudha.config`), the measured
slot-level F1 delta between an ``llm_enabled=True`` run and an
``llm_enabled=False`` run over the same evaluation set is a headline
result, not an afterthought -- CLAUDE.md's new invariant is a claim
("the system's core claims must hold with it disabled") that has to be
backed by a number, and this is the shared data shape and comparison
function T7's evaluation harness will consume. T7 itself (running the full
pipeline over a labelled fixture set and producing a report) does not
exist yet; this module is deliberately just the plumbing, added now so T7
does not have to invent it under time pressure later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from puriyudha.schema import ComprehensionSlot


def slot_prf1(true_positives: int, false_positives: int, false_negatives: int) -> Tuple[float, float, float]:
    """Precision, recall, and F1 from raw counts.

    Returns ``0.0`` for any ratio with a zero denominator rather than
    raising ``ZeroDivisionError``: an evaluation run with zero examples for
    a given slot (e.g. a fixture set with no PRN orders) is a real, common
    case, not an error condition.
    """
    if true_positives < 0 or false_positives < 0 or false_negatives < 0:
        raise ValueError(
            f"counts must be >= 0, got tp={true_positives}, fp={false_positives}, "
            f"fn={false_negatives}"
        )
    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else 0.0
    )
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


@dataclass(frozen=True)
class SlotPRF1:
    """Precision / recall / F1 for one comprehension slot's extraction
    correctness across an evaluation run.

    This is about whether the *extracted* MedicationOrder correctly
    populated the slot -- not whether a patient later said it back
    correctly (that is :class:`puriyudha.schema.PatientUnderstanding`'s
    job, a downstream and unrelated measurement).
    """

    slot: ComprehensionSlot
    precision: float
    recall: float
    f1: float

    @classmethod
    def from_counts(
        cls, slot: ComprehensionSlot, true_positives: int, false_positives: int, false_negatives: int
    ) -> "SlotPRF1":
        precision, recall, f1 = slot_prf1(true_positives, false_positives, false_negatives)
        return cls(slot=slot, precision=precision, recall=recall, f1=f1)


@dataclass(frozen=True)
class PipelineRunMetrics:
    """One evaluation run's aggregate result, tagged with whether the LLM
    fallback was enabled for that run -- the two runs T7 will compare."""

    llm_enabled: bool
    per_slot: Dict[ComprehensionSlot, SlotPRF1] = field(default_factory=dict)
    n_orders: int = 0

    @property
    def macro_f1(self) -> float:
        """Unweighted mean F1 across every slot with a scored result --
        the single headline number T7 reports per run. 0.0 for a run with
        no scored slots at all (an empty fixture set), not an error."""
        if not self.per_slot:
            return 0.0
        return sum(s.f1 for s in self.per_slot.values()) / len(self.per_slot)


def llm_enabled_f1_delta(
    with_llm: PipelineRunMetrics, without_llm: PipelineRunMetrics
) -> Dict[str, float]:
    """The headline result for part 5.3: per-slot and macro F1 delta
    between an ``llm_enabled=True`` run and an ``llm_enabled=False`` run
    over the SAME evaluation set.

    Positive means the LLM fallback helped; negative means it hurt --
    both are legitimate, reportable outcomes (CLAUDE.md invariant #2: the
    LLM is never trusted as a source of truth on its own, so a negative
    delta is an expected possibility, not a bug in this function).

    Returns a dict keyed by ``"macro_f1"`` plus each
    :class:`~puriyudha.schema.ComprehensionSlot`'s ``.value`` (e.g.
    ``"drug"``, ``"dose"``) present in either run, each mapped to
    ``with_llm - without_llm`` for that slot's F1 (0.0 for a slot scored in
    only one of the two runs).
    """
    if with_llm.llm_enabled is not True or without_llm.llm_enabled is not False:
        raise ValueError(
            "llm_enabled_f1_delta expects (with_llm.llm_enabled=True, "
            f"without_llm.llm_enabled=False), got "
            f"({with_llm.llm_enabled!r}, {without_llm.llm_enabled!r})"
        )
    delta: Dict[str, float] = {"macro_f1": with_llm.macro_f1 - without_llm.macro_f1}
    all_slots = set(with_llm.per_slot) | set(without_llm.per_slot)
    for slot in all_slots:
        with_f1 = with_llm.per_slot[slot].f1 if slot in with_llm.per_slot else 0.0
        without_f1 = without_llm.per_slot[slot].f1 if slot in without_llm.per_slot else 0.0
        delta[slot.value] = with_f1 - without_f1
    return delta
