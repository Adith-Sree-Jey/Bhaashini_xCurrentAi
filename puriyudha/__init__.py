"""Puriyudha: offline medication-comprehension appliance.

See CLAUDE.md at the repository root for the product invariants and
engineering rules that govern every module in this package. In short:

- No network calls at inference time.
- The language model is never the source of a medical fact.
- Refuse rather than guess (see :mod:`puriyudha.schema`).
- No PHI is persisted.
- Deterministic before probabilistic.
"""

__version__ = "0.1.0"
