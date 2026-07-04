"""Circuit-breaker health semantics for repositories._base.run_sync.

The breaker measures CONNECTIVITY. Application-level errors (duplicate keys,
FK/CHECK violations, RLS denials) arrive in a normal server response and
prove the database is reachable — they must never open the breaker. Counting
them did exactly that on 2026-07-04: the daily onboarding-reminder scan's
expected 23505 duplicate-claim burst (multi-replica idempotency pattern)
opened the breaker, and every DB call in the process 503'd — the entire API
appeared down to riders and drivers.

Run:
    pytest backend/tests/test_db_circuit_breaker.py -v
"""

from __future__ import annotations

import time

import pytest

try:
    from backend.repositories import _base as base
    from backend.utils.error_handling import (
        DatabaseError,
        DuplicateRecordError,
        ServiceUnavailableException,
    )
except ImportError:
    from repositories import _base as base  # type: ignore
    from utils.error_handling import (  # type: ignore
        DatabaseError,
        DuplicateRecordError,
        ServiceUnavailableException,
    )


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """Reset the module-level breaker around every test here. The conftest
    reset targets the db_supabase re-export; this file drives
    repositories._base directly, so reset the singleton it uses."""
    base._breaker.record_success()
    yield
    base._breaker.record_success()


def _raise_duplicate():
    raise Exception('duplicate key value violates unique constraint "driver_onboarding_reminder_log_daily_uidx"')


def _raise_transient():
    raise RuntimeError("ConnectionTerminated: connection lost (GOAWAY)")


@pytest.mark.anyio
async def test_duplicate_key_burst_does_not_open_breaker():
    """More consecutive duplicates than FAILURE_THRESHOLD must leave the
    breaker closed — 23505 is expected control flow for claim patterns."""
    for _ in range(base._CircuitBreaker.FAILURE_THRESHOLD * 2):
        with pytest.raises(DuplicateRecordError):
            await base.run_sync(_raise_duplicate, retry_policy="write")
    assert base._breaker._state == "closed"
    # And the next call still reaches the DB — no breaker rejection.
    with pytest.raises(DuplicateRecordError):
        await base.run_sync(_raise_duplicate, retry_policy="write")


@pytest.mark.anyio
async def test_transient_failures_still_open_breaker():
    """Connectivity-class failures keep tripping the breaker as designed."""
    for _ in range(base._CircuitBreaker.FAILURE_THRESHOLD):
        with pytest.raises(DatabaseError):
            await base.run_sync(_raise_transient, retry_policy="write")
    assert base._breaker._state == "open"
    with pytest.raises(ServiceUnavailableException):
        await base.run_sync(_raise_transient, retry_policy="write")


@pytest.mark.anyio
async def test_application_error_closes_half_open_breaker():
    """A half-open probe answered with an application error proves the DB is
    reachable — the breaker must close, not flip back to open."""
    base._breaker._state = "open"
    base._breaker._opened_at = time.monotonic() - (base._CircuitBreaker.OPEN_DURATION + 1)

    with pytest.raises(DuplicateRecordError):
        await base.run_sync(_raise_duplicate, retry_policy="write")

    assert base._breaker._state == "closed"
