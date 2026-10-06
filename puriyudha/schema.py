"""The validated structured medication record and its supporting types.

This is the contract between everything upstream of it (formulary, sig
parser, LLM fallback, vision/OCR) and everything downstream (render,
verify, issue): a :class:`MedicationOrder` is the single source of truth
handed to the patient, and a :class:`PatientUnderstanding` is how correctly
they said it back.

Two validation layers, deliberately kept separate:

- **Structural validity** (out-of-range values, wrong types, bad enum
  members) is enforced in every dataclass's ``__post_init__`` and raises
  ``ValueError`` immediately. That kind of failure means a bug in whatever
  produced the data, not a legitimate real-world case -- it should never be
  silently swallowed.
- **Confidence** (are we sure enough about this otherwise well-formed
  record to act on it) is handled by :func:`validate`, which returns a
  :class:`NeedsHumanConfirmation` instead of the order when confidence is
  below threshold. This is CLAUDE.md invariant #3, "Refuse rather than
  guess": refusal is a reported metric, not a failure, so it is a normal
  return value here, never an exception.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

__all__ = [
    "Slot",
    "FoodRelation",
    "Source",
    "ComprehensionSlot",
    "Frequency",
    "Confidence",
    "MedicationOrder",
    "ConfirmedOrder",
    "require_confirmed_order",
    "NeedsHumanConfirmation",
    "SlotUnderstanding",
    "PatientUnderstanding",
    "validate",
    "DEFAULT_CONFIDENCE_THRESHOLD",
]


# --- Enums ------------------------------------------------------------------

class Slot(str, Enum):
    """A named time-of-day dosing slot."""

    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"
    NIGHT = "night"


class FoodRelation(str, Enum):
    """When a dose is taken relative to food."""

    BEFORE = "before"
    AFTER = "after"
    WITH = "with"
    ANY = "any"


class Source(str, Enum):
    """Where a MedicationOrder's data primarily came from.

    Mirrors CLAUDE.md invariant #2: the rules parser (sig) runs first, the
    LLM extractor only fills gaps it could not, and OCR/manual entry are the
    other two ways a record gets created.
    """

    SIG_PARSER = "sig_parser"
    LLM_FALLBACK = "llm_fallback"
    OCR = "ocr"
    MANUAL = "manual"


class ComprehensionSlot(str, Enum):
    """The parts of a MedicationOrder the patient is asked to say back."""

    DRUG = "drug"
    DOSE = "dose"
    FREQUENCY = "frequency"
    FOOD_RELATION = "food_relation"
    DURATION = "duration"
    CAUTIONS = "cautions"


# --- Bounds (see docstrings on the fields that use them for rationale) ------

MIN_DOSE_QTY = 0.0  # exclusive: zero or negative doses are never valid
MAX_DOSE_QTY = 50.0  # generous ceiling (e.g. mL of a syrup); not a clinical limit
MIN_TIMES_PER_DAY = 1
MAX_TIMES_PER_DAY = 6
MAX_DURATION_DAYS = 365

#: Below this overall or per-field confidence, validate() refuses instead
#: of returning the order (CLAUDE.md invariant #3).
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

#: NeedsHumanConfirmation never names more than this many candidates
#: (CLAUDE.md invariant #3: "naming the top-3 candidates").
MAX_CANDIDATES = 3


# --- Coercion / validation helpers -------------------------------------------

def _non_empty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name!r} must be a non-empty string, got {value!r}")
    return value.strip()


def _is_strict_int(value) -> bool:
    # bool is a subclass of int in Python; reject it explicitly so
    # `prn=True` can never be mistaken for `times_per_day=1`.
    return isinstance(value, int) and not isinstance(value, bool)


def _coerce_enum(value, enum_cls, field_name: str):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = ", ".join(m.value for m in enum_cls)
        raise ValueError(f"{field_name!r} must be one of [{valid}], got {value!r}") from exc


def _coerce_date(value, field_name: str) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{field_name!r} must be an ISO date (YYYY-MM-DD), got {value!r}"
            ) from exc
    raise ValueError(f"{field_name!r} must be a date or ISO date string, got {value!r}")


def _validate_unit_interval(value, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    value = float(value)
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"{field_name} must be between 0.0 and 1.0, got {value}")
    return value


# --- Frequency ----------------------------------------------------------------

@dataclass
class Frequency:
    """How many times a day a dose is taken, and (optionally) which named
    slots those times fall in.

    ``slots`` may be left empty (e.g. "three times a day" with no specific
    named times), but if given, it must have exactly ``times_per_day``
    entries with no repeats -- a slot list is a *schedule*, not a bag.
    """

    times_per_day: int
    slots: List[Slot] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not _is_strict_int(self.times_per_day):
            raise ValueError(f"times_per_day must be an int, got {self.times_per_day!r}")
        if not (MIN_TIMES_PER_DAY <= self.times_per_day <= MAX_TIMES_PER_DAY):
            raise ValueError(
                f"times_per_day must be between {MIN_TIMES_PER_DAY} and "
                f"{MAX_TIMES_PER_DAY}, got {self.times_per_day}"
            )

        self.slots = [_coerce_enum(s, Slot, "frequency.slots[]") for s in self.slots]
        if len(set(self.slots)) != len(self.slots):
            raise ValueError(f"frequency.slots must not repeat a slot, got {self.slots}")
        if self.slots and len(self.slots) != self.times_per_day:
            raise ValueError(
                f"frequency.slots has {len(self.slots)} entries but "
                f"times_per_day is {self.times_per_day}; they must match "
                "when slots are given"
            )

    def to_dict(self) -> dict:
        return {"times_per_day": self.times_per_day, "slots": [s.value for s in self.slots]}

    @classmethod
    def from_dict(cls, data: dict) -> "Frequency":
        return cls(**data)


# --- Confidence -----------------------------------------------------------------

@dataclass
class Confidence:
    """Overall confidence in a record, plus an optional per-field breakdown.

    Both are on a 0.0-1.0 scale. ``per_field`` keys are expected to be
    ``MedicationOrder`` field names, but this is not enforced here -- callers
    are free to score whichever fields they actually produced a confidence
    for.
    """

    overall: float
    per_field: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.overall = _validate_unit_interval(self.overall, "confidence.overall")
        self.per_field = {
            str(name): _validate_unit_interval(value, f"confidence.per_field[{name!r}]")
            for name, value in self.per_field.items()
        }

    def to_dict(self) -> dict:
        return {"overall": self.overall, "per_field": dict(self.per_field)}

    @classmethod
    def from_dict(cls, data: dict) -> "Confidence":
        return cls(**data)


# --- MedicationOrder --------------------------------------------------------

@dataclass
class MedicationOrder:
    """A single validated, structured medication instruction.

    Every field is required (even the nullable ones must be passed
    explicitly as ``None``): medical data should never pick up a silent
    default. Drug identity, strength, dose, and form are expected to have
    already been resolved against the offline formulary before a
    MedicationOrder is constructed (CLAUDE.md invariant #2) -- this class
    only checks that the *shape* of the data is sane, not that the drug
    actually exists.
    """

    drug_generic: str
    drug_display: str
    strength: str
    form: str
    dose_qty: float
    route: str
    frequency: Frequency
    food_relation: FoodRelation
    duration_days: Optional[int]
    prn: bool
    cautions: List[str]
    followup_date: Optional[date]
    source: Source
    confidence: Confidence

    def __post_init__(self) -> None:
        self.drug_generic = _non_empty_str(self.drug_generic, "drug_generic")
        self.drug_display = _non_empty_str(self.drug_display, "drug_display")
        self.strength = _non_empty_str(self.strength, "strength")
        self.form = _non_empty_str(self.form, "form")
        self.route = _non_empty_str(self.route, "route")

        if isinstance(self.dose_qty, bool) or not isinstance(self.dose_qty, (int, float)):
            raise ValueError(f"dose_qty must be a number, got {self.dose_qty!r}")
        self.dose_qty = float(self.dose_qty)
        if not (MIN_DOSE_QTY < self.dose_qty <= MAX_DOSE_QTY):
            raise ValueError(
                f"dose_qty must be > {MIN_DOSE_QTY} and <= {MAX_DOSE_QTY}, got {self.dose_qty}"
            )

        if isinstance(self.frequency, dict):
            self.frequency = Frequency.from_dict(self.frequency)
        elif not isinstance(self.frequency, Frequency):
            raise ValueError(f"frequency must be a Frequency, got {self.frequency!r}")

        self.food_relation = _coerce_enum(self.food_relation, FoodRelation, "food_relation")
        if self.duration_days is not None:
            if not _is_strict_int(self.duration_days):
                raise ValueError(
                    f"duration_days must be an int or None, got {self.duration_days!r}"
                )
            if not (1 <= self.duration_days <= MAX_DURATION_DAYS):
                raise ValueError(
                    f"duration_days must be between 1 and {MAX_DURATION_DAYS}, "
                    f"got {self.duration_days}"
                )

        if not isinstance(self.prn, bool):
            raise ValueError(f"prn must be a bool, got {self.prn!r}")

        self.cautions = [_non_empty_str(c, "cautions[]") for c in self.cautions]

        self.followup_date = _coerce_date(self.followup_date, "followup_date")

        self.source = _coerce_enum(self.source, Source, "source")

        if isinstance(self.confidence, dict):
            self.confidence = Confidence.from_dict(self.confidence)
        elif not isinstance(self.confidence, Confidence):
            raise ValueError(f"confidence must be a Confidence, got {self.confidence!r}")

    def to_dict(self) -> dict:
        return {
            "drug_generic": self.drug_generic,
            "drug_display": self.drug_display,
            "strength": self.strength,
            "form": self.form,
            "dose_qty": self.dose_qty,
            "route": self.route,
            "frequency": self.frequency.to_dict(),
            "food_relation": self.food_relation.value,
            "duration_days": self.duration_days,
            "prn": self.prn,
            "cautions": list(self.cautions),
            "followup_date": self.followup_date.isoformat() if self.followup_date else None,
            "source": self.source.value,
            "confidence": self.confidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MedicationOrder":
        return cls(**data)

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, raw: str) -> "MedicationOrder":
        return cls.from_dict(json.loads(raw))


# --- NeedsHumanConfirmation ---------------------------------------------------

@dataclass
class NeedsHumanConfirmation:
    """The "refuse rather than guess" result (CLAUDE.md invariant #3).

    Returned by :func:`validate` instead of a :class:`MedicationOrder` when
    confidence is below threshold, and reusable by any other module (e.g.
    the formulary fuzzy matcher) that needs to name a short list of
    candidates instead of committing to one. Never names more than the
    top-3 candidates: the system must never speak a drug name it is not
    confident about, and offering an unbounded list is just guessing with
    extra steps.
    """

    field_name: str
    candidates: List[str]
    reason: str
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        self.field_name = _non_empty_str(self.field_name, "field_name")
        self.reason = _non_empty_str(self.reason, "reason")
        if not isinstance(self.candidates, list) or not all(
            isinstance(c, str) for c in self.candidates
        ):
            raise ValueError(f"candidates must be a list of strings, got {self.candidates!r}")
        if len(self.candidates) > MAX_CANDIDATES:
            raise ValueError(
                f"candidates must name at most the top {MAX_CANDIDATES} "
                f"(CLAUDE.md), got {len(self.candidates)}: {self.candidates}"
            )
        if self.confidence is not None:
            self.confidence = _validate_unit_interval(self.confidence, "confidence")

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NeedsHumanConfirmation":
        return cls(**data)

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, raw: str) -> "NeedsHumanConfirmation":
        return cls.from_dict(json.loads(raw))


# --- PatientUnderstanding -------------------------------------------------------

@dataclass
class SlotUnderstanding:
    """Whether the patient correctly said back one comprehension slot."""

    slot: ComprehensionSlot
    patient_said: str
    correct: bool
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.slot = _coerce_enum(self.slot, ComprehensionSlot, "slot")
        if not isinstance(self.patient_said, str):
            raise ValueError(f"patient_said must be a string, got {self.patient_said!r}")
        if not isinstance(self.correct, bool):
            raise ValueError(f"correct must be a bool, got {self.correct!r}")
        self.confidence = _validate_unit_interval(self.confidence, "slot.confidence")

    def to_dict(self) -> dict:
        return {
            "slot": self.slot.value,
            "patient_said": self.patient_said,
            "correct": self.correct,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SlotUnderstanding":
        return cls(**data)


@dataclass
class PatientUnderstanding:
    """The result of one round of "say it back" for a MedicationOrder.

    Scores understanding slot by slot (CLAUDE.md: "asks the patient to say
    it back, scores understanding slot by slot"), so the caller
    (:mod:`puriyudha.verify`) knows exactly which slots to re-explain.
    ``attempt`` counts re-explanation rounds, starting at 1.
    """

    order: MedicationOrder
    slots: List[SlotUnderstanding]
    attempt: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.order, dict):
            self.order = MedicationOrder.from_dict(self.order)
        elif not isinstance(self.order, MedicationOrder):
            raise ValueError(f"order must be a MedicationOrder, got {self.order!r}")

        self.slots = [
            s if isinstance(s, SlotUnderstanding) else SlotUnderstanding.from_dict(s)
            for s in self.slots
        ]
        if not self.slots:
            raise ValueError("PatientUnderstanding.slots must not be empty")
        seen = set()
        for s in self.slots:
            if s.slot in seen:
                raise ValueError(
                    f"duplicate ComprehensionSlot {s.slot.value!r} in "
                    "PatientUnderstanding.slots"
                )
            seen.add(s.slot)

        if not _is_strict_int(self.attempt) or self.attempt < 1:
            raise ValueError(f"attempt must be a positive int, got {self.attempt!r}")

    @property
    def all_correct(self) -> bool:
        return all(s.correct for s in self.slots)

    @property
    def incorrect_slots(self) -> List[ComprehensionSlot]:
        return [s.slot for s in self.slots if not s.correct]

    def to_dict(self) -> dict:
        return {
            "order": self.order.to_dict(),
            "slots": [s.to_dict() for s in self.slots],
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PatientUnderstanding":
        return cls(**data)

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, raw: str) -> "PatientUnderstanding":
        return cls.from_dict(json.loads(raw))


# --- ConfirmedOrder ---------------------------------------------------------
#
# Finding H3 from the verification pass: NeedsHumanConfirmation was
# returned by validate() but nothing consumed it, so a low-confidence
# MedicationOrder could still reach a patient-facing function by accident --
# harmless only because downstream modules were stubs. This is fixed with
# the type system, not a docstring convention: ConfirmedOrder is a distinct
# type, and every patient-facing function signature (CLAUDE.md: anything in
# render/, issue/, or anything that produces speech, a screen frame, or a
# QR payload) takes a ConfirmedOrder, never a bare MedicationOrder.


class _ConfirmedOrderToken:
    """An opaque, module-private sentinel. Its only purpose is to be an
    object nothing outside this module has a legitimate reason to hold a
    reference to -- see ConfirmedOrder.__post_init__."""


#: The one instance of the token above. Not exported (absent from
#: __all__), and named with a leading underscore, so importing it is a
#: deliberate, visible act of reaching into puriyudha.schema's internals --
#: not something that happens by writing ConfirmedOrder(order) normally.
_CONFIRMED_ORDER_TOKEN = _ConfirmedOrderToken()


@dataclass(frozen=True)
class ConfirmedOrder:
    """A MedicationOrder that has cleared validate()'s confidence check
    (CLAUDE.md invariant #3, "Refuse rather than guess") and is therefore
    safe to show or speak to a patient.

    **Enforcement mechanism (private constructor token):** the constructor
    requires a second argument, `_token`, and raises `TypeError` unless it
    is passed the exact sentinel object `_CONFIRMED_ORDER_TOKEN` defined
    just above this class -- which is module-private, not exported, and
    not accessible without deliberately importing a leading-underscore name
    out of `puriyudha.schema`. This does not make constructing one from
    arbitrary code *impossible* (nothing short of a C extension type could
    promise that in Python), but it makes doing so obviously deliberate and
    out-of-band, never something that happens by accident, by habit, or by
    copying the pattern of any other dataclass in this file. The only
    legitimate caller is `validate()`, in this same module.

    Deliberately NOT round-trippable through JSON the way MedicationOrder
    is (no `from_dict`/`from_json`): a ConfirmedOrder should only ever be
    born from a live `validate()` call in the same process, never
    reconstructed from stored or transmitted data, which would defeat the
    whole point of the type.
    """

    order: MedicationOrder
    _token: "_ConfirmedOrderToken" = field(repr=False)

    def __post_init__(self) -> None:
        if self._token is not _CONFIRMED_ORDER_TOKEN:
            raise TypeError(
                "ConfirmedOrder cannot be constructed directly -- it is only "
                "returned by puriyudha.schema.validate() after a "
                "MedicationOrder clears the confidence threshold. If you "
                "have a MedicationOrder, call validate(order) instead of "
                "constructing ConfirmedOrder yourself."
            )
        if not isinstance(self.order, MedicationOrder):
            raise TypeError(f"ConfirmedOrder.order must be a MedicationOrder, got {self.order!r}")

    def to_dict(self) -> dict:
        return self.order.to_dict()

    def to_json(self, **kwargs) -> str:
        return self.order.to_json(**kwargs)


def require_confirmed_order(value, *, caller: str = "") -> ConfirmedOrder:
    """Runtime guard for every patient-facing function (CLAUDE.md: anything
    in ``render/``, ``issue/``, or anything that produces speech, a screen
    frame, or a QR payload).

    A type hint of ``order: ConfirmedOrder`` on a function signature is not
    enforced by Python at runtime -- nothing stops a caller from passing a
    bare ``MedicationOrder`` anyway. This closes that gap: call it as the
    first line of every patient-facing function. Raises ``TypeError``
    naming exactly what was passed (and, if given, which function rejected
    it) rather than letting a bare ``MedicationOrder`` silently reach
    patient-facing code. Returns ``value`` unchanged (so it can be used
    inline: ``order = require_confirmed_order(order, caller=...)``) when it
    already is a ``ConfirmedOrder``.
    """
    if not isinstance(value, ConfirmedOrder):
        where = f" in {caller}" if caller else ""
        raise TypeError(
            f"expected a ConfirmedOrder{where}, got {type(value).__name__}. "
            "Patient-facing functions must never accept a bare MedicationOrder "
            "-- call puriyudha.schema.validate() first (see CLAUDE.md)."
        )
    return value


# --- validate() -----------------------------------------------------------------

def validate(
    order: MedicationOrder,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    candidates: Optional[List[str]] = None,
):
    """Apply CLAUDE.md invariant #3 ("Refuse rather than guess") to an
    already-constructed, structurally-valid MedicationOrder.

    Structural problems (out-of-range values, bad enum members, wrong
    types) are a bug in whatever produced ``order`` and already raised
    ``ValueError`` from its own ``__post_init__`` -- ``validate`` never
    re-checks that. What it checks is whether we are *confident* enough in
    an otherwise well-formed record to actually act on it: if the overall
    confidence, or any individual field's confidence, is below
    ``threshold``, this returns a :class:`NeedsHumanConfirmation` instead of
    a :class:`ConfirmedOrder`. Refusal is a reported metric, not a failure.

    ``candidates`` lets a caller who already has a ranked list of
    alternatives (e.g. the formulary fuzzy matcher's top matches) pass them
    through to the returned NeedsHumanConfirmation; only the first
    ``MAX_CANDIDATES`` are kept. Without it, the order's own
    ``drug_generic`` is used as the sole candidate for an overall-confidence
    refusal, and no candidates are offered for a single low-confidence
    field.

    Returns a :class:`ConfirmedOrder` wrapping ``order`` unchanged when
    confidence clears the threshold everywhere -- this is the ONLY place in
    puriyudha that constructs one (see ConfirmedOrder's docstring for the
    enforcement mechanism), which is precisely the point: every
    patient-facing function downstream requires a ConfirmedOrder, so a
    low-confidence (or never-validated) MedicationOrder is structurally
    unable to reach a patient (finding H3, verification pass).
    """
    if not isinstance(order, MedicationOrder):
        raise TypeError(f"validate() expects a MedicationOrder, got {type(order)!r}")

    if order.confidence.overall < threshold:
        return NeedsHumanConfirmation(
            field_name="drug_generic",
            candidates=(candidates if candidates is not None else [order.drug_generic])[
                :MAX_CANDIDATES
            ],
            reason=(
                f"overall confidence {order.confidence.overall:.2f} is below "
                f"the {threshold:.2f} threshold"
            ),
            confidence=order.confidence.overall,
        )

    low_confidence_fields = sorted(
        (name for name, c in order.confidence.per_field.items() if c < threshold),
        key=lambda name: order.confidence.per_field[name],
    )
    if low_confidence_fields:
        worst = low_confidence_fields[0]
        return NeedsHumanConfirmation(
            field_name=worst,
            candidates=(candidates or [])[:MAX_CANDIDATES],
            reason=(
                f"field {worst!r} confidence "
                f"{order.confidence.per_field[worst]:.2f} is below the "
                f"{threshold:.2f} threshold"
            ),
            confidence=order.confidence.per_field[worst],
        )

    return ConfirmedOrder(order, _token=_CONFIRMED_ORDER_TOKEN)
