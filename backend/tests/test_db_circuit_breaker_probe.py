"""Regression tests for finding 1: the DB circuit-breaker half-open probe must
never leak.

should_allow() grants exactly one probe in half-open state (setting
_probe_in_flight=True) and only record_success/record_failure clear it. In
run_sync the client-deadline abort paths (in-loop deadline pre-check, wait_for
timeout) and the ValueError early-exit raise past the breaker bookkeeping,
calling neither — so _probe_in_flight would stay True forever, should_allow()
would return False on every future call, and the whole API would 503 on all DB
operations until the process restarts. release_probe() closes that gap.
"""

import pytest

pytestmark = pytest.mark.unit

try:
    from backend.repositories._base import _CircuitBreaker

    _IMPORTABLE = True
except Exception:  # pragma: no cover - backend deps unavailable in this env
    _IMPORTABLE = False

pytestmark = [pytest.mark.unit, pytest.mark.skipif(not _IMPORTABLE, reason="backend deps unavailable")]


def _open_breaker():
    cb = _CircuitBreaker()
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure()
    assert cb._state == "open"
    return cb


def _age_open_timer(cb):
    """Pretend OPEN_DURATION has elapsed since the breaker last opened."""
    assert cb._opened_at is not None
    cb._opened_at -= cb.OPEN_DURATION + 1


def test_release_probe_reverts_half_open_to_open():
    cb = _open_breaker()
    _age_open_timer(cb)
    assert cb.should_allow() is True  # open -> half_open, grants the probe
    assert cb._state == "half_open"
    assert cb._probe_in_flight is True
    # While the probe is in flight, every other call is blocked.
    assert cb.should_allow() is False

    # Probe aborts on a client deadline (a run_sync bypass path) -> release it.
    cb.release_probe()
    assert cb._state == "open"
    assert cb._probe_in_flight is False

    # Recovery still works: after another OPEN_DURATION a fresh probe is granted.
    _age_open_timer(cb)
    assert cb.should_allow() is True
    assert cb._state == "half_open"


def test_release_probe_is_noop_when_closed():
    cb = _CircuitBreaker()
    assert cb._state == "closed"
    cb.release_probe()
    # Must never open a healthy breaker or touch its probe flag.
    assert cb._state == "closed"
    assert cb._probe_in_flight is False


def test_leaked_probe_wedges_without_release_and_release_unwedges():
    cb = _open_breaker()
    _age_open_timer(cb)
    assert cb.should_allow() is True  # grants probe
    assert cb._probe_in_flight is True

    # Simulate the buggy bypass: probe neither succeeds nor fails, not released.
    # half_open + probe_in_flight short-circuits should_allow to False, and it
    # never re-checks the open timer, so ageing it changes nothing — wedged.
    assert cb.should_allow() is False
    cb._opened_at -= cb.OPEN_DURATION + 1
    assert cb.should_allow() is False  # still wedged, forever

    # The fix unwedges it: release -> open, then it re-probes after the wait.
    cb.release_probe()
    assert cb._state == "open"
    _age_open_timer(cb)
    assert cb.should_allow() is True
