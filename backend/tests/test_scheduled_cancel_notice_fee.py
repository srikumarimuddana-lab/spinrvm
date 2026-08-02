"""Unit tests for calculate_scheduled_cancel_notice_fee (Finding #01,
scheduled-rides gap review) -- the notice-window cancellation fee for a
PRE-DISPATCH scheduled ride, distinct from calculate_cancellation_fee
(which handles dispatched, driver-assigned rides).

Pure-function tests only; the charging/wiring integration lives in
test_ride_cancellation_branches.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal


def _settings(**overrides):
    base = {
        "scheduled_ride_notice_window_fee_enabled": True,
        "scheduled_ride_notice_window_minutes": 60,
        "scheduled_ride_notice_window_fee_amount": "3.00",
    }
    base.update(overrides)
    return base


def _ride(minutes_until_pickup: float, **overrides) -> dict:
    scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=minutes_until_pickup)
    base = {
        "id": "ride-notice-1",
        "scheduled_time": scheduled_time.isoformat(),
        "payment_method": "card",
    }
    base.update(overrides)
    return base


def _calc(ride, settings):
    from backend.services.cancellation_service import calculate_scheduled_cancel_notice_fee

    return calculate_scheduled_cancel_notice_fee(ride, settings)


def test_flag_disabled_is_always_free():
    ride = _ride(minutes_until_pickup=10)  # well inside the window
    fee = _calc(ride, _settings(scheduled_ride_notice_window_fee_enabled=False))
    assert fee == Decimal("0")


def test_inside_window_charges_the_configured_amount():
    ride = _ride(minutes_until_pickup=30)  # inside the 60-min window
    fee = _calc(ride, _settings())
    assert fee == Decimal("3.00")


def test_outside_window_is_free():
    ride = _ride(minutes_until_pickup=90)  # outside the 60-min window
    fee = _calc(ride, _settings())
    assert fee == Decimal("0")


def test_exactly_at_window_boundary_charges_fee():
    """Free requires STRICTLY more than the window's notice (policy: "free
    if cancelled >60 min before pickup") -- exactly at 60 minutes out is
    still inside the fee window, not free."""
    ride = _ride(minutes_until_pickup=60)
    fee = _calc(ride, _settings())
    assert fee == Decimal("3.00")


def test_just_inside_window_charges_fee():
    ride = _ride(minutes_until_pickup=59.9)
    fee = _calc(ride, _settings())
    assert fee == Decimal("3.00")


def test_corporate_paid_ride_is_always_free():
    """Mirrors calculate_cancellation_fee's card-branch exclusion: a
    corporate-paid fee belongs on the company wallet ledger, not wired up
    here -- so it must never be charged via this path."""
    ride = _ride(minutes_until_pickup=5, payment_method="company_allowance")
    fee = _calc(ride, _settings())
    assert fee == Decimal("0")


def test_missing_scheduled_time_is_free_not_an_error():
    ride = {"id": "ride-notice-1", "payment_method": "card"}
    fee = _calc(ride, _settings())
    assert fee == Decimal("0")


def test_unparseable_scheduled_time_is_free_not_an_error():
    ride = {"id": "ride-notice-1", "payment_method": "card", "scheduled_time": "not-a-real-timestamp"}
    fee = _calc(ride, _settings())
    assert fee == Decimal("0")


def test_pickup_time_already_passed_is_free():
    """Should be unreachable via the normal cancel flow (the dispatcher
    would already have claimed the ride), but must never charge against a
    stale/past timestamp if it somehow is."""
    ride = _ride(minutes_until_pickup=-5)
    fee = _calc(ride, _settings())
    assert fee == Decimal("0")


def test_custom_window_and_amount_from_settings():
    ride = _ride(minutes_until_pickup=20)
    fee = _calc(ride, _settings(scheduled_ride_notice_window_minutes=15, scheduled_ride_notice_window_fee_amount="5.00"))
    assert fee == Decimal("0")  # 20 min out is outside a 15-min window

    ride2 = _ride(minutes_until_pickup=10)
    fee2 = _calc(ride2, _settings(scheduled_ride_notice_window_minutes=15, scheduled_ride_notice_window_fee_amount="5.00"))
    assert fee2 == Decimal("5.00")
