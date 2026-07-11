"""Tests for corporate ride payment wiring — Plans 4 & 5.

Tests for:
  A. create_ride() pre-dispatch corporate check (work_profile=True path)
  B. process_payment() company_allowance branch

Uses app.dependency_overrides for auth and AsyncMock patches for all I/O.
MagicMock is used for supabase-layer calls (run_sync pattern); AsyncMock for
higher-level async DB helpers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_FAKE_USER = {"id": "rider_1", "phone": "+15550001111", "status": "active"}

_BASE_RIDE_BODY = {
    "vehicle_type_id": "vt_standard",
    "pickup_address": "123 Main St, Saskatoon",
    "pickup_lat": 52.1332,
    "pickup_lng": -106.6700,
    "dropoff_address": "456 Park Ave, Saskatoon",
    "dropoff_lat": 52.1500,
    "dropoff_lng": -106.6500,
    "payment_method": "card",
}

_CORP_COMPANY_ID = "company_abc"
_MEMBER_ID = "member_xyz"
_ALLOWANCE_ID = "allow_1"
_WALLET_ID = "wallet_1"
_RIDE_ID = "ride_99"


_APP_CHECK_HEADERS = {"X-Firebase-AppCheck": "test-token"}

_mock_app_check = MagicMock()
_mock_app_check.verify_token = MagicMock(return_value=None)


@pytest.fixture
def rider_override():
    """Install a fake current_user and bypass Firebase App Check for /api/* routes."""
    import sys

    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    # App Check middleware verifies X-Firebase-AppCheck on all /api/* paths.
    # Inject a stub module so verify_token always succeeds in tests.
    _orig = sys.modules.get("firebase_admin.app_check")
    sys.modules["firebase_admin.app_check"] = _mock_app_check
    yield
    app.dependency_overrides.pop(get_current_user, None)
    if _orig is None:
        sys.modules.pop("firebase_admin.app_check", None)
    else:
        sys.modules["firebase_admin.app_check"] = _orig


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fake_fare_info():
    return {
        "vehicle_type": {"id": "vt_standard"},
        "base_fare": 5.0,
        "per_km_rate": 1.2,
        "per_minute_rate": 0.2,
        "booking_fee": 2.0,
        "minimum_fare": 6.0,
        "surge_multiplier": 1.0,
    }


def _get_rows_side_effect(*args, **kwargs):
    table = args[0] if args else ""
    if table == "service_areas":
        return []
    if table == "vehicle_types":
        return [{"id": "vt_standard", "name": "Standard", "is_active": True}]
    if table == "rides":
        return []
    return []


def _mock_create_ride_deps(*, memberships=None, allowance=None, policy=None):
    return {
        "routes.rides._deps.db_supabase.find_one": AsyncMock(
            return_value={"id": "rider_1", "status": "active", "stripe_customer_id": None}
        ),
        "routes.rides._deps.db_supabase.get_rows": AsyncMock(side_effect=_get_rows_side_effect),
        "routes.rides._deps._fares_for_location_impl": AsyncMock(return_value=[_fake_fare_info()]),
        "routes.rides._deps.calculate_airport_fee": AsyncMock(return_value={"airport_fee": 0.0}),
        "routes.rides._deps.calculate_all_fees": AsyncMock(
            return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}
        ),
        "routes.rides._deps.db_supabase.insert_ride": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.get_ride": AsyncMock(return_value=None),
        "routes.rides.matching.match_driver_to_ride": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.list_active_memberships_for_user": AsyncMock(
            return_value=memberships if memberships is not None else []
        ),
        "routes.rides._deps.db_supabase.get_member_allowance": AsyncMock(return_value=allowance or {}),
        "routes.rides._deps.db_supabase.get_corporate_policy": AsyncMock(return_value=policy or {}),
        "routes.rides._deps.db_supabase.get_corporate_wallet_by_company": AsyncMock(
            return_value={"id": _WALLET_ID, "balance": 500.0}
        ),
        "routes.rides._deps.db.find_one": AsyncMock(
            return_value={"id": "rider_1", "status": "active", "stripe_customer_id": None}
        ),
    }


def _apply_all_patches(patch_dict):
    patchers = []
    mocks = {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


# ─────────────────────────────────────────────────────────────────────────────
#  A. create_ride() — corporate pre-dispatch
# ─────────────────────────────────────────────────────────────────────────────


def test_personal_ride_skips_corporate_block(test_client, rider_override):
    """No work_profile → list_active_memberships_for_user never called."""
    deps = _mock_create_ride_deps()
    patchers, mocks = _apply_all_patches(deps)
    try:
        test_client.post("/api/v1/rides", json=_BASE_RIDE_BODY, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    mocks["routes.rides._deps.db_supabase.list_active_memberships_for_user"].assert_not_called()


def test_work_profile_without_membership_returns_400(test_client, rider_override):
    """work_profile=true but no matching membership → 400 no_corporate_membership."""
    deps = _mock_create_ride_deps(memberships=[])
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        resp = test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "no_corporate_membership"


def test_work_profile_policy_violation_returns_400(test_client, rider_override):
    """Policy violation → 400 with failed_rules list."""
    membership = {"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "policy_override": False}
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 500, "used": 0}
    policy = {"active": True, "max_fare_per_ride": 0.01}
    deps = _mock_create_ride_deps(memberships=[membership], allowance=allowance, policy=policy)
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        resp = test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["reason"] == "policy_violation"
    assert "max_fare_per_ride" in detail["failed_rules"]


def test_work_profile_allowance_low_returns_400(test_client, rider_override):
    """Remaining allowance too low and master fallback not allowed → 400 allowance_low."""
    membership = {"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "policy_override": False}
    allowance = {
        "id": _ALLOWANCE_ID,
        "type": "fixed_recurring",
        "amount": 1.00,
        "used": 0.99,
    }
    policy = {"active": True, "allowed_payment_source": "allowance_only"}
    deps = _mock_create_ride_deps(memberships=[membership], allowance=allowance, policy=policy)
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        resp = test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "allowance_low"


def test_work_profile_tags_ride_as_company_allowance(test_client, rider_override):
    """Successful corporate ride creation tags payment_method=company_allowance."""
    membership = {"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "policy_override": False}
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 500, "used": 0}
    deps = _mock_create_ride_deps(memberships=[membership], allowance=allowance, policy={})

    inserted_data = {}

    async def _capture_insert(data):
        inserted_data.update(data)
        return None

    deps["routes.rides._deps.db_supabase.insert_ride"] = _capture_insert
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert inserted_data.get("payment_method") == "company_allowance"
    assert inserted_data.get("corporate_account_id") == _CORP_COMPANY_ID


# ─────────────────────────────────────────────────────────────────────────────
#  B. process_payment() — company_allowance branch
# ─────────────────────────────────────────────────────────────────────────────


def _fake_corporate_ride(total_fare=25.0):
    return {
        "id": _RIDE_ID,
        "rider_id": "rider_1",
        "total_fare": total_fare,
        "payment_method": "company_allowance",
        "payment_status": "pending",
        "corporate_account_id": _CORP_COMPANY_ID,
        "status": "completed",
        "tip_amount": 0,
    }


def _mock_process_payment_deps(*, allowance, membership=None):
    if membership is None:
        membership = {
            "id": _MEMBER_ID,
            "company_id": _CORP_COMPANY_ID,
            "user_id": "rider_1",
            "policy_override": False,
        }
    return {
        "routes.rides._deps.db_supabase.get_ride": AsyncMock(return_value=_fake_corporate_ride()),
        "routes.rides._deps.db.update_one": AsyncMock(return_value=MagicMock(modified_count=1)),
        "routes.rides._deps.db_supabase.list_active_memberships_for_user": AsyncMock(return_value=[membership]),
        "routes.rides._deps.db_supabase.get_member_allowance": AsyncMock(return_value=allowance),
        "routes.rides._deps.db_supabase.get_corporate_wallet_by_company": AsyncMock(
            return_value={"id": _WALLET_ID, "balance": 1000.0}
        ),
        "routes.rides._deps.db_supabase.get_corporate_policy": AsyncMock(return_value={}),
        "routes.rides._deps.db_supabase.insert_one": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.update_ride": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.get_user_by_id": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.get_driver_by_id": AsyncMock(return_value=None),
        "services.payment_service.corporate_allowance_service.apply_rollback": AsyncMock(
            return_value={"transaction_id": "t1"}
        ),
        "services.payment_service.corporate_wallet_service.apply_adjustment": AsyncMock(
            return_value={"transaction_id": "t2"}
        ),
    }


def test_personal_ride_skips_corporate_payment_branch(test_client, rider_override):
    """A wallet-payment ride does not call any corporate service."""
    wallet_ride = {
        "id": _RIDE_ID,
        "rider_id": "rider_1",
        "total_fare": 20.0,
        "payment_method": "wallet",
        "payment_status": "pending",
        "status": "completed",
        "tip_amount": 0,
    }
    with (
        patch("routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=wallet_ride)),
        patch("routes.rides._deps.db.update_one", AsyncMock(return_value=MagicMock(modified_count=1))),
        patch("routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
        patch("routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch(
            "routes.wallet.get_or_create_wallet",
            AsyncMock(return_value={"id": "w1", "balance": 100.0, "is_active": True}),
        ),
        patch("routes.wallet._record_transaction", AsyncMock()),
        patch(
            "services.payment_service.corporate_allowance_service.apply_rollback",
            AsyncMock(),
        ) as mock_allowance,
    ):
        test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment", json={"tip_amount": "0"}, headers=_APP_CHECK_HEADERS
        )
    mock_allowance.assert_not_called()


def test_company_allowance_debits_allowance_fully_when_sufficient(test_client, rider_override):
    """Fare fully covered by allowance → allowance_debit=fare, master_debit=0."""
    allowance = {
        "id": _ALLOWANCE_ID,
        "type": "fixed_recurring",
        "amount": 200,
        "used": 0,
    }
    deps = _mock_process_payment_deps(allowance=allowance)
    patchers, mocks = _apply_all_patches(deps)
    try:
        test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    mocks["services.payment_service.corporate_allowance_service.apply_rollback"].assert_called_once()
    call_kwargs = mocks["services.payment_service.corporate_allowance_service.apply_rollback"].call_args.kwargs
    assert call_kwargs["amount"] == pytest.approx(25.0)
    assert call_kwargs["wallet_id"] == _WALLET_ID
    assert call_kwargs["allowance_id"] == _ALLOWANCE_ID
    assert call_kwargs["member_id"] == _MEMBER_ID

    mocks["services.payment_service.corporate_wallet_service.apply_adjustment"].assert_not_called()

    mocks["routes.rides._deps.db_supabase.insert_one"].assert_called()
    insert_call = mocks["routes.rides._deps.db_supabase.insert_one"].call_args
    assert insert_call.args[0] == "ride_payment_sources"
    row = insert_call.args[1]
    assert row["allowance_debit_amount"] == pytest.approx(25.0)
    assert row["master_fallback_amount"] == pytest.approx(0.0)


def test_company_allowance_splits_when_allowance_partial(test_client, rider_override):
    """Only $10 remaining in allowance, ride is $25 → split $10/$15."""
    allowance = {
        "id": _ALLOWANCE_ID,
        "type": "fixed_recurring",
        "amount": 100,
        "used": 90,
    }
    deps = _mock_process_payment_deps(allowance=allowance)
    patchers, mocks = _apply_all_patches(deps)
    try:
        test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    rollback_kwargs = mocks["services.payment_service.corporate_allowance_service.apply_rollback"].call_args.kwargs
    assert rollback_kwargs["amount"] == pytest.approx(10.0)

    adj_kwargs = mocks["services.payment_service.corporate_wallet_service.apply_adjustment"].call_args.kwargs
    assert adj_kwargs["amount"] == pytest.approx(-15.0)
    assert _RIDE_ID in adj_kwargs["notes"]

    row = mocks["routes.rides._deps.db_supabase.insert_one"].call_args.args[1]
    assert row["allowance_debit_amount"] == pytest.approx(10.0)
    assert row["master_fallback_amount"] == pytest.approx(15.0)


def test_company_allowance_debit_and_flag_on_allowance_only_policy(test_client, rider_override):
    """Allowance depleted + allowance_only policy → debit-and-flag, do not raise."""
    allowance = {
        "id": _ALLOWANCE_ID,
        "type": "fixed_recurring",
        "amount": 100,
        "used": 100,
    }
    deps = _mock_process_payment_deps(allowance=allowance)
    deps["routes.rides._deps.db_supabase.get_corporate_policy"] = AsyncMock(
        return_value={"allowed_payment_source": "allowance_only"}
    )
    patchers, mocks = _apply_all_patches(deps)
    try:
        resp = test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    mocks["services.payment_service.corporate_wallet_service.apply_adjustment"].assert_called_once()

    insert_calls = mocks["routes.rides._deps.db_supabase.insert_one"].call_args_list
    tables = [c.args[0] for c in insert_calls]
    assert "corporate_policy_evaluations" in tables

    assert resp.status_code == 200


def test_company_allowance_unlimited_covers_full_fare(test_client, rider_override):
    """Unlimited allowance → allowance_debit = full fare, master_debit = 0."""
    allowance = {
        "id": _ALLOWANCE_ID,
        "type": "unlimited",
        "amount": None,
        "used": 0,
    }
    deps = _mock_process_payment_deps(allowance=allowance)
    patchers, mocks = _apply_all_patches(deps)
    try:
        test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    mocks["services.payment_service.corporate_allowance_service.apply_rollback"].assert_called_once()
    mocks["services.payment_service.corporate_wallet_service.apply_adjustment"].assert_not_called()
    row = mocks["routes.rides._deps.db_supabase.insert_one"].call_args.args[1]
    assert row["master_fallback_amount"] == pytest.approx(0.0)


def test_company_allowance_missing_membership_returns_400(test_client, rider_override):
    """No matching membership for the ride's company → 400."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 200, "used": 0}
    deps = _mock_process_payment_deps(allowance=allowance)
    deps["routes.rides._deps.db_supabase.list_active_memberships_for_user"] = AsyncMock(return_value=[])
    patchers, _ = _apply_all_patches(deps)
    try:
        resp = test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
#  C. settle_corporate — payer resolution via rides.corporate_member_id
#     (guest bookings: the BOOKING EMPLOYEE pays, the rider is the company's
#     customer with no membership at all; existing tests above keep covering
#     the legacy rider-derived fallback because their ride carries no stamp)
# ─────────────────────────────────────────────────────────────────────────────

_BOOKER_MEMBER_ID = "member_booker"
_BOOKER_USER_ID = "user_booker"


def _fake_guest_corporate_ride(total_fare=25.0):
    return {
        **_fake_corporate_ride(total_fare),
        "rider_id": "guest_rider_1",  # the customer — NOT a member
        "corporate_member_id": _BOOKER_MEMBER_ID,
        "guest_booking": True,
    }


def _booker_member(status="active", company_id=_CORP_COMPANY_ID):
    return {
        "id": _BOOKER_MEMBER_ID,
        "company_id": company_id,
        "user_id": _BOOKER_USER_ID,
        "status": status,
        "policy_override": False,
    }


def _settle_patches(*, member_lookup, allowance, memberships=None):
    """Unit-level patches for calling settle_corporate directly. Targets the
    payment_service module's own references so the tests are immune to the
    routes.rides module-identity quirks the endpoint-driven tests above hit."""
    base = "backend.services.payment_service."
    return {
        base + "db_supabase.get_corporate_member_by_id": AsyncMock(return_value=member_lookup),
        base + "db_supabase.list_active_memberships_for_user": AsyncMock(
            return_value=memberships if memberships is not None else []
        ),
        base + "db_supabase.get_member_allowance": AsyncMock(return_value=allowance),
        base + "db_supabase.get_corporate_wallet_by_company": AsyncMock(
            return_value={"id": _WALLET_ID, "balance": 1000.0}
        ),
        base + "db_supabase.get_corporate_policy": AsyncMock(return_value={}),
        base + "db_supabase.insert_one": AsyncMock(return_value=None),
        base + "db_supabase.update_ride": AsyncMock(return_value=None),
        base + "corporate_allowance_service.apply_rollback": AsyncMock(return_value={"transaction_id": "t1"}),
        base + "corporate_wallet_service.apply_adjustment": AsyncMock(return_value={"transaction_id": "t2"}),
    }


async def _call_settle(ride, patch_dict):
    from decimal import Decimal

    from backend.services import payment_service as ps

    patchers, mocks = _apply_all_patches(patch_dict)
    try:
        result = await ps.settle_corporate(ride, _RIDE_ID, Decimal("25.00"), Decimal("0"))
    finally:
        for p in patchers:
            p.stop()
    return result, mocks


@pytest.mark.anyio
async def test_settle_uses_stamped_member_not_rider_membership():
    """A ride stamped with corporate_member_id settles against that member —
    the rider (a guest customer) has NO membership and the rider-derived
    lookup must not even run. Partial allowance forces a master split so the
    ledger actor is asserted too."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 90}
    deps = _settle_patches(member_lookup=_booker_member(), allowance=allowance)
    result, mocks = await _call_settle(_fake_guest_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "db_supabase.get_corporate_member_by_id"].assert_called_once_with(_BOOKER_MEMBER_ID)
    mocks[base + "db_supabase.list_active_memberships_for_user"].assert_not_called()

    rollback_kwargs = mocks[base + "corporate_allowance_service.apply_rollback"].call_args.kwargs
    assert rollback_kwargs["member_id"] == _BOOKER_MEMBER_ID

    adj_kwargs = mocks[base + "corporate_wallet_service.apply_adjustment"].call_args.kwargs
    assert adj_kwargs["actor_user_id"] == _BOOKER_USER_ID, "ledger actor must be the payer, not the guest"

    ps_rows = [
        c.args[1] for c in mocks[base + "db_supabase.insert_one"].call_args_list if c.args[0] == "ride_payment_sources"
    ]
    assert ps_rows and ps_rows[0]["member_id"] == _BOOKER_MEMBER_ID


@pytest.mark.anyio
async def test_settle_stamped_member_wrong_company_leaves_pending():
    """A stamped member from another company is a contract violation: fail
    with 400, debit nothing, leave the ride pending for retry/ops."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 200, "used": 0}
    deps = _settle_patches(member_lookup=_booker_member(company_id="some_other_company"), allowance=allowance)
    result, mocks = await _call_settle(_fake_guest_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is False
    assert result.status_code == 400
    mocks[base + "corporate_allowance_service.apply_rollback"].assert_not_called()
    mocks[base + "corporate_wallet_service.apply_adjustment"].assert_not_called()
    pending_writes = [
        c
        for c in mocks[base + "db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "pending"
    ]
    assert pending_writes, "ride must be left payment_status=pending"


@pytest.mark.anyio
async def test_settle_stamped_member_inactive_leaves_pending():
    """A suspended/removed stamped member must not be billed."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 200, "used": 0}
    deps = _settle_patches(member_lookup=_booker_member(status="suspended"), allowance=allowance)
    result, mocks = await _call_settle(_fake_guest_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is False
    assert result.status_code == 400
    mocks[base + "corporate_allowance_service.apply_rollback"].assert_not_called()


@pytest.mark.anyio
async def test_settle_without_stamp_falls_back_to_rider_membership():
    """Legacy rides (no corporate_member_id) keep the rider-derived path."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 200, "used": 0}
    rider_membership = {
        "id": _MEMBER_ID,
        "company_id": _CORP_COMPANY_ID,
        "user_id": "rider_1",
        "status": "active",
    }
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[rider_membership])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "db_supabase.get_corporate_member_by_id"].assert_not_called()
    mocks[base + "db_supabase.list_active_memberships_for_user"].assert_called_once_with("rider_1")
    rollback_kwargs = mocks[base + "corporate_allowance_service.apply_rollback"].call_args.kwargs
    assert rollback_kwargs["member_id"] == _MEMBER_ID
