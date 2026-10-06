"""A total time budget shared across every client call in one patient
session.

Finding H2 from the verification pass: the old design only had a per-call
timeout plus a naive retry loop, applied independently by each client. One
patient session makes at least four Bhashini calls (ASR, NMT inbound, NMT
outbound, TTS) and, if the LLM fallback is enabled, at least one Ollama
call -- worst case, those compounded to roughly a minute, against a 12
second end-to-end product target (CLAUDE.md) with a patient standing at the
counter. A per-call timeout cannot express "the whole session is out of
time"; a shared, shrinking :class:`Deadline` can.

Usage: construct exactly one ``Deadline`` per patient session (not per
call), and pass it to every :class:`~puriyudha.clients.bhashini.BhashiniClient`
and :class:`~puriyudha.clients.ollama.OllamaClient` call that session makes.
Each call derives its own request timeout as
``min(that client's own per-call ceiling, deadline.remaining())``, so the
budget only ever shrinks, never resets, across the whole session -- and
once it hits zero, every further call raises immediately (a
``BhashiniDeadlineExceeded`` / ``OllamaDeadlineExceeded``, both distinct
from a generic transport failure) instead of attempting a request that has
no time left to complete.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

#: Ties the shared session budget to CLAUDE.md's stated product target:
#: "Our end-to-end target is 12 seconds with a patient standing at a
#: counter." A caller building a full patient-session pipeline should
#: construct one Deadline(DEFAULT_SESSION_BUDGET_SECONDS) at the start of
#: the session and thread it through every client call; a caller that only
#: needs a handful of calls (e.g. a one-off tool) may choose a smaller or
#: larger budget explicitly instead.
DEFAULT_SESSION_BUDGET_SECONDS = 12.0


@dataclass
class Deadline:
    """A monotonic-clock-based total time budget.

    ``clock`` defaults to ``time.monotonic`` (wall-clock-safe: unaffected by
    system clock adjustments) but is overridable so tests can drive it with
    a fake clock instead of real sleeps -- see tests/test_bhashini_client.py
    and tests/test_ollama_client.py's ``FakeClock``.
    """

    budget_seconds: float
    clock: Callable[[], float] = time.monotonic
    _start: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.budget_seconds < 0.0:
            raise ValueError(f"budget_seconds must be >= 0, got {self.budget_seconds}")
        self._start = self.clock()

    def remaining(self) -> float:
        """Seconds left in the budget. Never negative: 0.0 once exhausted,
        never a negative number a caller might mistake for "no timeout"."""
        elapsed = self.clock() - self._start
        return max(0.0, self.budget_seconds - elapsed)

    def exceeded(self) -> bool:
        return self.remaining() <= 0.0

    def timeout_for(self, per_call_timeout: float) -> float:
        """The timeout a single request should actually use: whichever is
        smaller of the calling client's own per-call ceiling and however
        much of the shared session budget is left."""
        return min(per_call_timeout, self.remaining())
