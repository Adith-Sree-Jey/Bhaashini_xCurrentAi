"""The formulary's own record type: `FormularyDrug`.

Distinct from `puriyudha.schema.MedicationOrder` (one patient's dosing
instruction) -- this is one drug's entry in the offline reference data:
what it is, what forms/strengths exist, what a standard adult oral dose
looks like, and what it's called in Tamil and Hindi. CLAUDE.md invariant
#2: drug identity, strength, dose range, and form always come from here,
never from the language model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class FormularyDrug:
    """One drug's formulary entry.

    Dosing fields (`dose_unit`, `min_adult_dose`, `max_adult_dose`,
    `max_daily_dose`) describe a STANDARD, commonly-cited adult oral dosing
    range for routine outpatient use -- the kind of range found in a
    national formulary or the WHO Model List of Essential Medicines. This
    is a safety *reference range* for cross-checking what OCR/ASR/the LLM
    fallback extracted (CLAUDE.md invariant #3: "refuse rather than
    guess" -- a dose outside this range is exactly the kind of thing that
    should trigger `NeedsHumanConfirmation`, not silent accept-or-reject).
    It is NOT a prescribing recommendation, does not cover pediatric,
    renal-impairment, hepatic-impairment, or pregnancy dosing, and does not
    replace the actual prescriber's instruction, which always comes from
    the sig parser or OCR/ASR capture -- never from this table.

    `dose_unit` is one of "mg", "mcg", or "tablet": most drugs here dose in
    mg (or mcg for a few), but a handful of fixed-dose combination /
    supplement products (multivitamins, calcium+vitamin D, iron+folic
    acid) do not have one clinically standard active-ingredient dose the
    way a single-ingredient drug does, and are dosed in whole tablets/caps
    instead -- forcing those into an mg range would be false precision.
    """

    #: Canonical matching key: lowercase, e.g. "paracetamol". Every other
    #: name (brand names, Tamil/Hindi transliterations) is an alias FOR
    #: this, never a replacement for it -- see `puriyudha.formulary.matcher`.
    generic_name: str
    #: Proper-case display form, e.g. "Paracetamol".
    display_name: str
    #: Tamil-script transliteration of the generic name, for patient-facing
    #: display (CLAUDE.md language scope: "ta"). Phonetic transliteration,
    #: not a translation -- there is usually no Tamil *word* for a generic
    #: drug name, only a rendering of how it's said.
    tamil_name: str
    #: Devanagari-script (Hindi) transliteration, same rationale.
    hindi_name: str
    #: Broad therapeutic category, e.g. "analgesic-antipyretic". Free text,
    #: not an enum -- used for display/grouping, never for dosing logic.
    category: str
    #: Every form this drug is commonly dispensed in, in India, for
    #: outpatient use. At least one entry, e.g. ("tablet",).
    forms: Tuple[str, ...]
    #: Commonly available strengths as they'd appear on a strip/label,
    #: e.g. ("500 mg", "650 mg"). Free text (mixed units/concentrations
    #: for syrups, e.g. "125 mg/5 ml"), not necessarily reducible to a
    #: single number -- unlike `min_adult_dose`/`max_adult_dose`, which
    #: are always in `dose_unit`.
    common_strengths: Tuple[str, ...]
    #: "mg", "mcg", or "tablet" -- see class docstring.
    dose_unit: str
    #: Standard adult single-dose minimum, in `dose_unit`.
    min_adult_dose: float
    #: Standard adult single-dose maximum, in `dose_unit`.
    max_adult_dose: float
    #: Standard adult daily ceiling, in `dose_unit`. `None` when there is
    #: no single commonly-cited daily maximum (e.g. a drug that is always
    #: dosed once daily, where the per-dose max already *is* the daily
    #: max, or a drug whose maintenance dose is individually titrated).
    max_daily_dose: Optional[float]
    #: How many times a day this is typically taken, e.g. (1,) for a
    #: once-daily drug, (3, 4) for a drug commonly given three OR four
    #: times a day. Not exhaustive -- a real prescription can deviate;
    #: this is what to expect, not a hard rule.
    typical_frequency_per_day: Tuple[int, ...]
    #: Route of administration. "oral" for every entry in the part-6 seed
    #: set (CLAUDE.md: outpatient / discharge-counter context), kept as a
    #: field (not a hardcoded assumption elsewhere) because it will not
    #: stay true once the formulary grows past oral solids.
    route: str
    #: A handful of well-known Indian brand names for this generic, for
    #: the fuzzy matcher (a pharmacist or patient is far more likely to
    #: say/write a brand name than the generic). Deliberately short and
    #: only includes brands we're confident are correct and current --
    #: better an empty tuple than a wrong brand mapping.
    common_brand_names: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.generic_name or self.generic_name != self.generic_name.strip().lower():
            raise ValueError(
                f"generic_name must be non-empty, trimmed, and lowercase, got {self.generic_name!r}"
            )
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.tamil_name.strip():
            raise ValueError(f"{self.generic_name}: tamil_name must not be empty")
        if not self.hindi_name.strip():
            raise ValueError(f"{self.generic_name}: hindi_name must not be empty")
        if not self.forms:
            raise ValueError(f"{self.generic_name}: forms must not be empty")
        if not self.common_strengths:
            raise ValueError(f"{self.generic_name}: common_strengths must not be empty")
        if self.dose_unit not in ("mg", "mcg", "tablet"):
            raise ValueError(
                f"{self.generic_name}: dose_unit must be 'mg', 'mcg', or 'tablet', got {self.dose_unit!r}"
            )
        if self.min_adult_dose <= 0:
            raise ValueError(f"{self.generic_name}: min_adult_dose must be > 0, got {self.min_adult_dose}")
        if self.max_adult_dose < self.min_adult_dose:
            raise ValueError(
                f"{self.generic_name}: max_adult_dose ({self.max_adult_dose}) must be >= "
                f"min_adult_dose ({self.min_adult_dose})"
            )
        if self.max_daily_dose is not None and self.max_daily_dose < self.max_adult_dose:
            raise ValueError(
                f"{self.generic_name}: max_daily_dose ({self.max_daily_dose}) must be >= "
                f"max_adult_dose ({self.max_adult_dose})"
            )
        if not self.typical_frequency_per_day:
            raise ValueError(f"{self.generic_name}: typical_frequency_per_day must not be empty")
        if any(f < 1 for f in self.typical_frequency_per_day):
            raise ValueError(f"{self.generic_name}: typical_frequency_per_day entries must be >= 1")
        if not self.route.strip():
            raise ValueError(f"{self.generic_name}: route must not be empty")

    def dose_in_range(self, dose: float) -> bool:
        """True if `dose` (in this drug's `dose_unit`) falls within the
        standard adult single-dose range -- the sanity check CLAUDE.md
        invariant #2/#3 describe: a dose outside this range is a signal
        for `NeedsHumanConfirmation`, not a silent pass-through."""
        return self.min_adult_dose <= dose <= self.max_adult_dose

    def all_names(self) -> Tuple[str, ...]:
        """Every name this drug could plausibly be matched against:
        generic, display, and brand names (English/Latin script only --
        Tamil/Hindi names are for display, not input matching; see
        `puriyudha.formulary.matcher`)."""
        return (self.generic_name, self.display_name, *self.common_brand_names)
