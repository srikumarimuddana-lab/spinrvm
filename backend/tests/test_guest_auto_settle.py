"""Server-side settlement for corporate guest rides.

Guests have no app and never call /process-payment: completion spawns
payment_service.auto_settle_guest_corporate, and the payment-retry sweep
re-drives strays. The atomic pending→processing claim is the replay-safety
contract — these tests pin it.

Run:
    pytest backend/tests/test_guest_auto_settle.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

_PS = "backend.services.payment_service."
_RIDE_ID = "ride_guest_settle"


def _guest_ride(**overrides):
    ride = {
        "id": _RIDE_ID,
        "rider_id": "guest_u1",
        "status": "completed",
        "payment_status": "pending",
        "payment_method": "company_allowance",
        "guest_booking": True,
        "corporate_account_id": "company_gb",
        "corporate_member_id": "member_booker_gb",
        "total_fare": 15.64,
        "grand_total": 16.42,
        "tip_amount": 0,
    }
    ride.update(overrides)
    return ride


def _patches(*, ride, claim_results=None, settle=None):
    claim = AsyncMock(side_effect=claim_results if claim_results is not None else [{"id": _RIDE_ID}])
    return {
        _PS + "db_supabase.get_ride": AsyncMock(return_value=ride),
        _PS + "db_supabase.update_one": claim,
        _PS + "settle_corporate": settle
        # Mirror the real PaymentResult dataclass, not just the fields the
        # assertions read. auto_settle_guest_corporate now also inspects
        # already_paid/charged_amount to decide whether to emit a Meta
        # Purchase conversion, and a stub thinner than the real return type
        # fails as an AttributeError that looks like a production bug.
        or AsyncMock(
            return_value=__import__("types").SimpleNamespace(
                success=True,
                error=None,
                status_code=200,
                already_paid=False,
                charged_amount="0.00",
            )
        ),
    }


def _start(patch_dict):
    patchers, mocks = [], {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


@pytest.mark.anyio
async def test_non_guest_ride_is_ignored():
    from backend.services.payment_service import auto_settle_guest_corporate

    patchers, mocks = _start(_patches(ride=_guest_ride(guest_booking=False)))
    try:
        result = await auto_settle_guest_corporate(_RIDE_ID)
    finally:
        for p in patchers:
            p.stop()
    assert result is None
    mocks[_PS + "db_supabase.update_one"].assert_not_called()
    mocks[_PS + "settle_corporate"].assert_not_called()


@pytest.mark.anyio
async def test_incomplete_ride_never_settles():
    from backend.services.payment_service import auto_settle_guest_corporate

    patchers, mocks = _start(_patches(ride=_guest_ride(status="in_progress")))
    try:
        result = await auto_settle_guest_corporate(_RIDE_ID)
    finally:
        for p in patchers:
            p.stop()
    assert result is None
    mocks[_PS + "db_supabase.update_one"].assert_not_called()


@pytest.mark.anyio
async def test_lost_claim_race_noops():
    """Both pending and failed claims match zero rows → another replica won."""
    from backend.services.payment_service import auto_settle_guest_corporate

    patchers, mocks = _start(_patches(ride=_guest_ride(), claim_results=[None, None]))
    try:
        result = await auto_settle_guest_corporate(_RIDE_ID)
    finally:
        for p in patchers:
            p.stop()
    assert result is None
    assert mocks[_PS + "db_supabase.update_one"].call_count == 2
    mocks[_PS + "settle_corporate"].assert_not_called()


@pytest.mark.anyio
async def test_happy_path_settles_with_zero_tip():
    from backend.services.payment_service import auto_settle_guest_corporate

    patchers, mocks = _start(_patches(ride=_guest_ride()))
    try:
        result = await auto_settle_guest_corporate(_RIDE_ID)
    finally:
        for p in patchers:
            p.stop()

    assert result is not None and result.success is True
    claim_call = mocks[_PS + "db_supabase.update_one"].call_args_list[0]
    assert claim_call.args[1] == {"id": _RIDE_ID, "payment_status": "pending"}
    assert claim_call.args[2]["payment_status"] == "processing"

    settle_kwargs = mocks[_PS + "settle_corporate"].call_args.kwargs
    assert settle_kwargs["tip_amount"] == Decimal("0"), "guests cannot tip"
    assert settle_kwargs["total_charge"] == Decimal("16.42"), "settles the grand total"


@pytest.mark.anyio
async def test_unexpected_crash_releases_claim():
    from backend.services.payment_service import auto_settle_guest_corporate

    deps = _patches(
        ride=_guest_ride(),
        claim_results=[{"id": _RIDE_ID}, {"id": _RIDE_ID}],
        settle=AsyncMock(side_effect=RuntimeError("boom")),
    )
    patchers, mocks = _start(deps)
    try:
        result = await auto_settle_guest_corporate(_RIDE_ID)
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    release_call = mocks[_PS + "db_supabase.update_one"].call_args_list[-1]
    assert release_call.args[1] == {"id": _RIDE_ID, "payment_status": "processing"}
    assert release_call.args[2]["payment_status"] == "pending"


@pytest.mark.anyio
async def test_retry_sweep_drives_stuck_guest_rides():
    from backend.utils.payment_retry import sweep_guest_corporate_settlements

    with (
        patch(
            "backend.utils.payment_retry.db.get_rows",
            AsyncMock(return_value=[{"id": "r1"}, {"id": "r2"}]),
        ) as rows,
        patch(
            "backend.services.payment_service.auto_settle_guest_corporate",
            AsyncMock(return_value=None),
        ) as settle,
    ):
        await sweep_guest_corporate_settlements()

    filters = rows.call_args.args[1]
    assert filters["guest_booking"] is True
    assert filters["payment_method"] == "company_allowance"
    assert filters["payment_status"] == "pending"
    assert filters["status"] == "completed"
    assert "$lte" in filters["ride_completed_at"]
    assert settle.await_count == 2
