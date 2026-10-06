"""Offline drug formulary: SQLite build and fuzzy/phonetic matcher.

Drug identity, strength, dose range and form always come from here, never
from the language model (CLAUDE.md invariant #2).

The 40-drug hand-curated seed set
(:data:`puriyudha.formulary.seed_data.SEED_DRUGS`) is the PRIMARY
deliverable of this module -- not a stopgap ahead of a full NLEM (National
List of Essential Medicines) / Jan Aushadhi ingest, which is secondary and
not yet built. See :mod:`puriyudha.formulary.seed_data` for the full
rationale behind the dosing data and its limits.

::

    python -m puriyudha.formulary --build   # writes data/formulary.sqlite
    python -m puriyudha.formulary --demo    # prints a few example matches

See README.md's "puriyudha.formulary" section for the exact expected
output.
"""
from __future__ import annotations

from puriyudha.formulary.db import build_formulary_db, load_formulary_db
from puriyudha.formulary.matcher import DEFAULT_MIN_SCORE, MAX_MATCHES, DrugMatch, match_drug_name
from puriyudha.formulary.models import FormularyDrug
from puriyudha.formulary.seed_data import SEED_DRUGS

__all__ = [
    "FormularyDrug",
    "SEED_DRUGS",
    "build_formulary_db",
    "load_formulary_db",
    "DrugMatch",
    "match_drug_name",
    "DEFAULT_MIN_SCORE",
    "MAX_MATCHES",
]
