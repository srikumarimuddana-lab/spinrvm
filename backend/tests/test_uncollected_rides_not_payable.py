"""Uncollected fares must not become driver-payable money (2026-09-20 C1).

A completed ride whose card charge failed keeps its ``driver_earnings``
(``utils/payment_retry.py`` gives up at ``payment_status='failed'`` and never
touches the earnings column). Without a filter, every driver-payable surface —
``/drivers/balance``, the weekly ``auto_payout`` batch, driver statements —
sums ``status='completed'`` rides with no ``payment_status`` predicate, so that
fare flows into ``payable_balance`` and out through a real Stripe Transfer.
With 0% commission there is nothing to net it against.

**The filter is flag-gated and OFF by default**, so these tests come in pairs:
with the flag off nothing changes (the historical behaviour every already-paid
driver depends on), and with it on the uncollected ride drops out. See
``utils.payment_collection.payable_ride_filter`` for why switching it on is
not a blind change — ``payable_balance`` is a live recompute with no floor, so
a driver already paid out for an uncollected ride goes negative.

The mock ``get_rows`` honours the predicate the way PostgREST would
(``payment_status IN (...)``), so each test proves both that the filter is
sent AND that the math changes — a test that only pinned the dict shape would
pass vacuously if a caller dropped the spread.

Patch targets follow test_earnings_coverage.py / test_auto_payout.py /
test_driver_statement.py: ``db_supabase`` is one shared module object, so
patching ``backend.db_supabase.<fn>`` covers every importer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import auto_payout, driver_statement
from backend.utils.payment_collection import (
    COLLECTED_PAYMENT_STATUSES,
    ONLY_COLLECTED_RIDES,
    PAYABLE_FILTER_FLAG,
    drop_uncollected_rides,
    is_collected,
    payable_ride_filter,
)

pytestmark = pytest.mark.anyio

USER_ID = "user_uncollected"
DRIVER_ID = "driver_uncollected"


def _ride(ride_id: str, payment_status: str, earnings: str, tax: str = "0") -> dict:
    return {
        "id": ride_id,
        "driver_id": DRIVER_ID,
        "status": "completed",
        "payment_status": payment_status,
        "driver_earnings": earnings,
        "tax_amount": tax,
        "tip_amount": 0,
        "ride_completed_at": "2026-07-21T12:00:00+00:00",
    }


PAID = _ride("r-paid", "paid", "20.00", tax="1.00")
FAILED = _ride("r-failed", "failed", "30.00", tax="1.50")
PENDING = _ride("r-pending", "pending", "40.00")
ALL_RIDES = [PAID, FAILED, PENDING]


def _matches_collected(filters: dict | None, ride: dict) -> bool:
    """Apply the ONLY_COLLECTED_RIDES predicate the way PostgREST would."""
    if not filters or "payment_status" not in filters:
        return True
    return ride.get("payment_status") in filters["payment_status"]["$in"]


def _get_rows(rides: list[dict], captured: list[dict]):
    async def get_rows(table, filters=None, **kw):
        if table == "drivers":
            return [{"id": DRIVER_ID, "user_id": USER_ID}]
        if table == "rides":
            if filters and filters.get("status") == "cancelled":
                return []
            captured.append(dict(filters or {}))
            return [r for r in rides if _matches_collected(filters, r)]
        return []

    return get_rows


# ---------------------------------------------------------------------------
# The constant and the flag gate
# ---------------------------------------------------------------------------


def test_collected_set_is_every_post_charge_status():
    """Every status a ride can hold after a successful charge. Includes the
    dispute states (a chargeback lands on a charge that succeeded, usually
    after payout — dropping those would pull an already-paid ride out of a
    live-recomputed balance) and `succeeded`, the raw Stripe status
    routes/payments.py used to persist verbatim."""
    assert set(COLLECTED_PAYMENT_STATUSES) == {
        "paid",
        "succeeded",
        "waived_admin",
        "refunded",
        "partially_refunded",
        "disputed",
        "dispute_lost",
    }
    assert ONLY_COLLECTED_RIDES == {"payment_status": {"$in": list(COLLECTED_PAYMENT_STATUSES)}}


def test_webhooks_settled_guard_is_the_same_set():
    """routes/webhooks.py's already-settled guard must not drift back to a
    narrower tuple: a ride at partially_refunded/disputed/dispute_lost that
    misses that guard gets CAS'd to 'failed' by a stale payment_failed
    redelivery, turning a collected ride into an uncollected one."""
    from backend.routes import webhooks

    assert set(webhooks._SETTLED_PAYMENT_STATUSES) == set(COLLECTED_PAYMENT_STATUSES)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("paid", True),
        ("succeeded", True),
        ("waived_admin", True),
        ("refunded", True),
        ("partially_refunded", True),
        ("disputed", True),
        ("dispute_lost", True),
        ("failed", False),
        ("pending", False),
        ("processing", False),
        ("retrying", False),
        ("requires_action", False),
        ("held_for_review", False),
        ("", False),
        (None, False),
    ],
)
def test_is_collected(status, expected):
    assert is_collected({"payment_status": status}) is expected


def test_drop_uncollected_rides_keeps_only_collected():
    assert drop_uncollected_rides(ALL_RIDES) == [PAID]


async def test_filter_is_off_by_default():
    assert await payable_ride_filter({}) == {}
    assert await payable_ride_filter({PAYABLE_FILTER_FLAG: False}) == {}


async def test_filter_is_on_when_flagged():
    assert await payable_ride_filter({PAYABLE_FILTER_FLAG: True}) == ONLY_COLLECTED_RIDES


async def test_filter_fails_closed_when_settings_unreadable():
    """A flag-read failure must never be the reason a driver's balance moves."""
    with patch("backend.settings_loader.get_app_settings", AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await payable_ride_filter() == {}


# ---------------------------------------------------------------------------
# /drivers/balance
# ---------------------------------------------------------------------------


async def test_balance_unchanged_when_flag_off():
    """The default path: every completed ride still counts, so no driver's
    balance moves on deploy."""
    from backend.routes.drivers import get_driver_balance

    captured: list[dict] = []
    with (
        patch("backend.routes.drivers.earnings.payable_ride_filter", AsyncMock(return_value={})),
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=len(ALL_RIDES))),
        patch("backend.db_supabase.supabase") as mock_supabase,
    ):
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        result = await get_driver_balance(current_user={"id": USER_ID})

    # 20.00 + 1.00 + 30.00 + 1.50 + 40.00
    assert result["payable_balance"] == "92.50"
    assert captured and "payment_status" not in captured[0]


async def test_balance_excludes_failed_and_pending_rides_when_flag_on():
    from backend.routes.drivers import get_driver_balance

    captured: list[dict] = []
    with (
        patch(
            "backend.routes.drivers.earnings.payable_ride_filter",
            AsyncMock(return_value=dict(ONLY_COLLECTED_RIDES)),
        ),
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=len(ALL_RIDES))),
        patch("backend.db_supabase.supabase") as mock_supabase,
    ):
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        result = await get_driver_balance(current_user={"id": USER_ID})

    # Only the paid ride's earnings + tax are payable: 20.00 + 1.00.
    assert result["payable_balance"] == "21.00"
    assert result["total_earnings"] == "21.00"
    # Activity count is NOT money — it stays over every completed ride.
    assert result["total_rides"] == 3
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


# ---------------------------------------------------------------------------
# utils/auto_payout — the weekly Stripe Transfer batch
# ---------------------------------------------------------------------------


async def test_compute_payable_balance_excludes_uncollected_rides_when_flag_on():
    captured: list[dict] = []
    with (
        patch.object(auto_payout, "payable_ride_filter", AsyncMock(return_value=dict(ONLY_COLLECTED_RIDES))),
        patch.object(auto_payout.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
        patch.object(auto_payout.db_supabase, "supabase") as mock_supabase,
    ):
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        balance = await auto_payout._compute_payable_balance(DRIVER_ID)

    assert balance == Decimal("21.00")
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


async def test_compute_payable_balance_unchanged_when_flag_off():
    captured: list[dict] = []
    with (
        patch.object(auto_payout, "payable_ride_filter", AsyncMock(return_value={})),
        patch.object(auto_payout.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
        patch.object(auto_payout.db_supabase, "supabase") as mock_supabase,
    ):
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        balance = await auto_payout._compute_payable_balance(DRIVER_ID)

    assert balance == Decimal("92.50")
    assert captured and "payment_status" not in captured[0]


async def test_compute_payable_balances_batch_excludes_uncollected_rides_when_flag_on():
    captured: list[dict] = []

    async def batched(table, column, ids, extra_filters=None, **kw):
        if table == "rides":
            if extra_filters and extra_filters.get("status") == "cancelled":
                return []
            captured.append(dict(extra_filters or {}))
            return [r for r in ALL_RIDES if _matches_collected(extra_filters, r)]
        return []

    with (
        patch.object(auto_payout, "payable_ride_filter", AsyncMock(return_value=dict(ONLY_COLLECTED_RIDES))),
        patch.object(auto_payout.db_supabase, "get_rows_batched_in", AsyncMock(side_effect=batched)),
    ):
        balances = await auto_payout._compute_payable_balances_batch([DRIVER_ID])

    assert balances == {DRIVER_ID: Decimal("21.00")}
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


# ---------------------------------------------------------------------------
# utils/driver_statement
# ---------------------------------------------------------------------------


async def test_statement_excludes_uncollected_rides_when_flag_on():
    captured: list[dict] = []
    with (
        patch.object(driver_statement, "payable_ride_filter", AsyncMock(return_value=dict(ONLY_COLLECTED_RIDES))),
        patch.object(driver_statement.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
    ):
        out = await driver_statement.build_statement({"id": DRIVER_ID}, "weekly", date(2026, 7, 20))

    assert out["earnings"]["ride_earnings"] == "20.00"
    assert out["earnings"]["tax_collected"] == "1.00"
    assert out["trips"] == 1
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


async def test_statement_unchanged_when_flag_off():
    captured: list[dict] = []
    with (
        patch.object(driver_statement, "payable_ride_filter", AsyncMock(return_value={})),
        patch.object(driver_statement.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
    ):
        out = await driver_statement.build_statement({"id": DRIVER_ID}, "weekly", date(2026, 7, 20))

    assert out["trips"] == 3
    assert captured and "payment_status" not in captured[0]
