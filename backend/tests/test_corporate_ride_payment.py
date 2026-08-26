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


def _mock_create_ride_deps(*, memberships=None, allowance=None, policy=None, company_status="active"):
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
        # require_company_bookable() (corporate + admin portal review, Critical
        # #1 — the work_profile path now shares this guard instead of its own
        # inline status check) does its own get_app_settings() fetch and a
        # get_corporate_account_by_id() lookup on the real backend.db_supabase
        # singleton — both must be mocked explicitly or the guard 403s before
        # any of the tests below reach the branch they're actually testing.
        "routes.rides._deps.get_app_settings": AsyncMock(return_value={}),
        "backend.db_supabase.get_corporate_account_by_id": AsyncMock(return_value={"status": company_status}),
    }


def _apply_all_patches(patch_dict):
    """Start every patch in patch_dict and return (patchers, mocks).

    Two entries here can alias the SAME underlying attribute (e.g.
    "routes.rides._deps.db_supabase.find_one" and "routes.rides._deps.db.find_one"
    -- _deps.py's `db = db_supabase` legacy alias means both patch targets sit on
    the identical module object). unittest.mock.patch saves whatever value is
    live *at start() time* as the value to restore on stop(), so two overlapping
    patches on the same attribute must be stopped in the reverse of the order
    they were started (LIFO) -- otherwise the later patch's stop() restores the
    EARLIER patch's mock instead of the real original, permanently leaving the
    attribute monkeypatched after the test's own try/finally believes it cleaned
    up. Caller loops (`for p in patchers: p.stop()`) don't know about the
    aliasing, so the list is reversed here once, up front, rather than fixing
    every call site.
    """
    patchers = []
    mocks = {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return list(reversed(patchers)), mocks


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


def test_work_profile_policy_check_uses_grand_total_not_bare_fare(test_client, rider_override):
    """Corporate + admin portal review, gap #39: the booking-time
    max_fare_per_ride check must compare against grand_total (fare +
    area fees + tax), not the bare total_fare that excludes them —
    otherwise a company's per-ride cap can be bypassed whenever area
    fees/tax push the actual charge over the cap while total_fare alone
    stays under it. fees_total is set far above any plausible total_fare
    for this short in-city fixture route so the assertion doesn't depend
    on the exact fare-calc output, matching this file's existing
    max_fare_per_ride=0.01 pattern in test_work_profile_policy_violation_
    returns_400."""
    membership = {"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "policy_override": False}
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 500, "used": 0}
    # Comfortably above any plausible total_fare for this ~2km fixture route,
    # comfortably below total_fare + fees_total once area fees are included.
    policy = {"active": True, "max_fare_per_ride": 100}
    deps = _mock_create_ride_deps(memberships=[membership], allowance=allowance, policy=policy)
    deps["routes.rides._deps.calculate_all_fees"] = AsyncMock(
        return_value={"fees_total": 10000, "tax_amount": 0, "fees": [], "tax_breakdown": {}}
    )
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        resp = test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400, resp.text
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


def test_work_profile_pending_verification_company_returns_400(test_client, rider_override):
    """Corporate + admin portal review, Critical #1: a company still in
    pending_verification (never approved through KYB, so it has no wallet
    row yet) must be blocked from work_profile booking the same way
    suspended/closed companies already were — previously this path's own
    inline check only matched literal "suspended"/"closed", silently letting
    a never-verified company's owner book a ride that would later settle
    with no money moved at all (see settle_corporate's new missing-wallet
    guard, tested separately in test_settle_no_wallet_leaves_pending_below)."""
    membership = {"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "policy_override": False}
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 500, "used": 0}
    deps = _mock_create_ride_deps(
        memberships=[membership], allowance=allowance, policy={}, company_status="pending_verification"
    )
    patchers, _ = _apply_all_patches(deps)
    body = {**_BASE_RIDE_BODY, "work_profile": True, "corporate_account_id": _CORP_COMPANY_ID}
    try:
        resp = test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "company_inactive"


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
        "backend.services.payment_service.corporate_allowance_service.apply_ride_debit": AsyncMock(
            return_value={"transaction_id": "t1"}
        ),
        "backend.services.payment_service.corporate_wallet_service.apply_adjustment": AsyncMock(
            return_value={"transaction_id": "t2"}
        ),
        "backend.services.payment_service.send_push_notification": AsyncMock(),
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
            "backend.services.payment_service.corporate_allowance_service.apply_ride_debit",
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

    mocks["backend.services.payment_service.corporate_allowance_service.apply_ride_debit"].assert_called_once()
    call_kwargs = mocks[
        "backend.services.payment_service.corporate_allowance_service.apply_ride_debit"
    ].call_args.kwargs
    assert call_kwargs["amount"] == pytest.approx(25.0)
    assert call_kwargs["wallet_id"] == _WALLET_ID
    assert call_kwargs["allowance_id"] == _ALLOWANCE_ID
    assert call_kwargs["member_id"] == _MEMBER_ID

    mocks["backend.services.payment_service.corporate_wallet_service.apply_adjustment"].assert_not_called()

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

    rollback_kwargs = mocks[
        "backend.services.payment_service.corporate_allowance_service.apply_ride_debit"
    ].call_args.kwargs
    assert rollback_kwargs["amount"] == pytest.approx(10.0)

    adj_kwargs = mocks["backend.services.payment_service.corporate_wallet_service.apply_adjustment"].call_args.kwargs
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

    mocks["backend.services.payment_service.corporate_wallet_service.apply_adjustment"].assert_called_once()

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

    mocks["backend.services.payment_service.corporate_allowance_service.apply_ride_debit"].assert_called_once()
    mocks["backend.services.payment_service.corporate_wallet_service.apply_adjustment"].assert_not_called()
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
        base + "corporate_allowance_service.apply_ride_debit": AsyncMock(return_value={"transaction_id": "t1"}),
        base + "corporate_wallet_service.apply_adjustment": AsyncMock(return_value={"transaction_id": "t2"}),
        base + "send_push_notification": AsyncMock(),
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

    rollback_kwargs = mocks[base + "corporate_allowance_service.apply_ride_debit"].call_args.kwargs
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
    mocks[base + "corporate_allowance_service.apply_ride_debit"].assert_not_called()
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
    mocks[base + "corporate_allowance_service.apply_ride_debit"].assert_not_called()


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
    rollback_kwargs = mocks[base + "corporate_allowance_service.apply_ride_debit"].call_args.kwargs
    assert rollback_kwargs["member_id"] == _MEMBER_ID


@pytest.mark.anyio
async def test_settle_no_wallet_leaves_pending_and_moves_no_money():
    """Corporate + admin portal review, Critical #1: a company with no wallet
    row (e.g. self-serve-signed-up, never completed KYB) must fail loudly at
    settlement, not silently succeed. Previously both the allowance-debit and
    master-fallback branches were gated on corp_wallet.get("id"), which is
    falsy with no wallet — neither executed, no exception was raised, and the
    ride fell through to payment_status="paid" with zero money moved."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 200, "used": 0}
    rider_membership = {
        "id": _MEMBER_ID,
        "company_id": _CORP_COMPANY_ID,
        "user_id": "rider_1",
        "status": "active",
    }
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[rider_membership])
    base = "backend.services.payment_service."
    deps[base + "db_supabase.get_corporate_wallet_by_company"] = AsyncMock(return_value=None)
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)

    assert result.success is False
    assert result.status_code == 503
    mocks[base + "corporate_allowance_service.apply_ride_debit"].assert_not_called()
    mocks[base + "corporate_wallet_service.apply_adjustment"].assert_not_called()
    mocks[base + "db_supabase.insert_one"].assert_not_called()
    pending_writes = [
        c
        for c in mocks[base + "db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "pending"
    ]
    assert pending_writes, "ride must be left payment_status=pending, never paid"


# ─────────────────────────────────────────────────────────────────────────────
#  D. R44 (ACTION_ITEMS.md N15) — allowance threshold-crossing notifications
# ─────────────────────────────────────────────────────────────────────────────


def _rider_membership():
    return {
        "id": _MEMBER_ID,
        "company_id": _CORP_COMPANY_ID,
        "user_id": "rider_1",
        "status": "active",
    }


@pytest.mark.anyio
async def test_settle_notifies_exhausted_on_crossing_to_zero():
    """$10 remaining, $25 fare -> allowance_debit=10, remaining_after=0.
    remaining_before (10) > 0 and remaining_after <= 0 -> exhausted push."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 90}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "send_push_notification"].assert_awaited_once()
    args, kwargs = mocks[base + "send_push_notification"].await_args
    assert args[0] == "rider_1"
    assert kwargs["data"] == {"type": "corporate_allowance_exhausted"}
    assert kwargs["priority"] == "normal"
    assert kwargs["target_app"] == "rider"


@pytest.mark.anyio
async def test_settle_notifies_low_on_crossing_below_threshold():
    """$30 remaining of $100 (30%) before, $25 fare fully covered ->
    remaining_after=$5 (5%). Crosses the 20% line without hitting zero ->
    'running low' push, not 'exhausted'."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 70}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "send_push_notification"].assert_awaited_once()
    args, kwargs = mocks[base + "send_push_notification"].await_args
    assert kwargs["data"] == {"type": "corporate_allowance_low"}


@pytest.mark.anyio
async def test_settle_no_notification_when_comfortably_above_threshold():
    """$1000 allowance, $25 fare -> remaining stays at 97.5%. No crossing,
    no push."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 1000, "used": 0}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "send_push_notification"].assert_not_awaited()


@pytest.mark.anyio
async def test_settle_no_notification_when_already_exhausted():
    """Allowance already at 0 before this ride -> allowance_debit is 0, the
    allowance-debit branch (and thus apply_ride_debit) never runs, so there
    is no NEW crossing to notify about — the rider was already told on the
    ride that exhausted it."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 100}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "corporate_allowance_service.apply_ride_debit"].assert_not_called()
    mocks[base + "send_push_notification"].assert_not_awaited()


@pytest.mark.anyio
async def test_settle_no_notification_for_unlimited_allowance():
    """Unlimited allowances have no ceiling to warn about."""
    allowance = {"id": _ALLOWANCE_ID, "type": "unlimited", "amount": None, "used": 0}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)
    base = "backend.services.payment_service."

    assert result.success is True
    mocks[base + "send_push_notification"].assert_not_awaited()


@pytest.mark.anyio
async def test_settle_succeeds_even_if_notification_push_raises():
    """A push failure must never turn an already-successful settlement into
    an error response."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 90}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    base = "backend.services.payment_service."
    deps[base + "send_push_notification"] = AsyncMock(side_effect=RuntimeError("push down"))
    result, _mocks = await _call_settle(_fake_corporate_ride(), deps)

    assert result.success is True


# ─────────────────────────────────────────────────────────────────────────
# E5 kill switch: corporate_billing_enabled
# ─────────────────────────────────────────────────────────────────────────
#
# settle_corporate does a LAZY dual import of get_app_settings (module-level
# except-branch import lists are stripped by a formatter hook in this file —
# see the identical pattern _atomic_settle_enabled already uses above), so
# these tests patch the function at its source (settings_loader) rather than
# as a payment_service module attribute, which the lazy import re-resolves
# on every call.


@pytest.mark.anyio
async def test_settle_flag_off_returns_503_before_any_membership_lookup():
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 0}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    deps["backend.settings_loader.get_app_settings"] = AsyncMock(return_value={"corporate_billing_enabled": False})
    result, mocks = await _call_settle(_fake_corporate_ride(), deps)

    assert result.success is False
    assert result.status_code == 503
    mocks["backend.services.payment_service.db_supabase.list_active_memberships_for_user"].assert_not_awaited()


@pytest.mark.anyio
async def test_settle_flag_missing_key_defaults_to_enabled():
    """A settings dict with no corporate_billing_enabled key (legacy row)
    must still proceed -- the flag defaults to enabled."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 0}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    deps["backend.settings_loader.get_app_settings"] = AsyncMock(return_value={})
    result, _mocks = await _call_settle(_fake_corporate_ride(), deps)

    assert result.success is True


@pytest.mark.anyio
async def test_settle_fails_open_on_settings_lookup_error():
    """A settings-read error must never itself block corporate settlement."""
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 100, "used": 0}
    deps = _settle_patches(member_lookup=None, allowance=allowance, memberships=[_rider_membership()])
    deps["backend.settings_loader.get_app_settings"] = AsyncMock(side_effect=RuntimeError("settings down"))
    result, _mocks = await _call_settle(_fake_corporate_ride(), deps)

    assert result.success is True
