"""Admin idle-timeout failures must be loud and counted, not swallowed.

``_verify_admin_payload`` has two best-effort branches around the 30-minute
admin idle timeout, and both used to end in ``logger.warning(...)`` and carry
on:

  * ``last_activity_at`` fails to parse -> the ``> _IDLE_SECONDS`` comparison
    never runs, so the idle timeout is **silently disabled** for that request.
  * the ``last_activity_at`` touch write fails -> the column stops advancing,
    so every later request measures idleness against an ever-older timestamp
    and the operator is logged out ~30 minutes later while actively working.

Neither is a recoverable anomaly: the first is a security control quietly
turning itself off, the second is a DB write failure on the auth path, which
CLAUDE.md's "Do not silently swallow errors" section forbids handling at
``warning``. Worse, WARNING sits below ``server.py``'s Sentry
``event_level="ERROR"`` threshold, so neither produced a signal anywhere --
the exact pattern migration 233 records as a real incident, and 2026-09-20
review finding E3 (still open from the 09-15 review's Major #8).

The let-through behaviour is deliberate and unchanged -- an operator must not
lose the dashboard mid-incident over one bad column value or one failed touch.
What changes is that the failure is now ERROR (with a real traceback, via
loguru's ``.opt(exception=True)``) and increments
``spinr_auth_admin_idle_touch_failed_total{reason=...}``, and is bound with
``domain="auth"`` so the loguru->Sentry sink can promote it to a tag.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import dependencies
from backend.utils import metrics
from dependencies import JWT_AUD_ADMIN, _verify_admin_payload

pytestmark = pytest.mark.anyio

_METRIC = "spinr_auth_admin_idle_touch_failed_total"


def _admin_payload() -> dict:
    return {
        "user_id": "staff-77",
        "role": "operations",
        "email": "ops@spinr.ca",
        "aud": JWT_AUD_ADMIN,
        "jti": "tok-staff-77",
        "token_version": 0,
    }


def _staff_row(last_activity_at) -> dict:
    return {
        "id": "staff-77",
        "email": "ops@spinr.ca",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        "last_activity_at": last_activity_at,
    }


def _counter(reason: str) -> float:
    """Current value of the counter for one reason label."""
    return metrics.snapshot()["counters"].get(_METRIC, {}).get((("reason", reason),), 0)


@pytest.fixture(autouse=True)
def _redis_quiet(monkeypatch):
    """The JTI denylist is pinned in test_admin_revocation_failopen.py and is
    not what these tests are about."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))


def _install_staff(monkeypatch, *, last_activity_at, update_one: AsyncMock) -> None:
    monkeypatch.setattr(
        dependencies.db_supabase,
        "get_rows",
        AsyncMock(return_value=[_staff_row(last_activity_at)]),
    )
    monkeypatch.setattr(dependencies.db_supabase, "update_one", update_one)


def _spy_logger(monkeypatch) -> MagicMock:
    """Replace the module's loguru logger so the call can be asserted directly.

    Asserting on ``.bind(...).opt(exception=True).error`` rather than on
    captured text is deliberate: loguru does not route through ``caplog``, and
    the thing under test is precisely *which* logger call is made -- the level,
    that the traceback comes from ``.opt(exception=True)`` rather than an
    ``exc_info=`` kwarg loguru would silently swallow as a ``str.format``
    keyword, and that ``domain`` is bound so ``server.py``'s loguru->Sentry sink
    can promote it to a tag (it reads only ``record["extra"]``).
    """
    spy = MagicMock()
    monkeypatch.setattr(dependencies, "logger", spy)
    return spy


def _assert_error_with_domain(spy: MagicMock) -> None:
    """The full contract for one of these log calls."""
    spy.bind.assert_any_call(domain="auth", user_id="staff-77")
    spy.bind.return_value.opt.assert_any_call(exception=True)
    assert spy.bind.return_value.opt.return_value.error.called
    # WARNING is below server.py's Sentry event_level threshold -- the whole
    # reason this finding existed.
    assert not spy.warning.called


class TestMalformedTimestamp:
    async def test_lets_the_request_through(self, monkeypatch):
        """Unchanged behaviour: a bad column value must not lock an operator out."""
        _install_staff(monkeypatch, last_activity_at="not-a-timestamp", update_one=AsyncMock())
        _spy_logger(monkeypatch)

        user = await _verify_admin_payload(_admin_payload())

        assert user is not None
        assert user["id"] == "staff-77"
        assert user["_admin_verified"] is True

    async def test_logs_at_error_with_a_traceback(self, monkeypatch):
        _install_staff(monkeypatch, last_activity_at="not-a-timestamp", update_one=AsyncMock())
        spy = _spy_logger(monkeypatch)

        await _verify_admin_payload(_admin_payload())

        _assert_error_with_domain(spy)

    async def test_increments_the_counter(self, monkeypatch):
        _install_staff(monkeypatch, last_activity_at="not-a-timestamp", update_one=AsyncMock())
        _spy_logger(monkeypatch)
        before = _counter("malformed_timestamp")

        await _verify_admin_payload(_admin_payload())

        assert _counter("malformed_timestamp") == before + 1


class TestTouchWriteFailure:
    _RECENT = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    async def test_lets_the_request_through(self, monkeypatch):
        """A failed touch must not cost the operator the dashboard."""
        _install_staff(
            monkeypatch,
            last_activity_at=self._RECENT,
            update_one=AsyncMock(side_effect=RuntimeError("supabase unreachable")),
        )
        _spy_logger(monkeypatch)

        user = await _verify_admin_payload(_admin_payload())

        assert user is not None
        assert user["id"] == "staff-77"

    async def test_logs_at_error_and_counts(self, monkeypatch):
        _install_staff(
            monkeypatch,
            last_activity_at=self._RECENT,
            update_one=AsyncMock(side_effect=RuntimeError("supabase unreachable")),
        )
        spy = _spy_logger(monkeypatch)
        before = _counter("touch_write_failed")

        await _verify_admin_payload(_admin_payload())

        _assert_error_with_domain(spy)
        assert _counter("touch_write_failed") == before + 1


class TestHealthyPath:
    async def test_a_fresh_timestamp_touches_nothing_and_counts_nothing(self, monkeypatch):
        """Guards the other direction: the counter must stay flat on the normal
        path, or an alert built on it would fire constantly and be ignored.

        A timestamp inside _ACTIVITY_TOUCH_INTERVAL_S (60s) is 'fresh', so the
        write is coalesced away entirely -- no update_one call, no metric.
        """
        update_one = AsyncMock()
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        _install_staff(monkeypatch, last_activity_at=fresh, update_one=update_one)
        _spy_logger(monkeypatch)
        before = (_counter("malformed_timestamp"), _counter("touch_write_failed"))

        user = await _verify_admin_payload(_admin_payload())

        assert user is not None
        assert update_one.await_count == 0
        assert (_counter("malformed_timestamp"), _counter("touch_write_failed")) == before
