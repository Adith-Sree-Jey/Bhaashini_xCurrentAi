"""Rules-based prescription shorthand ("sig") parser.

CLAUDE.md invariant #6: with the LLM fallback extractor cut from the
critical path, this module is now the ONLY path from a clinician's spoken
or written dosing shorthand to a structured schedule. Its coverage of
real, messy input IS the product's accuracy -- there is no second chance
downstream.

**Scope, deliberately narrow.** CLAUDE.md invariant #2: "Drug identity,
strength, dose range and form come from the offline formulary. The
schedule comes from the rules parser first." This module only resolves
the *schedule* half of a `MedicationOrder`: `dose_qty` (as a bare number,
not tied to a specific drug/strength), `frequency`, `food_relation`,
`duration_days`, and `prn`. It never sees or produces `drug_generic`,
`drug_display`, `strength`, or `form` -- those come from
`puriyudha.formulary` and `puriyudha.vision`/OCR. `route` is also out of
scope: nothing in the messy ASR pool this module is tested against ever
states a route, and inferring one would be guessing, not parsing.

**Language scope.** CLAUDE.md's "Language scope" section: English is the
clinician-side input language; Tamil and Hindi are patient-facing OUTPUT
only. This parser therefore only targets the "en" ASR pool in
`services/mock_bhashini/fake_backends.py`, not the "hi"/"ta" pools (which
are for the ASR/NMT/TTS boundary in general, not clinician dosing
shorthand). It does, however, recognise a small, deliberately narrow set
of Hindi/Tamil words -- see `_TOKEN_SYNONYMS` -- because the "en" messy
pool's own code-switching category represents a real, common pattern: a
bilingual pharmacist's English shading into Hindi/Tamil words
mid-sentence. This is not a Hindi/Tamil parser; it is an English parser
that tolerates a few loanwords.

**Refuse rather than guess (CLAUDE.md invariant #3).** `parse_sig()` never
raises for malformed, incomplete, or empty input -- an empty ASR
transcript (silence) is a normal, expected real-world case, not a bug.
Every field is either resolved with full confidence or explicitly marked
unresolved; there is no partial-confidence guessing (a rule either matched
cleanly or it didn't -- inventing a fractional confidence for a regex
match would be false precision). A `SigParseResult` with any unresolved
field must never be treated as a complete contribution to a
`MedicationOrder`; call `.to_needs_confirmation()` (or use
`parse_sig_or_confirm()`) and route the result to
`puriyudha.schema.NeedsHumanConfirmation` instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from puriyudha.schema import (
    MAX_DOSE_QTY,
    MAX_DURATION_DAYS,
    MIN_DOSE_QTY,
    Confidence,
    FoodRelation,
    Frequency,
    NeedsHumanConfirmation,
    Slot,
)

__all__ = ["SigParseResult", "parse_sig", "parse_sig_or_confirm", "SIG_FIELDS"]

# --- Normalisation vocabulary -------------------------------------------------

#: Words that carry no dosing information and are stripped before any
#: pattern matching -- hesitation/filler (finding H1, category (b)) plus
#: the two tokens of "i think" (finding H1, category (b) example), which
#: only ever appear as throwaway hedging in this domain.
_FILLER_WORDS: FrozenSet[str] = frozenset({"uh", "um", "so", "like", "i", "think"})

#: One token -> canonical token substitutions, applied uniformly before
#: any regex runs. Three unrelated purposes share this single dict because
#: they are all "make an equivalent word look like the canonical word" and
#: benefit from happening in one pass:
#:   - English number words -> digit strings (general vocabulary, one..ten
#:     -- not just what the fixtures happen to exercise).
#:   - A small, deliberately narrow code-switch vocabulary -- exactly the
#:     words the messy "en" ASR pool's code-switching category (finding
#:     H1, category (e)) uses, no more. Not a general Hindi/Tamil
#:     dictionary; see the module docstring.
#:   - Nothing here maps "twice"/"thrice"/"once": those are frequency
#:     *multiplier* idioms ("twice a day" = "2 times a day"), not raw
#:     quantities, and are matched as whole phrases in
#:     `_FREQUENCY_WORD_PATTERNS` instead -- substituting them token-by-
#:     token here would risk corrupting an unrelated "twice tablet"-shaped
#:     match that should never exist.
_TOKEN_SYNONYMS: Dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "onnu": "1",          # Tamil "one"
    "ek": "1",             # Hindi "one"
    "subah": "morning",    # Hindi
    "raat": "night",       # Hindi
    "khana": "food",       # Hindi
    "saapadu": "food",     # Tamil
}

#: Slot -> the (already-synonym-normalised) token that signals it. Checked
#: by simple membership, not regex -- slot detection is "is this word
#: present anywhere", unlike the phrase-shaped patterns below.
_SLOT_TOKENS: Tuple[Tuple[Slot, str], ...] = (
    (Slot.MORNING, "morning"),
    (Slot.NOON, "noon"),
    (Slot.EVENING, "evening"),
    (Slot.NIGHT, "night"),
)

# --- Dose quantity --------------------------------------------------------------

_DOSE_PATTERN = re.compile(r"\b(\d+)\s+(?:tablets?|tabs?|capsules?|caps?|ml)\b")

# --- Frequency -------------------------------------------------------------------

#: Whole-phrase multiplier idioms, checked before the digit form -- see
#: _TOKEN_SYNONYMS's docstring for why "twice"/"thrice" are not simple
#: token substitutions.
_FREQUENCY_WORD_PATTERNS: Tuple[Tuple["re.Pattern[str]", int], ...] = (
    (re.compile(r"\bonce\s+a\s+day\b"), 1),
    (re.compile(r"\btwice\s+a\s+day\b"), 2),
    (re.compile(r"\bthrice\s+a\s+day\b"), 3),
)
_FREQUENCY_DIGIT_PATTERN = re.compile(r"\b(\d+)\s+times?\s+a\s+day\b")

# --- Food relation -----------------------------------------------------------------

#: Checked in this order against the normalised text. "food ke baad" /
#: "food se pehle" are the Hindi postposition forms ("khana ke baad" /
#: "khana se pehle") that survive as exactly this shape once "khana" has
#: already become "food" via _TOKEN_SYNONYMS -- Hindi puts the
#: before/after word AFTER its noun, the mirror image of English.
_FOOD_RELATION_PATTERNS: Tuple[Tuple["re.Pattern[str]", FoodRelation], ...] = (
    (re.compile(r"\bbefore\s+food\b"), FoodRelation.BEFORE),
    (re.compile(r"\bfood\s+se\s+pehle\b"), FoodRelation.BEFORE),
    (re.compile(r"\bafter\s+food\b"), FoodRelation.AFTER),
    (re.compile(r"\bfood\s+ke\s+baad\b"), FoodRelation.AFTER),
    (re.compile(r"\bwith\s+food\b"), FoodRelation.WITH),
)

#: If none of the patterns above match, but one of these tokens is present
#: anywhere, food *was* mentioned in some form that didn't cleanly parse
#: (e.g. a truncated "after fo") -- that is a reason to ask a human, not a
#: reason to confidently assume FoodRelation.ANY (CLAUDE.md invariant #3:
#: refuse rather than guess). Only the true absence of any of these words
#: earns the confident ANY default.
_FOOD_AMBIGUOUS_KEYWORDS: FrozenSet[str] = frozenset({"food", "before", "after", "with"})

# --- Duration ----------------------------------------------------------------------

#: Two alternatives, deliberately NOT "for" is optional + singular-or-
#: plural unit: that shape also matches "a day" inside "twice A DAY",
#: mistaking a frequency *rate* for a treatment *duration* (caught by
#: test_messy_phrase_resolves_as_expected -- "twice a day ... for a WEEK"
#: was resolving to 1 day, from the wrong "a day"). Singular "day"/"week"
#: only ever means duration when "for" is unambiguously right before it
#: ("for a day", "for 2 weeks"); bare plural "days"/"weeks" is
#: unambiguous on its own even without "for" (real ASR drops "for" just
#: as often as it keeps it -- "...after food five days" has none,
#: "...WITH food for a WEEK" does), which the second alternative covers.
#: "a" is handled as a literal alternative to a digit (not folded into
#: _TOKEN_SYNONYMS -- see that dict's docstring) so "for a week"/"for a
#: day" resolve without also turning every other bare "a" in the text
#: into "1".
_DURATION_PATTERN = re.compile(
    r"\bfor\s+(?P<for_qty>\d+|a)\s+(?P<for_unit>days?|weeks?)\b"
    r"|\b(?P<bare_qty>\d+)\s+(?P<bare_unit>days|weeks)\b"
)
_DURATION_KEYWORDS: FrozenSet[str] = frozenset({"day", "days", "week", "weeks"})

# --- PRN -----------------------------------------------------------------------------

_PRN_PATTERN = re.compile(r"\b(?:as needed|if needed|when needed|sos|prn)\b")

# --- Field bookkeeping ------------------------------------------------------------

#: Every field this parser attempts to resolve, in the fixed priority
#: order used both to pick which single field a NeedsHumanConfirmation
#: names (schema.NeedsHumanConfirmation only ever names one) and to order
#: SigParseResult.unresolved_fields. dose_qty and frequency come first:
#: unlike food_relation/duration_days, there is no legitimate confident
#: default for "how much" or "how often" -- every real order needs both.
SIG_FIELDS: Tuple[str, ...] = ("dose_qty", "frequency", "food_relation", "duration_days")


# --- SigParseResult ------------------------------------------------------------------

@dataclass(frozen=True)
class SigParseResult:
    """Everything the rules parser could extract from one utterance of
    prescription shorthand -- always returned, never raised, even for
    empty or unintelligible input.

    Never a `MedicationOrder`: this parser has no drug identity (see
    module docstring), so a `SigParseResult` is always a partial
    contribution, to be merged with the formulary's output elsewhere.

    `dose_qty`, `frequency`: `None` when unresolved (there is no
    legitimate confident default for either -- see `SIG_FIELDS`).
    `food_relation`: **never `None`** -- when unresolved, this holds
    `FoodRelation.ANY` as a placeholder, not a resolved value. Check
    `unresolved_fields` (or `fully_resolved`), never trust this field in
    isolation. `duration_days`: `None` means either "confidently not
    mentioned" (not in `unresolved_fields`) or "mentioned but
    unparseable" (in `unresolved_fields`) -- same "check unresolved_fields
    first" rule applies.
    """

    raw_text: str
    normalized_text: str
    dose_qty: Optional[float]
    frequency: Optional[Frequency]
    food_relation: FoodRelation
    duration_days: Optional[int]
    prn: bool
    confidence: Confidence
    unresolved_fields: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def fully_resolved(self) -> bool:
        return not self.unresolved_fields

    def to_needs_confirmation(self) -> NeedsHumanConfirmation:
        """Turns an unresolved result into the CLAUDE.md invariant #3
        refusal. Raises `ValueError` if called on a fully-resolved
        result -- that would mean a caller skipped the `fully_resolved`
        check this method exists to enforce."""
        if self.fully_resolved:
            raise ValueError(
                "to_needs_confirmation() called on a fully-resolved SigParseResult "
                "-- check .fully_resolved before calling this"
            )
        return NeedsHumanConfirmation(
            field_name=self.unresolved_fields[0],
            candidates=[],
            reason=(
                f"rules parser could not resolve {', '.join(self.unresolved_fields)} "
                f"from: {self.raw_text!r}"
            ),
            confidence=self.confidence.overall,
        )


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _strip_fillers(tokens: List[str]) -> List[str]:
    return [t for t in tokens if t not in _FILLER_WORDS]


def _collapse_repeats(tokens: List[str]) -> List[str]:
    result: List[str] = []
    for t in tokens:
        if result and result[-1] == t:
            continue
        result.append(t)
    return result


def _resolve_dose_qty(text: str) -> Tuple[Optional[float], bool]:
    matches = _DOSE_PATTERN.findall(text)
    if not matches:
        return None, True
    values = {float(m) for m in matches}
    if len(values) != 1:
        return None, True  # conflicting quantities mentioned (e.g. different AM/PM doses)
    value = values.pop()
    if not (MIN_DOSE_QTY < value <= MAX_DOSE_QTY):
        return None, True
    return value, False


def _resolve_frequency(text: str, tokens: FrozenSet[str]) -> Tuple[Optional[Frequency], bool]:
    explicit_n: Optional[int] = None
    for pattern, n in _FREQUENCY_WORD_PATTERNS:
        if pattern.search(text):
            explicit_n = n
            break
    if explicit_n is None:
        m = _FREQUENCY_DIGIT_PATTERN.search(text)
        if m:
            explicit_n = int(m.group(1))

    found_slots = [slot for slot, token in _SLOT_TOKENS if token in tokens]

    try:
        if explicit_n is not None and found_slots:
            if explicit_n != len(found_slots):
                return None, True  # conflicting frequency signals
            return Frequency(times_per_day=explicit_n, slots=found_slots), False
        if explicit_n is not None:
            return Frequency(times_per_day=explicit_n), False
        if found_slots:
            return Frequency(times_per_day=len(found_slots), slots=found_slots), False
    except ValueError:
        return None, True  # e.g. out of Frequency's valid times_per_day range
    return None, True


def _resolve_food_relation(text: str, tokens: FrozenSet[str]) -> Tuple[FoodRelation, bool]:
    for pattern, relation in _FOOD_RELATION_PATTERNS:
        if pattern.search(text):
            return relation, False
    if tokens & _FOOD_AMBIGUOUS_KEYWORDS:
        return FoodRelation.ANY, True
    return FoodRelation.ANY, False


def _resolve_duration(text: str, tokens: FrozenSet[str]) -> Tuple[Optional[int], bool]:
    m = _DURATION_PATTERN.search(text)
    if m:
        if m.group("for_qty") is not None:
            qty_str, unit = m.group("for_qty"), m.group("for_unit")
        else:
            qty_str, unit = m.group("bare_qty"), m.group("bare_unit")
        qty = 1 if qty_str == "a" else int(qty_str)
        days = qty * (7 if unit.startswith("week") else 1)
        if 1 <= days <= MAX_DURATION_DAYS:
            return days, False
        return None, True  # out of range -- e.g. absurd durations

    # No duration phrase matched. Before concluding "not mentioned at
    # all", strip out any recognised frequency *rate* idiom ("twice a
    # day", "3 times a day") -- its "day" token is not a duration mention
    # and must not trip the ambiguous-keyword fallback below.
    stripped = text
    for pattern, _n in _FREQUENCY_WORD_PATTERNS:
        stripped = pattern.sub(" ", stripped)
    stripped = _FREQUENCY_DIGIT_PATTERN.sub(" ", stripped)
    remaining_tokens = frozenset(stripped.split())

    if remaining_tokens & _DURATION_KEYWORDS:
        return None, True  # "days"/"weeks" mentioned but no parseable quantity
    return None, False


def _resolve_prn(text: str) -> bool:
    return bool(_PRN_PATTERN.search(text))


def parse_sig(text: str) -> SigParseResult:
    """Parses one utterance of English prescription shorthand into a
    `SigParseResult`. Always returns a result; never raises for
    malformed, incomplete, or empty `text` (`TypeError` only if `text`
    isn't a string at all -- that's a caller bug, not messy real-world
    input).

    Deterministic and rule-based throughout (CLAUDE.md invariant #5): no
    field's confidence is ever a fuzzy number between 0 and 1, because a
    regex/keyword rule either matched or it didn't -- see
    `SigParseResult`'s docstring for exactly what an unresolved field
    means.
    """
    if not isinstance(text, str):
        raise TypeError(f"parse_sig() expects a string, got {type(text).__name__}")

    tokens = _tokenize(text)
    tokens = _strip_fillers(tokens)
    tokens = _collapse_repeats(tokens)
    tokens = [_TOKEN_SYNONYMS.get(t, t) for t in tokens]
    normalized_text = " ".join(tokens)
    token_set = frozenset(tokens)

    dose_qty, dose_unresolved = _resolve_dose_qty(normalized_text)
    frequency, freq_unresolved = _resolve_frequency(normalized_text, token_set)
    food_relation, food_unresolved = _resolve_food_relation(normalized_text, token_set)
    duration_days, duration_unresolved = _resolve_duration(normalized_text, token_set)
    prn = _resolve_prn(normalized_text)

    unresolved_flags = {
        "dose_qty": dose_unresolved,
        "frequency": freq_unresolved,
        "food_relation": food_unresolved,
        "duration_days": duration_unresolved,
    }
    unresolved_fields = tuple(name for name in SIG_FIELDS if unresolved_flags[name])
    per_field_confidence = {name: (0.0 if unresolved_flags[name] else 1.0) for name in SIG_FIELDS}

    return SigParseResult(
        raw_text=text,
        normalized_text=normalized_text,
        dose_qty=dose_qty,
        frequency=frequency,
        food_relation=food_relation,
        duration_days=duration_days,
        prn=prn,
        confidence=Confidence(overall=min(per_field_confidence.values()), per_field=per_field_confidence),
        unresolved_fields=unresolved_fields,
    )


def parse_sig_or_confirm(text: str):
    """Convenience wrapper for callers that don't need the partial
    result: `parse_sig(text)`, then immediately
    `.to_needs_confirmation()` if it didn't fully resolve.

    Returns a `SigParseResult` when `text` fully resolved, or a
    `NeedsHumanConfirmation` when it didn't -- this IS the "partial result
    that leads to NeedsHumanConfirmation" CLAUDE.md and this module's
    docstring describe; a caller must never treat an unresolved
    `SigParseResult` (had they called `parse_sig` directly) as if it were
    a complete one.
    """
    result = parse_sig(text)
    if not result.fully_resolved:
        return result.to_needs_confirmation()
    return result
