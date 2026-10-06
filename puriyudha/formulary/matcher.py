"""Fuzzy drug-name matcher.

Matches free-text drug names (from OCR, ASR, or the LLM fallback) against
the formulary's generic and brand names, so a misspelled, ASR-garbled, or
brand-name mention resolves to the right generic -- or, per CLAUDE.md
invariant #3 ("refuse rather than guess"), returns nothing when no
candidate is confident enough to act on.

English/Latin-script names only: `FormularyDrug.tamil_name`/`hindi_name`
are for patient-facing DISPLAY (`puriyudha.render`), not input matching --
by the time text reaches this matcher, it has already gone through
`puriyudha.clients.bhashini` (ASR/NMT), which CLAUDE.md documents as
lowercase-Latin/English-normalised at our boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from rapidfuzz import fuzz, process, utils

from puriyudha.formulary.models import FormularyDrug

#: Below this score (rapidfuzz's 0-100 scale), a match is not returned at
#: all -- CLAUDE.md invariant #3: better to report no match (and let the
#: caller emit NeedsHumanConfirmation) than hand back a low-confidence
#: guess.
DEFAULT_MIN_SCORE = 70.0

#: Never return more than this many candidates -- mirrors CLAUDE.md
#: invariant #3 ("naming the top-3 candidates") and
#: puriyudha.schema.NeedsHumanConfirmation.MAX_CANDIDATES.
MAX_MATCHES = 3


@dataclass(frozen=True)
class DrugMatch:
    """One candidate match: which drug, which of its names matched, and
    how well (rapidfuzz's 0-100 `WRatio` scale)."""

    drug: FormularyDrug
    matched_name: str
    score: float


def match_drug_name(
    query: str,
    formulary: Sequence[FormularyDrug],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    max_matches: int = MAX_MATCHES,
) -> List[DrugMatch]:
    """Returns up to `max_matches` candidate drugs for free-text `query`,
    best match first, each scoring >= `min_score`.

    An empty list is the expected, common, correct result for garbled or
    unrecognisable input (CLAUDE.md: refuse rather than guess) -- not an
    error condition.

    Matches against every drug's generic name, display name, and brand
    names (`FormularyDrug.all_names()`). The same drug is never returned
    twice even if several of its names score highly -- only its single
    best-scoring name is kept. Matching is done by list index (not by
    name string) so this stays correct even if two different drugs
    happened to share an identical name string.
    """
    if not query or not query.strip():
        return []
    if not formulary:
        return []

    all_names: List[str] = []
    owning_drugs: List[FormularyDrug] = []
    for drug in formulary:
        for name in drug.all_names():
            all_names.append(name)
            owning_drugs.append(drug)

    # rapidfuzz.fuzz.WRatio does NOT normalise case or whitespace on its
    # own (verified: WRatio("PARACETAMOL", "paracetamol") == 0.0 without
    # this) -- real ASR/OCR input has inconsistent casing (see
    # services/mock_bhashini/fake_backends.py's messy-transcript fixtures),
    # so this processor is not optional.
    raw_matches = process.extract(
        query,
        all_names,
        scorer=fuzz.WRatio,
        processor=utils.default_process,
        limit=None,
        score_cutoff=min_score,
    )

    best_per_drug: Dict[str, DrugMatch] = {}
    for matched_name, score, idx in raw_matches:
        drug = owning_drugs[idx]
        existing = best_per_drug.get(drug.generic_name)
        if existing is None or score > existing.score:
            best_per_drug[drug.generic_name] = DrugMatch(drug=drug, matched_name=matched_name, score=score)

    ranked = sorted(best_per_drug.values(), key=lambda m: m.score, reverse=True)
    return ranked[:max_matches]
