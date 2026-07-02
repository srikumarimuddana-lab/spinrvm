"""Tests for utils/stripe_reconcile.py.

Covers:
- Skips cleanly when stripe_secret_key not configured
- Stripe API list failure → returns early, no DB query
- DB query failure → returns early, no discrepancies written
- DB_PAID_STRIPE_MISSING: ride in DB but no matching PI in Stripe
- DB_PAID_STRIPE_MISMATCH: PI exists but status != "succeeded"
- DB_PAID_AMOUNT_MISMATCH: Stripe amount_received differs from DB fare
- STRIPE_ORPHAN: succeeded PI with no matching DB ride
- Clean run (zero discrepancies) → info log, not error
- audit_logs write failure → error logged, not raised
- Redis lock not acquired → tick skipped, no Stripe call
- _in_window: valid/outside/edge/malformed timestamps
- Multiple discrepancy types in a single pass
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _yesterday_epoch() -> tuple[int, int]:
    """Return (window_start, window_end) epoch ints for yesterday UTC."""
    yesterday = date.today() - timedelta(days=1)
    start = int(datetime.combine(yesterday, time(0, 0), tzinfo=timezone.utc).timestamp())
    end = int(datetime.combine(yesterday, time(23, 59, 59), tzinfo=timezone.utc).timestamp())
    return start, end


def _ts_yesterday(hour: int = 12) -> str:
    """ISO timestamp at 'hour' UTC yesterday."""
    yesterday = date.today() - timedelta(days=1)
    dt = datetime.combine(yesterday, time(hour, 0), tzinfo=timezone.utc)
    return dt.isoformat()


def _ride(
    ride_id: str = "ride1",
    pi_id: str = "pi_abc",
    fare: str = "25.00",
    grand_total: str = "25.00",
    tip_amount: str = "0.00",
    authorized_amount: str | None = None,
) -> dict:
    return {
        "id": ride_id,
        "payment_intent_id": pi_id,
        # C2: reconcile compares against grand_total + tip (the authoritative
        # charge), not the fare-side subtotal. `fare` is retained only so legacy
        # callers don't break; the reconcile no longer reads it.
        "fare": fare,
        "grand_total": grand_total,
        "tip_amount": tip_amount,
        "authorized_amount": authorized_amount,
        "status": "completed",
        "payment_status": "paid",
        "ride_completed_at": _ts_yesterday(),
    }


def _pi(pi_id: str = "pi_abc", status: str = "succeeded", amount_received: int = 2500) -> dict:
    return {"id": pi_id, "status": status, "amount_received": amount_received}


def _make_stripe_mock(pis: list[dict]) -> MagicMock:
    """Return a mock stripe module whose PaymentIntent.list auto_paging_iter yields pis."""
    mock = MagicMock()
    list_result = MagicMock()
    list_result.auto_paging_iter.return_value = iter(pis)
    mock.PaymentIntent.list.return_value = list_result
    return mock


# ── Skip when unconfigured ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skips_when_no_stripe_secret_key():
    """No stripe_secret_key in settings → function returns without touching DB."""
    db_mock = AsyncMock()

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    db_mock.get_rows.assert_not_awaited()
    db_mock.insert_one.assert_not_awaited()


# ── Stripe API failure ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stripe_api_failure_returns_early():
    """Stripe list() raises → DB get_rows never called."""
    db_mock = AsyncMock()
    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.side_effect = RuntimeError("Stripe 500")

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    db_mock.get_rows.assert_not_awaited()


# ── DB query failure ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_query_failure_returns_early():
    """DB get_rows raises → function returns before writing audit_logs."""
    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = RuntimeError("DB down")
    stripe_mock = _make_stripe_mock([])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    db_mock.insert_one.assert_not_awaited()


# ── DB_PAID_STRIPE_MISSING ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_db_paid_stripe_missing():
    """DB ride has PI id but PI absent in Stripe → DB_PAID_STRIPE_MISSING discrepancy."""
    ride = _ride(pi_id="pi_gone")
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([])  # no PIs in Stripe

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    db_mock.insert_one.assert_awaited_once()
    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert audit_detail["discrepancies"] == 1
    assert audit_detail["discrepancy_detail"][0]["type"] == "DB_PAID_STRIPE_MISSING"
    assert audit_detail["discrepancy_detail"][0]["payment_intent_id"] == "pi_gone"


# ── DB_PAID_STRIPE_MISMATCH ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_db_paid_stripe_mismatch():
    """PI exists but status is 'requires_action' → DB_PAID_STRIPE_MISMATCH."""
    ride = _ride()
    pi = _pi(status="requires_action", amount_received=2500)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    types = [d["type"] for d in audit_detail["discrepancy_detail"]]
    assert "DB_PAID_STRIPE_MISMATCH" in types


# ── DB_PAID_AMOUNT_MISMATCH ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_db_paid_amount_mismatch():
    """Stripe amount_received (cents) != DB fare × 100 → DB_PAID_AMOUNT_MISMATCH."""
    ride = _ride(fare="25.00")  # expect 2500 cents
    pi = _pi(status="succeeded", amount_received=2499)  # 1 cent short
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    mismatch = next(d for d in audit_detail["discrepancy_detail"] if d["type"] == "DB_PAID_AMOUNT_MISMATCH")
    assert mismatch["db_cents"] == 2500
    assert mismatch["stripe_cents"] == 2499


@pytest.mark.asyncio
async def test_no_amount_mismatch_when_fare_matches():
    """Stripe amount_received exactly matches DB fare → no DB_PAID_AMOUNT_MISMATCH."""
    ride = _ride(fare="25.00")
    pi = _pi(status="succeeded", amount_received=2500)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    types = [d["type"] for d in audit_detail["discrepancy_detail"]]
    assert "DB_PAID_AMOUNT_MISMATCH" not in types


# ── STRIPE_ORPHAN ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_stripe_orphan():
    """Succeeded PI with no matching DB ride → STRIPE_ORPHAN."""
    pi = _pi(pi_id="pi_orphan", status="succeeded", amount_received=1500)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = []  # no rides in DB window
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    orphan = next(d for d in audit_detail["discrepancy_detail"] if d["type"] == "STRIPE_ORPHAN")
    assert orphan["payment_intent_id"] == "pi_orphan"
    assert orphan["stripe_amount"] == 1500


@pytest.mark.asyncio
async def test_failed_stripe_pi_not_flagged_as_orphan():
    """A failed/cancelled PI with no DB ride is NOT an orphan (only succeeded PIs matter)."""
    pi = _pi(pi_id="pi_failed", status="canceled", amount_received=0)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = []
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert audit_detail["discrepancies"] == 0


# ── Clean run ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_run_writes_zero_discrepancies():
    """Matched ride + succeeded PI with correct amount → zero discrepancies in audit log."""
    ride = _ride(grand_total="10.50")
    pi = _pi(status="succeeded", amount_received=1050)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert audit_detail["discrepancies"] == 0
    assert audit_detail["discrepancy_detail"] == []


# ── audit_logs write failure ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_log_write_failure_does_not_raise():
    """audit_logs insert failure → error logged, function completes without raising."""
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = []
    db_mock.insert_one.side_effect = RuntimeError("audit DB down")
    stripe_mock = _make_stripe_mock([])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()  # must not raise


# ── Multiple discrepancy types ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_discrepancy_types_counted():
    """One missing PI + one orphan PI → discrepancy count = 2."""
    ride_missing = _ride(ride_id="ride_missing", pi_id="pi_missing")
    pi_orphan = _pi(pi_id="pi_orphan", status="succeeded", amount_received=999)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride_missing]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi_orphan])  # only orphan PI, not ride_missing's PI

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert audit_detail["discrepancies"] == 2
    types = {d["type"] for d in audit_detail["discrepancy_detail"]}
    assert types == {"DB_PAID_STRIPE_MISSING", "STRIPE_ORPHAN"}


# ── Schema regression: rides has ride_completed_at, not completed_at ────────


@pytest.mark.asyncio
async def test_rides_query_selects_ride_completed_at_column():
    """Regression: the rides table column is `ride_completed_at` — selecting the
    nonexistent `completed_at` made PostgREST raise 42703 every nightly tick.
    Assert the get_rows columns request the real column and never the bad one."""
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = []
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    # Find the rides query among get_rows calls and inspect its columns kwarg.
    rides_calls = [c for c in db_mock.get_rows.await_args_list if c.args and c.args[0] == "rides"]
    assert rides_calls, "expected a get_rows('rides', ...) call"
    columns = rides_calls[0].kwargs.get("columns", "")
    assert "ride_completed_at" in columns
    assert "completed_at" not in columns.replace("ride_completed_at", "")


# ── _in_window ─────────────────────────────────────────────────────────────


def test_in_window_valid_timestamp_inside():
    """Timestamp exactly at window_start falls inside."""
    from utils.stripe_reconcile import _in_window

    start, end = _yesterday_epoch()
    mid = start + (end - start) // 2
    dt_iso = datetime.fromtimestamp(mid, tz=timezone.utc).isoformat()
    assert _in_window(dt_iso, start, end) is True


def test_in_window_timestamp_before_window():
    """Timestamp before window_start → False."""
    from utils.stripe_reconcile import _in_window

    start, end = _yesterday_epoch()
    before_iso = datetime.fromtimestamp(start - 1, tz=timezone.utc).isoformat()
    assert _in_window(before_iso, start, end) is False


def test_in_window_timestamp_after_window():
    """Timestamp after window_end → False."""
    from utils.stripe_reconcile import _in_window

    start, end = _yesterday_epoch()
    after_iso = datetime.fromtimestamp(end + 1, tz=timezone.utc).isoformat()
    assert _in_window(after_iso, start, end) is False


def test_in_window_z_suffix_handled():
    """ISO timestamps ending in 'Z' (Supabase format) are accepted."""
    from utils.stripe_reconcile import _in_window

    start, end = _yesterday_epoch()
    mid = start + 60
    dt_z = datetime.fromtimestamp(mid, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _in_window(dt_z, start, end) is True


def test_in_window_malformed_returns_false():
    """Malformed timestamp → False, no exception raised."""
    from utils.stripe_reconcile import _in_window

    assert _in_window("not-a-date", 0, 9999999999) is False
    assert _in_window("", 0, 9999999999) is False


def test_in_window_boundary_inclusive():
    """window_start and window_end are both inclusive."""
    from utils.stripe_reconcile import _in_window

    start, end = _yesterday_epoch()
    start_iso = datetime.fromtimestamp(start, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end, tz=timezone.utc).isoformat()
    assert _in_window(start_iso, start, end) is True
    assert _in_window(end_iso, start, end) is True


# ── Redis lock guard ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_skips_tick_when_lock_not_acquired():
    """redis_set_nx returns False → _run_reconciliation_tick never called."""
    import asyncio

    tick_called = False

    async def fake_tick():
        nonlocal tick_called
        tick_called = True

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch("utils.stripe_reconcile.redis_set_nx", AsyncMock(return_value=False)),
        patch("utils.stripe_reconcile._run_reconciliation_tick", fake_tick),
        patch("utils.stripe_reconcile._seconds_until", return_value=0),
        patch("utils.stripe_reconcile.asyncio.sleep", fake_sleep),
    ):
        from utils.stripe_reconcile import stripe_reconcile_loop

        with pytest.raises(asyncio.CancelledError):
            await stripe_reconcile_loop(target_hour_utc=2)

    assert not tick_called


@pytest.mark.asyncio
async def test_loop_runs_tick_when_lock_acquired():
    """redis_set_nx returns True → _run_reconciliation_tick is called."""
    import asyncio

    tick_called = False

    async def fake_tick():
        nonlocal tick_called
        tick_called = True

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch("utils.stripe_reconcile.redis_set_nx", AsyncMock(return_value=True)),
        patch("utils.stripe_reconcile._run_reconciliation_tick", fake_tick),
        patch("utils.stripe_reconcile._seconds_until", return_value=0),
        patch("utils.stripe_reconcile.asyncio.sleep", fake_sleep),
    ):
        from utils.stripe_reconcile import stripe_reconcile_loop

        with pytest.raises(asyncio.CancelledError):
            await stripe_reconcile_loop(target_hour_utc=2)

    assert tick_called


# ── Rides outside window are filtered ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rides_outside_window_ignored():
    """DB ride with completed_at two days ago is not in yesterday's window → no discrepancy."""
    two_days_ago = (date.today() - timedelta(days=2)).isoformat() + "T12:00:00+00:00"
    ride = {
        "id": "old_ride",
        "payment_intent_id": "pi_old",
        "fare": "15.00",
        "status": "completed",
        "payment_status": "paid",
        "ride_completed_at": two_days_ago,
    }
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([])  # no Stripe PIs

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert audit_detail["db_rides_checked"] == 0
    assert audit_detail["discrepancies"] == 0


# ── Ride with no fare field skips amount check ────────────────────────────


@pytest.mark.asyncio
async def test_ride_with_no_fare_skips_amount_check():
    """Ride missing fare field → only status checked, no DB_PAID_AMOUNT_MISMATCH raised."""
    ride = {
        "id": "ride_nofar",
        "payment_intent_id": "pi_nofar",
        "fare": None,
        "status": "completed",
        "payment_status": "paid",
        "ride_completed_at": _ts_yesterday(),
    }
    pi = _pi(pi_id="pi_nofar", status="succeeded", amount_received=999)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    types = [d["type"] for d in audit_detail["discrepancy_detail"]]
    assert "DB_PAID_AMOUNT_MISMATCH" not in types


# ── Payout settlement backstop (A4) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_payouts_flags_stranded_and_stuck():
    """Stranded (manual-review) and stuck (transfer_completed) payouts older
    than 1h are surfaced as discrepancies."""
    from utils import stripe_reconcile as sr

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    stranded = {
        "id": "p1",
        "driver_id": "d1",
        "status": "stranded",
        "requires_manual_review": True,
        "created_at": old_ts,
    }
    stuck = {
        "id": "p2",
        "driver_id": "d2",
        "status": "transfer_completed",
        "requires_manual_review": False,
        "created_at": old_ts,
    }

    def _rows(table, flt, columns=None, limit=None):
        if flt.get("requires_manual_review"):
            return [stranded]
        if flt.get("status") == "transfer_completed":
            return [stuck]
        return []

    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = _rows

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        result = await sr._reconcile_payouts()

    types = sorted(d["type"] for d in result)
    assert types == ["PAYOUT_STRANDED", "PAYOUT_STUCK"]


@pytest.mark.asyncio
async def test_reconcile_payouts_skips_recent_and_terminal():
    """Recent rows (<1h) and terminal rows are never flagged, even if a
    filter-agnostic query returns them."""
    from utils import stripe_reconcile as sr

    recent = {
        "id": "p3",
        "driver_id": "d3",
        "status": "stranded",
        "requires_manual_review": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    terminal = {
        "id": "p4",
        "driver_id": "d4",
        "status": "completed",
        "requires_manual_review": False,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
    }

    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [recent, terminal]

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        result = await sr._reconcile_payouts()

    assert result == []


# ── C2 regression: authoritative basis (grand_total + tip), underpay-only ────


@pytest.mark.asyncio
async def test_amount_basis_includes_fees_tax_and_tip():
    """C2: expected = grand_total + tip (not the fare subtotal). A PI that paid
    only the fare while grand_total includes fees/GST/PST + tip is UNDERPAYMENT."""
    ride = _ride(grand_total="30.00", tip_amount="5.00")  # expect 3500
    pi = _pi(status="succeeded", amount_received=2500)  # paid only the fare
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    mismatch = next(d for d in audit_detail["discrepancy_detail"] if d["type"] == "DB_PAID_AMOUNT_MISMATCH")
    assert mismatch["db_cents"] == 3500
    assert mismatch["stripe_cents"] == 2500


@pytest.mark.asyncio
async def test_overpayment_is_not_flagged():
    """C2: only UNDERPAYMENT is a revenue risk. Stripe amount >= expected → clean
    (no false positive, unlike the old strict != check)."""
    ride = _ride(grand_total="25.00")
    pi = _pi(status="succeeded", amount_received=2600)  # 1.00 over
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert all(d["type"] != "DB_PAID_AMOUNT_MISMATCH" for d in audit_detail["discrepancy_detail"])


@pytest.mark.asyncio
async def test_over_buffer_tip_capped_at_authorized_amount():
    """C2 + pre-auth: when tip exceeds the hold, only authorized_amount is
    captured on THIS PI (overflow is a separate PI), so expected is capped and
    the primary PI is not falsely flagged."""
    ride = _ride(grand_total="25.00", tip_amount="20.00", authorized_amount="35.00")  # cap 3500
    pi = _pi(status="succeeded", amount_received=3500)
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [ride]
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = _make_stripe_mock([pi])

    with (
        patch("utils.stripe_reconcile.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("utils.stripe_reconcile.db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.stripe_reconcile import _run_reconciliation_tick

        await _run_reconciliation_tick()

    audit_detail = db_mock.insert_one.call_args[0][1]["details"]
    assert all(d["type"] != "DB_PAID_AMOUNT_MISMATCH" for d in audit_detail["discrepancy_detail"])


# ── Stuck-processing ride backstop (review Finding #2) ──────────────────────


def _processing_ride(ride_id: str, *, minutes_ago: int, pi_id: str | None = "pi_x") -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": ride_id,
        "payment_intent_id": pi_id,
        "payment_status": "processing",
        "status": "completed",
        "updated_at": ts,
        "ride_completed_at": ts,
    }


@pytest.mark.asyncio
async def test_stuck_processing_flags_only_aged_rows():
    """Only rides stuck in 'processing' past the threshold are flagged; a fresh
    (still-settling) row is left alone, and nothing is mutated."""
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [
        _processing_ride("stuck", minutes_ago=60),  # well past the 15-min threshold
        _processing_ride("fresh", minutes_ago=1),  # settlement may still be in flight
    ]

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _reconcile_stuck_processing_rides

        out = await _reconcile_stuck_processing_rides()

    assert {d["ride_id"] for d in out} == {"stuck"}
    assert out[0]["type"] == "RIDE_PAYMENT_STUCK_PROCESSING"
    columns = db_mock.get_rows.await_args.kwargs["columns"]
    assert "ride_completed_at" in columns
    assert "completed_at" not in columns.replace("ride_completed_at", "")
    # Detection only — never writes/mutates a ride.
    db_mock.update_one.assert_not_awaited()
    db_mock.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_stuck_processing_surfaces_row_with_no_timestamp():
    """A processing row with neither updated_at nor ride_completed_at is surfaced
    (better to over-report to ops than hide a potentially stranded charge)."""
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [
        {"id": "no_ts", "payment_intent_id": None, "payment_status": "processing", "status": "completed"},
    ]

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _reconcile_stuck_processing_rides

        out = await _reconcile_stuck_processing_rides()

    assert [d["ride_id"] for d in out] == ["no_ts"]


@pytest.mark.asyncio
async def test_stuck_processing_query_failure_returns_empty():
    """A failed query is logged and yields no discrepancies (never raises)."""
    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = RuntimeError("DB down")

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _reconcile_stuck_processing_rides

        out = await _reconcile_stuck_processing_rides()

    assert out == []


# ── Flag-gated auto-heal (default OFF) ──────────────────────────────────────


def _full_processing_ride(*, grand_total="25.00", tip_amount="0.00", pi_id="pi_x", authorized_amount=None) -> dict:
    return {
        "id": "stuck",
        "payment_status": "processing",
        "payment_intent_id": pi_id,
        "grand_total": grand_total,
        "total_fare": grand_total,
        "tip_amount": tip_amount,
        "authorized_amount": authorized_amount,
        "driver_earnings": "25.00",
        "status": "completed",
    }


def _heal_stripe_mock(status: str = "succeeded", amount_received: int = 2500) -> MagicMock:
    m = MagicMock()
    m.PaymentIntent.retrieve.return_value = {"id": "pi_x", "status": status, "amount_received": amount_received}
    return m


@pytest.mark.asyncio
async def test_auto_heal_disabled_by_default_is_noop():
    """Flag absent → no Stripe lookup, no DB write; rides stay detection-only."""
    db_mock = AsyncMock()
    stripe_mock = _heal_stripe_mock()

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _maybe_heal_stuck_processing

        stats = await _maybe_heal_stuck_processing([{"ride_id": "stuck"}], stripe_mock, {})

    assert stats["enabled"] is False
    assert stats["healed"] == 0
    db_mock.get_ride.assert_not_awaited()
    db_mock.update_one.assert_not_awaited()
    stripe_mock.PaymentIntent.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_auto_heal_marks_paid_when_stripe_succeeded():
    """Flag ON + PI succeeded for the expected amount → atomic processing→paid."""
    db_mock = AsyncMock()
    db_mock.get_ride.return_value = _full_processing_ride()
    db_mock.update_one.return_value = {"id": "stuck"}  # claim won
    stripe_mock = _heal_stripe_mock(status="succeeded", amount_received=2500)

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _maybe_heal_stuck_processing

        stats = await _maybe_heal_stuck_processing(
            [{"ride_id": "stuck"}], stripe_mock, {"stripe_auto_heal_processing": True}
        )

    assert stats["healed"] == 1
    assert stats["healed_ride_ids"] == ["stuck"]
    # Atomic claim: filter on processing, write paid.
    filt, update = db_mock.update_one.call_args[0][1], db_mock.update_one.call_args[0][2]
    assert filt == {"id": "stuck", "payment_status": "processing"}
    assert update["payment_status"] == "paid"


@pytest.mark.asyncio
async def test_auto_heal_skips_when_pi_not_succeeded():
    """Flag ON but the charge never succeeded → never mark paid."""
    db_mock = AsyncMock()
    db_mock.get_ride.return_value = _full_processing_ride()
    stripe_mock = _heal_stripe_mock(status="requires_action")

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _maybe_heal_stuck_processing

        stats = await _maybe_heal_stuck_processing(
            [{"ride_id": "stuck"}], stripe_mock, {"stripe_auto_heal_processing": True}
        )

    assert stats["healed"] == 0
    db_mock.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_heal_skips_when_amount_short():
    """Flag ON, PI succeeded but amount_received < expected → leave for review."""
    db_mock = AsyncMock()
    db_mock.get_ride.return_value = _full_processing_ride(grand_total="25.00")
    stripe_mock = _heal_stripe_mock(status="succeeded", amount_received=2400)  # expected 2500

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _maybe_heal_stuck_processing

        stats = await _maybe_heal_stuck_processing(
            [{"ride_id": "stuck"}], stripe_mock, {"stripe_auto_heal_processing": True}
        )

    assert stats["healed"] == 0
    db_mock.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_heal_idempotent_when_claim_lost():
    """Flag ON, charge fine, but the atomic claim returns no row (process_payment
    or another replica won the race) → counted as not healed, no error."""
    db_mock = AsyncMock()
    db_mock.get_ride.return_value = _full_processing_ride()
    db_mock.update_one.return_value = None  # zero rows — already finalised
    stripe_mock = _heal_stripe_mock(status="succeeded", amount_received=2500)

    with patch("utils.stripe_reconcile.db_supabase", db_mock):
        from utils.stripe_reconcile import _maybe_heal_stuck_processing

        stats = await _maybe_heal_stuck_processing(
            [{"ride_id": "stuck"}], stripe_mock, {"stripe_auto_heal_processing": True}
        )

    assert stats["healed"] == 0
