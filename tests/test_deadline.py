"""Tests for puriyudha.clients.deadline.Deadline.

Entirely deterministic: every test drives Deadline with an explicit fake
clock rather than time.sleep, so nothing here takes real wall time.
"""
import pytest

from puriyudha.clients.deadline import DEFAULT_SESSION_BUDGET_SECONDS, Deadline


class FakeClock:
    def __init__(self, start: float = 0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_remaining_starts_at_full_budget():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=10.0, clock=clock)
    assert deadline.remaining() == 10.0


def test_remaining_decreases_as_clock_advances():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=10.0, clock=clock)
    clock.advance(4.0)
    assert deadline.remaining() == pytest.approx(6.0)


def test_remaining_never_goes_negative():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    clock.advance(100.0)
    assert deadline.remaining() == 0.0


def test_exceeded_is_false_until_budget_is_used_up():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    assert deadline.exceeded() is False
    clock.advance(0.99)
    assert deadline.exceeded() is False
    clock.advance(0.02)
    assert deadline.exceeded() is True


def test_exceeded_at_exactly_the_budget_boundary():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    clock.advance(1.0)
    assert deadline.exceeded() is True  # remaining() == 0.0 counts as exceeded


def test_timeout_for_returns_remaining_when_smaller_than_per_call_ceiling():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=2.0, clock=clock)
    clock.advance(1.7)
    assert deadline.timeout_for(5.0) == pytest.approx(0.3)


def test_timeout_for_returns_per_call_ceiling_when_smaller_than_remaining():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=10.0, clock=clock)
    assert deadline.timeout_for(1.5) == 1.5


def test_timeout_for_is_zero_once_exceeded():
    clock = FakeClock()
    deadline = Deadline(budget_seconds=1.0, clock=clock)
    clock.advance(5.0)
    assert deadline.timeout_for(5.0) == 0.0


def test_negative_budget_is_rejected():
    with pytest.raises(ValueError, match="budget_seconds"):
        Deadline(budget_seconds=-1.0)


def test_zero_budget_is_immediately_exceeded():
    deadline = Deadline(budget_seconds=0.0, clock=FakeClock())
    assert deadline.exceeded() is True


def test_default_session_budget_matches_claude_md_target():
    """CLAUDE.md: "Our end-to-end target is 12 seconds with a patient
    standing at a counter." This constant is the one place that number is
    encoded for client callers -- this test exists so a change to it is a
    deliberate, visible edit, not an accidental one."""
    assert DEFAULT_SESSION_BUDGET_SECONDS == 12.0


def test_deadline_uses_real_monotonic_clock_by_default():
    """Without an explicit clock=, Deadline should still work using the
    real time.monotonic -- constructing one and immediately checking it
    must not raise and must report (approximately) the full budget."""
    deadline = Deadline(budget_seconds=5.0)
    assert deadline.remaining() == pytest.approx(5.0, abs=0.5)
    assert deadline.exceeded() is False
