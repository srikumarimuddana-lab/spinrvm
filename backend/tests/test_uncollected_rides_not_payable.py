"""Uncollected fares must never become driver-payable money (2026-09-20 C1).

A completed ride whose card charge failed keeps its ``driver_earnings``
(``utils/payment_retry.py`` gives up at ``payment_status='failed'`` and never
touches the earnings column). Before this fix every driver-payable surface —
``/drivers/balance``, the weekly ``auto_payout`` batch, and driver statements —
summed ``status='completed'`` rides with no ``payment_status`` predicate, so
that fare flowed into ``payable_balance`` and out through a real Stripe
Transfer. With 0% commission there is nothing to net it against.

The three surfaces now spread ``utils.payment_collection.ONLY_COLLECTED_RIDES``
into their money query. The mock ``get_rows`` here honours that predicate the
way PostgREST would (``payment_status IN (...)``), so each test proves both
that the filter is sent AND that the math excludes the uncollected ride —
a test that only pinned the dict shape would pass vacuously if a caller
dropped the spread.

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
    drop_uncollected_rides,
    is_collected,
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
    allowed = filters["payment_status"]["$in"]
    return ride.get("payment_status") in allowed


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
# The constant itself
# ---------------------------------------------------------------------------


def test_collected_set_is_every_post_charge_status():
    """routes/webhooks.py's 'money is in' set, plus the post-collection
    dispute states: a chargeback on a charge that succeeded must not silently
    pull an already-paid-out ride back out of the driver's balance."""
    assert set(COLLECTED_PAYMENT_STATUSES) == {
        "paid",
        "waived_admin",
        "refunded",
        "partially_refunded",
        "disputed",
        "dispute_lost",
    }
    assert ONLY_COLLECTED_RIDES == {"payment_status": {"$in": list(COLLECTED_PAYMENT_STATUSES)}}


@pytest.mark.parametrize(
    "status,expected",
    [
        ("paid", True),
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


# ---------------------------------------------------------------------------
# /drivers/balance
# ---------------------------------------------------------------------------


async def test_balance_excludes_failed_and_pending_rides():
    from backend.routes.drivers import get_driver_balance

    captured: list[dict] = []
    with (
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
    # The money query carried the predicate (the mock enforced it above).
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


# ---------------------------------------------------------------------------
# utils/auto_payout — the weekly Stripe Transfer batch
# ---------------------------------------------------------------------------


async def test_compute_payable_balance_excludes_uncollected_rides():
    captured: list[dict] = []
    with (
        patch.object(auto_payout.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))),
        patch.object(auto_payout.db_supabase, "supabase") as mock_supabase,
    ):
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        balance = await auto_payout._compute_payable_balance(DRIVER_ID)

    assert balance == Decimal("21.00")
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


async def test_compute_payable_balances_batch_excludes_uncollected_rides():
    captured: list[dict] = []

    async def batched(table, column, ids, extra_filters=None, **kw):
        if table == "rides":
            if extra_filters and extra_filters.get("status") == "cancelled":
                return []
            captured.append(dict(extra_filters or {}))
            return [r for r in ALL_RIDES if _matches_collected(extra_filters, r)]
        return []

    with patch.object(auto_payout.db_supabase, "get_rows_batched_in", AsyncMock(side_effect=batched)):
        balances = await auto_payout._compute_payable_balances_batch([DRIVER_ID])

    assert balances == {DRIVER_ID: Decimal("21.00")}
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]


# ---------------------------------------------------------------------------
# routes/drivers/tax_exports — the CRA-facing T4A slip
# ---------------------------------------------------------------------------


async def test_t4a_summary_excludes_uncollected_rides():
    """get_rides_for_driver has no filter-dict, so the slip filters rows
    post-fetch via drop_uncollected_rides — a fare the driver never received
    must not be reported as income."""
    from backend.routes.drivers import get_t4a_summary

    async def _get_rows(table, filters=None, **kw):
        if table == "drivers":
            return [{"id": DRIVER_ID, "user_id": USER_ID}]
        return []

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=list(ALL_RIDES))),
    ):
        result = await get_t4a_summary(year=2026, current_user={"id": USER_ID})

    assert result["total_earnings"] == "20.00"
    assert result["total_trips"] == 1


# ---------------------------------------------------------------------------
# utils/driver_statement
# ---------------------------------------------------------------------------


async def test_statement_excludes_uncollected_rides():
    captured: list[dict] = []
    with patch.object(driver_statement.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows(ALL_RIDES, captured))):
        out = await driver_statement.build_statement({"id": DRIVER_ID}, "weekly", date(2026, 7, 20))

    assert out["earnings"]["ride_earnings"] == "20.00"
    assert out["earnings"]["tax_collected"] == "1.00"
    assert out["trips"] == 1
    assert captured and captured[0]["payment_status"] == ONLY_COLLECTED_RIDES["payment_status"]
