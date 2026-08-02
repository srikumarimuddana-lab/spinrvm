"""Targeted branch coverage for routes/rides/booking.py's create_ride —
the remaining ~2pp gap after PR #2538 covered _insert_ride_with_code and
_prep_and_dispatch. Complements the existing create_ride tests in
test_coverage_rides.py (banned/suspended user, card requirements, existing
active ride, idempotency, full happy path) and test_corporate_ride_payment.py.

Covers guard clauses this pass had not yet reached: the service_areas fetch
failure, insufficient wallet balance, the pre-dispatch corporate policy
check (both the failure and the corporate-member-id resolution), the
work_profile corporate pre-dispatch block (no membership / policy violation
/ low allowance), the SCA two-step first-leg early return, the idempotent
DB-level replay early return, and calculate_all_fees failing mid-booking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-cr-1"
_USER = {"id": _RIDER_ID}

_FARE_INFO = {
    "vehicle_type": {"id": "vt-1", "name": "Standard"},
    "per_km_rate": 1.5,
    "per_minute_rate": 0.25,
    "booking_fee": 2.0,
    "base_fare": 3.0,
    "minimum_fare": 5.0,
    "surge_multiplier": 1.0,
}


def _starlette_request(method="POST", path="/rides"):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }
    return SR(scope)


def _body(**kw):
    from backend.schemas import CreateRideRequest

    defaults = dict(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


async def test_service_area_fetch_failure_raises_503():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        # First two get_rows calls are the existing-active-ride and unpaid-ride
        # checks (both empty); the third is the service_areas fetch, which fails.
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503


async def test_insufficient_wallet_balance_raises_400():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _find_one(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 0.5}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_find_one)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="wallet"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 400
    assert "Insufficient wallet balance" in exc_info.value.detail


async def test_corporate_policy_check_failure_raises_403():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _FailedPolicy:
        passed = False
        failed_rules = ["max_fare_per_ride", "time_window"]

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_FailedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # require_company_bookable() does its own `from .. import db_supabase`
        # local import, which reaches the real backend.db_supabase singleton —
        # patching `_deps.db_supabase` (a whole-module replace above) does not
        # intercept that call, so the singleton's attribute must be patched too.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(
                    payment_method="company_allowance",
                    corporate_account_id="corp-1",
                ),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 403
    assert "max_fare_per_ride" in exc_info.value.detail["failed_rules"]
    assert "Fare exceeds company limit per ride." in exc_info.value.detail["message"]


async def test_corporate_policy_passes_resolves_member_id():
    """When the pre-dispatch policy check passes, the active corporate_members
    row's id must be tagged onto the ride (corporate_member_id)."""
    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _get_rows(table, *a, **kw):
            if table == "corporate_members":
                return [{"id": "corp-member-9"}]
            return []

        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})
        inserted = {"id": "ride-cr-1", "status": "searching"}
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})

        await create_ride(
            request=_starlette_request(),
            body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
            current_user=_USER,
        )

    inserted_payload = mock_supabase.insert_ride.await_args.args[0]
    assert inserted_payload["corporate_member_id"] == "corp-member-9"


async def test_corporate_policy_passes_but_no_active_membership_fails_closed():
    """Gap #3: the policy check can pass with no active membership at all
    (e.g. the company has no policy row, or allowed_payment_source isn't
    allowance_only) — a removed/never-a-member rider must still be blocked
    at booking time, not silently create a company_allowance ride that only
    fails later at settlement."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])  # no active corporate_members row
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["membership_inactive"]


async def test_corporate_policy_no_active_membership_allowed_when_flag_disabled():
    """Rollback path: corporate_member_removal_blocks_booking=False must
    fully restore the old (fail-open) behavior without a redeploy."""
    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch(
            "backend.routes.rides._deps.get_app_settings",
            AsyncMock(return_value={"corporate_member_removal_blocks_booking": False}),
        ),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})
        inserted = {"id": "ride-cr-2", "status": "searching"}
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})

        await create_ride(
            request=_starlette_request(),
            body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
            current_user=_USER,
        )

    inserted_payload = mock_supabase.insert_ride.await_args.args[0]
    assert "corporate_member_id" not in inserted_payload
    assert inserted_payload["corporate_account_id"] == "corp-1"


async def test_work_profile_without_membership_raises_400():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _wallet_ok(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_wallet_ok)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.list_active_memberships_for_user = AsyncMock(return_value=[])
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(
                    payment_method="wallet",
                    work_profile=True,
                    corporate_account_id="corp-1",
                ),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "no_corporate_membership"


async def test_work_profile_policy_violation_raises_400():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _FailedPolicy:
        passed = False
        failed_rules = ["allowed_payment_source"]
        allowance = {"type": "unlimited"}
        policy = {}

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_FailedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _wallet_ok(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_wallet_ok)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.list_active_memberships_for_user = AsyncMock(
            return_value=[{"company_id": "corp-1", "policy_override": False}]
        )
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="wallet", work_profile=True, corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "policy_violation"


async def test_work_profile_low_allowance_raises_400():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []
        allowance = {"type": "monthly", "amount": "1.00", "used": "0.00"}
        policy = {"allowed_payment_source": "allowance_only"}

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _wallet_ok(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_wallet_ok)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})
        mock_supabase.list_active_memberships_for_user = AsyncMock(
            return_value=[{"company_id": "corp-1", "policy_override": False}]
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="wallet", work_profile=True, corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "allowance_low"


async def test_sca_preauth_requires_action_returns_early_without_creating_ride():
    from backend.routes.rides import create_ride

    class _RequiresAction:
        requires_action = True
        client_secret = "secret_abc"
        payment_intent_id = "pi_abc"

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch(
            "backend.routes.rides.booking._preauthorize_ride_card",
            AsyncMock(return_value=_RequiresAction()),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.insert_ride = AsyncMock()

        result = await create_ride(
            request=_starlette_request(),
            body=_body(payment_method="card", payment_method_id="pm_1"),
            current_user=_USER,
        )

    assert result["requires_action"] is True
    assert result["payment_authorization"]["client_secret"] == "secret_abc"
    # No ride was ever inserted -- the state machine must stay clean until
    # the client re-books with preauthorized_payment_intent_id.
    mock_supabase.insert_ride.assert_not_called()


async def test_idempotency_key_replay_returns_existing_ride_without_recharging():
    from backend.routes.rides import create_ride

    existing_ride = {"id": "ride-existing-1", "status": "searching"}

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
    ):
        mock_supabase.find_one = AsyncMock(return_value=existing_ride)

        req = _starlette_request()
        req.headers._list.append((b"idempotency-key", b"idem-key-123"))

        result = await create_ride(request=req, body=_body(), current_user=_USER)

    assert result == existing_ride


async def test_calculate_all_fees_failure_raises_503():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(side_effect=RuntimeError("fees db down")),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="wallet"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 503


async def test_suspended_company_blocks_company_allowance_booking():
    """Corporate module lifecycle audit Finding 1: neither suspending nor
    closing a company stopped it from being used for BRAND NEW bookings —
    only the rides that already existed at the moment of suspension were
    cancelled (corporate_suspension_service). A suspended company's still-
    active member must not be able to create another company_allowance ride
    until the company is reactivated."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "suspended"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        async def _get_rows_member(table, *a, **kw):
            if table == "corporate_members":
                return [{"id": "corp-member-9"}]
            return []

        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows_member)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "suspended"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["company_inactive"]


async def test_closed_company_blocks_company_allowance_booking():
    """Same as suspended — a closed (terminal) company must also be blocked,
    and arguably matters more since gap #2 may have already refunded the
    company's wallet balance to zero, so a booking that slipped through would
    otherwise silently fall to payment_status='pending' at settlement."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "closed"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        async def _get_rows_member(table, *a, **kw):
            if table == "corporate_members":
                return [{"id": "corp-member-9"}]
            return []

        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows_member)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "closed"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["company_inactive"]


async def test_active_company_allows_company_allowance_booking():
    """Control case: an active company must be entirely unaffected by the
    new guard."""
    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        # require_company_bookable() reaches the real backend.db_supabase
        # singleton via its own local import, bypassing the whole-module
        # `_deps.db_supabase` replace above.
        patch("backend.db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        async def _get_rows_member(table, *a, **kw):
            if table == "corporate_members":
                return [{"id": "corp-member-9"}]
            return []

        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows_member)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "active"})
        inserted = {"id": "ride-cr-active", "status": "searching"}
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})

        await create_ride(
            request=_starlette_request(),
            body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
            current_user=_USER,
        )

    inserted_payload = mock_supabase.insert_ride.await_args.args[0]
    assert inserted_payload["corporate_member_id"] == "corp-member-9"


async def test_suspended_company_booking_allowed_when_flag_disabled():
    """Rollback path: corporate_inactive_company_blocks_booking=False must
    fully restore the old (fail-open) behavior without a redeploy."""
    from backend.routes.rides import create_ride

    class _PassedPolicy:
        passed = True
        failed_rules = []

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.evaluate_policy_for_ride", AsyncMock(return_value=_PassedPolicy())),
        patch(
            "backend.routes.rides._deps.get_app_settings",
            AsyncMock(return_value={"corporate_inactive_company_blocks_booking": False}),
        ),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        async def _get_rows_member(table, *a, **kw):
            if table == "corporate_members":
                return [{"id": "corp-member-9"}]
            return []

        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows_member)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "suspended"})
        inserted = {"id": "ride-cr-flagoff", "status": "searching"}
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})

        await create_ride(
            request=_starlette_request(),
            body=_body(payment_method="company_allowance", corporate_account_id="corp-1"),
            current_user=_USER,
        )

    inserted_payload = mock_supabase.insert_ride.await_args.args[0]
    assert inserted_payload["corporate_member_id"] == "corp-member-9"


async def test_work_profile_blocks_booking_for_suspended_company():
    """Same Finding 1 guard on the second corporate booking path — work_profile
    rides resolve membership via list_active_memberships_for_user, which only
    filters on the MEMBER's own status, never the company's, so without this
    guard a suspended company's still-active members could keep booking
    work_profile rides indefinitely."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _wallet_ok(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_wallet_ok)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.list_active_memberships_for_user = AsyncMock(
            return_value=[{"company_id": "corp-1", "policy_override": False}]
        )
        mock_supabase.get_corporate_account_by_id = AsyncMock(return_value={"status": "suspended"})

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(
                request=_starlette_request(),
                body=_body(payment_method="wallet", work_profile=True, corporate_account_id="corp-1"),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "company_inactive"
