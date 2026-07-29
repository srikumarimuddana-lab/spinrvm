"""Tests for utils/safety_checkin_loop.py.

Covers:
- Ride older than 20 min with no prior check-in → push sent
- Ride newer than 20 min → skipped (no push)
- Push already sent → no duplicate push
- Push sent + rider confirmed (ok_key set) → no escalation
- Push sent + no response + within 90 s window → no escalation yet
- Push sent + no response + past 90 s window → escalate
- Already escalated → no duplicate escalation
- FCM push failure → loop continues to next ride
- Ride with no rider_id → push skipped silently
- Ride with no ride_started_at → skipped
- _escalate sets escalated key only after successful DB insert
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────


def _ride(
    ride_id: str = "r1",
    rider_id: str = "u1",
    started_at: str | None = None,
    minutes_ago: int = 25,
) -> dict:
    if started_at is None:
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        started_at = ts.isoformat()
    return {"id": ride_id, "rider_id": rider_id, "status": "in_progress", "ride_started_at": started_at}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── _tick: push-send path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_sends_checkin_for_overdue_ride():
    """Ride >20 min old with no prior check-in → push sent + sent_key written."""
    push = AsyncMock()
    rset = AsyncMock()

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", rset),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_awaited_once()
    push_call = push.call_args
    assert push_call[0][0] == "u1"
    rset.assert_awaited_once()
    key_written = rset.call_args[0][0]
    assert "safety:checkin:sent:r1" == key_written


@pytest.mark.asyncio
async def test_tick_skips_ride_newer_than_20_minutes():
    """Ride only 5 min old → no push sent."""
    push = AsyncMock()

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[_ride(minutes_ago=5)])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_ride_with_no_id():
    """Ride row missing 'id' entirely → skipped before any Redis/push work."""
    push = AsyncMock()
    ride = {"rider_id": "u1", "status": "in_progress", "ride_started_at": _now_iso()}

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[ride])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_ride_with_unparseable_started_at():
    """Garbage ride_started_at value → caught (ValueError/AttributeError),
    ride skipped rather than crashing the whole tick."""
    push = AsyncMock()
    ride = {"id": "r1", "rider_id": "u1", "status": "in_progress", "ride_started_at": "not-a-timestamp"}

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[ride])
        from utils.safety_checkin_loop import _tick

        await _tick()  # must not raise

    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_ride_with_no_ride_started_at():
    """Ride missing ride_started_at (and updated_at) → skipped entirely."""
    push = AsyncMock()
    ride = {"id": "r1", "rider_id": "u1", "status": "in_progress"}

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[ride])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_push_when_no_rider_id():
    """Ride without rider_id → redis sent_key still written, push not sent."""
    push = AsyncMock()
    rset = AsyncMock()
    ride = _ride(rider_id=None)  # type: ignore[arg-type]
    ride["rider_id"] = None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", rset),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[ride])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_not_awaited()
    # sent_key IS written even without rider_id — prevents re-processing this ride
    rset.assert_awaited_once()
    assert "safety:checkin:sent:r1" in rset.call_args[0][0]


# ── _tick: no-duplicate-push path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_does_not_resend_if_sent_key_exists():
    """sent_key already in Redis → no second push."""
    push = AsyncMock()

    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        return None  # ok_key and escalated_key absent

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", push),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    push.assert_not_awaited()


# ── _tick: escalation path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_no_escalation_while_in_response_window():
    """Push sent 30 s ago, no ok, escalate_window=90 s → no escalation yet."""
    escalate = AsyncMock()
    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", escalate),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    escalate.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_escalates_after_response_window_expires():
    """Push sent 120 s ago, no ok → escalate called."""
    escalate = AsyncMock()
    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", escalate),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    escalate.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_no_escalation_when_rider_confirmed():
    """Push sent, ok_key set → no escalation."""
    escalate = AsyncMock()
    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        if "ok" in key:
            return "1"
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", escalate),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    escalate.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_no_duplicate_escalation_if_already_escalated():
    """escalated_key set → _escalate not called again."""
    escalate = AsyncMock()
    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        if "escalated" in key:
            return "1"
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", escalate),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()

    escalate.assert_not_awaited()


# ── _tick: resilience ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_continues_after_fcm_failure():
    """FCM push failure for ride r1 → loop continues and processes ride r2."""
    push_calls: list[str] = []

    async def flaky_push(user_id: str, *args, **kwargs) -> None:
        push_calls.append(user_id)
        if user_id == "u1":
            raise RuntimeError("FCM down")

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", AsyncMock(return_value=None)),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", side_effect=flaky_push),
    ):
        db.get_rows = AsyncMock(return_value=[_ride("r1", "u1"), _ride("r2", "u2")])
        from utils.safety_checkin_loop import _tick

        await _tick()  # must not raise

    assert "u1" in push_calls
    assert "u2" in push_calls


@pytest.mark.asyncio
async def test_tick_skips_ride_with_unparseable_sent_timestamp():
    """sent_key exists but holds a garbage value (shouldn't happen in
    practice — Redis only ever gets our own isoformat() writes — but the
    parse is defensively caught rather than crashing the tick)."""
    escalate = AsyncMock()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return "not-a-timestamp"
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", escalate),
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()  # must not raise

    escalate.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_escalation_failure_is_logged_and_does_not_propagate():
    """_escalate raising must not crash the tick — the escalated_key was
    never set (see test_escalate_does_not_set_redis_key_on_db_failure), so
    the next tick naturally retries."""
    sent_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()

    async def fake_get(key: str) -> str | None:
        if "sent" in key:
            return sent_ts
        return None

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_get", fake_get),
        patch("utils.safety_checkin_loop.redis_set", AsyncMock()),
        patch("utils.safety_checkin_loop.send_push_notification", AsyncMock()),
        patch("utils.safety_checkin_loop._escalate", AsyncMock(side_effect=RuntimeError("escalation blew up"))),
        patch("utils.safety_checkin_loop.logger") as mock_logger,
    ):
        db.get_rows = AsyncMock(return_value=[_ride()])
        from utils.safety_checkin_loop import _tick

        await _tick()  # must not raise

    assert any("Escalation failed for ride r1" in c.args[0] for c in mock_logger.error.call_args_list)


# ── _escalate: DB failure behaviour ──────────────────────────────────────


@pytest.mark.asyncio
async def test_escalate_sets_redis_key_only_after_successful_db_insert():
    """escalated_key written only when DB insert succeeds."""
    rset = AsyncMock()

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_set", rset),
    ):
        db.insert_one = AsyncMock(return_value={"id": "inc1"})
        from utils.safety_checkin_loop import _escalate

        await _escalate(_ride(), datetime.now(timezone.utc))

    rset.assert_awaited_once()
    assert "escalated:r1" in rset.call_args[0][0]


@pytest.mark.asyncio
async def test_escalate_does_not_set_redis_key_on_db_failure():
    """DB insert failure → escalated_key NOT set → next tick will retry."""
    rset = AsyncMock()

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_set", rset),
    ):
        db.insert_one = AsyncMock(side_effect=RuntimeError("DB down"))
        from utils.safety_checkin_loop import _escalate

        with pytest.raises(RuntimeError):
            await _escalate(_ride(), datetime.now(timezone.utc))

    rset.assert_not_awaited()


@pytest.mark.asyncio
async def test_escalate_audit_log_failure_does_not_prevent_escalation():
    """The audit-log write is best-effort — a failure there must not undo
    the escalation that already succeeded (incident inserted, escalated_key
    set, safety team already notified by this point)."""
    rset = AsyncMock()

    with (
        patch("utils.safety_checkin_loop._supabase_db") as db,
        patch("utils.safety_checkin_loop.redis_set", rset),
        patch("utils.safety_checkin_loop._log_audit", AsyncMock(side_effect=RuntimeError("audit down"))),
    ):
        db.insert_one = AsyncMock(return_value={"id": "inc1"})
        from utils.safety_checkin_loop import _escalate

        await _escalate(_ride(), datetime.now(timezone.utc))  # must not raise

    rset.assert_awaited_once()
    assert "escalated:r1" in rset.call_args[0][0]
