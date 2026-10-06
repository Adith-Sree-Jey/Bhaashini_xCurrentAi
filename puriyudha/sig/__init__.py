"""Rules-based prescription shorthand ("sig") parser.

CLAUDE.md invariant #5 ("any behaviour that can be a rule is a rule") and
invariant #6 (LLM assistance is optional and disabled by default; the
system must produce a complete, correct `MedicationOrder` for supported
inputs using deterministic rules alone): this module is the ONLY
extraction path from a clinician's spoken/written dosing shorthand to a
structured schedule. See `puriyudha.sig.parser` for the full design
rationale, scope (schedule fields only -- never drug identity, see
CLAUDE.md invariant #2), and exactly what "cannot fully resolve" means for
each field.

    from puriyudha.sig import parse_sig, parse_sig_or_confirm

See README.md's "puriyudha.sig" section for the expected test-command
output.
"""
from __future__ import annotations

from puriyudha.sig.parser import SIG_FIELDS, SigParseResult, parse_sig, parse_sig_or_confirm

__all__ = ["SigParseResult", "parse_sig", "parse_sig_or_confirm", "SIG_FIELDS"]
