"""Pipeline-wide configuration flags.

Currently holds exactly one flag, `llm_enabled` -- kept in its own module,
separate from any one client and separate from puriyudha/runtime/ (model
scheduler / timing / power instrumentation), because it is read by
multiple modules (currently puriyudha.extract; later puriyudha.app) that
must all agree on it without importing each other.

Architecture change (T4 cut, verification-pass finding, part 5): the LLM
fallback extractor is off the critical path. New CLAUDE.md invariant this
module exists to satisfy: "The system MUST produce a complete, correct
MedicationOrder for supported inputs using deterministic rules alone.
Language-model assistance is an optional enhancement that can be disabled
entirely by configuration, and the system's core claims must hold with it
disabled." `llm_enabled` defaults to False for exactly that reason -- this
is a designed property, not a temporary descope.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

#: See module docstring. Defaulting to False is the point: the rules-first
#: path (puriyudha.sig, then puriyudha.formulary) must produce a complete,
#: correct MedicationOrder on its own, with the LLM fallback (puriyudha.extract)
#: switched off entirely.
DEFAULT_LLM_ENABLED = False

#: Set to "1" to opt into the LLM fallback path without editing code --
#: e.g. for local experimentation, or for the T7 evaluation harness's
#: llm_enabled=True comparison run (part 5.3). Any other value, or unset,
#: keeps DEFAULT_LLM_ENABLED.
LLM_ENABLED_ENV_VAR = "PURIYUDHA_LLM_ENABLED"


@dataclass(frozen=True)
class PipelineConfig:
    """One pipeline run's configuration.

    Constructed once (per run, or per evaluation-harness invocation -- see
    T7 / puriyudha.runtime.metrics) and threaded through explicitly to
    whatever needs it, rather than read from module-global state at
    arbitrary call sites -- so, in particular, an evaluation run can
    construct two configs (llm_enabled=True and llm_enabled=False) side by
    side in the same process and compare their results (part 5.3's
    slot-level F1 delta) without one run's flag leaking into the other's.
    """

    llm_enabled: bool = DEFAULT_LLM_ENABLED

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Reads LLM_ENABLED_ENV_VAR from the environment; everything else
        uses its default. A convenience for CLI entry points -- library
        code should prefer an explicit PipelineConfig(...), not this."""
        return cls(llm_enabled=os.environ.get(LLM_ENABLED_ENV_VAR) == "1")
