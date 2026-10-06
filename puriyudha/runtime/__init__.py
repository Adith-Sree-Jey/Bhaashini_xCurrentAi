"""Model scheduler, timing, and power instrumentation.

Logs timing for every pipeline stage in the same JSONL shape the reference
(pocketinfer) app uses, so numbers are comparable to the upstream baseline.
The scheduler/timing/power instrumentation itself is not yet implemented.

:mod:`puriyudha.runtime.metrics` (part 5.3 of the T4 architecture change --
see CLAUDE.md and :mod:`puriyudha.config`) is implemented now: the
slot-level F1 comparison plumbing the T7 evaluation harness will use to
report the same pipeline run with the LLM fallback enabled and disabled.
"""
