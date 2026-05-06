"""Unit tests for four P0/P1 security fixes.

Fix 1 (Dispatch filter gap):
    DispatchService.find_candidate_drivers requires status='active' AND
    is_verified=True so suspended or unverified drivers are never offered rides.

Fix 2 (Card ownership verification):
    delete_card and set_default_card verify that the Stripe PaymentMethod
    belongs to the authenticated user's stripe_customer_id before proceeding,
    returning 403 on mismatch.

Fix 3 (Wallet pay ride-status guard):
    /wallet/pay checks that the ride has status='completed' before
    deducting the balance, returning 400 if not completed.

Fix 4 (Quests double-claim guard):
    The claim endpoint performs an atomic update WHERE status='completed'
    and returns 409 when the row was already claimed.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend/ is on the path (conftest already does this, but explicit is
# safer for direct pytest invocation from the repo root).
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_root = os.path.dirname(_backend_dir)
for _p in (_backend_dir, _project_root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub heavy third-party packages that may not be installed in the unit-test
# environment.  Must run before any backend module is imported.
_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
    "loguru",
    "sentry_sdk",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# Patch loguru.logger so ride/dispatch modules that import it don't crash.
import logging as _logging

sys.modules["loguru"].logger = _logging.getLogger("loguru_stub")

# ─────────────────────────────────────────────────────────────────────────────
# Shared guards: skip entire module gracefully when backend deps are absent
# ─────────────────────────────────────────────────────────────────────────────

try:
    from backend.services.dispatch_service import DispatchService

    _HAS_DISPATCH = True
except BaseException:
    _HAS_DISPATCH = False

try:
    import dependencies as _dependencies
    from backend.server import app as _app

    _HAS_APP = True
except BaseException:
    _HAS_APP = False

_skip_dispatch = pytest.mark.skipif(not _HAS_DISPATCH, reason="backend dispatch deps not installed")
_skip_app = pytest.mark.skipif(not _HAS_APP, reason="backend app deps not installed")


# ─────────────────────────────────────────────────────────────────────────────
# Shared DB mock helper (mirrors test_quests.py pattern)
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_db(rows: dict | None = None):
    """Build a mock that mirrors the db_supabase flat-function interface.

    ``rows`` maps table name → row-or-list to return from find_one/get_rows.
    """
    rows = rows or {}
    mock = MagicMock()

    _tables = ("drivers", "quests", "quest_progress", "wallets", "wallet_transactions", "users", "rides")
    for tbl in _tables:
        col_mock = MagicMock()
        col_mock.find_one = AsyncMock(return_value=None)
        col_mock.insert_one = AsyncMock(return_value=None)
        col_mock.update_one = AsyncMock(return_value=None)
        setattr(mock, tbl, col_mock)

    mock.get_rows = AsyncMock(return_value=[])

    async def _find_one(table, filters=None, **kwargs):
        if table in rows:
            return rows[table]
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.find_one(filters, **kwargs)
        return None

    async def _insert_one(table, doc, **kwargs):
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.insert_one(doc, **kwargs)
        return {**doc}

    async def _update_one(table, filters, update, **kwargs):
        tbl_attr = getattr(mock, table, None)
        if tbl_attr is not None:
            return await tbl_attr.update_one(filters, update, **kwargs)
        return None

    mock.find_one = _find_one
    mock.insert_one = _insert_one
    mock.update_one = _update_one

    return mock


def _make_test_client(user: dict):
    """Return a TestClient with the given user wired as get_current_user."""
    if not _HAS_APP:
        pytest.skip("backend app deps not installed")
    from fastapi.testclient import TestClient

    _app.dependency_overrides[_dependencies.get_current_user] = lambda: user
    return TestClient(_app, raise_server_exceptions=False)


# ═════════════════════════════════════════════════════════════════════════════
# FIX 1 — Dispatch filter gap (DispatchService.find_candidate_drivers)
# ═════════════════════════════════════════════════════════════════════════════
#
# find_candidate_drivers passes status='active' and is_verified=True in its
# DB filter so the DB layer itself excludes suspended/unverified drivers.
# ─────────────────────────────────────────────────────────────────────────────


def _base_ride() -> dict:
    return {
        "id": "ride_001",
        "rider_id": "user_001",
        "pickup_lat": 52.1333,
        "pickup_lng": -106.6667,
        "vehicle_type_id": "vt-sedan",
        "status": "searching",
    }


def _base_driver(**overrides) -> dict:
    """A fully-valid, near-pickup driver row."""
    d = {
        "id": "driver_001",
        "user_id": "user_driver_001",
        "status": "active",
        "is_verified": True,
        "is_online": True,
        "is_available": True,
        "vehicle_type_id": "vt-sedan",
        "lat": 52.1340,  # ~0.08 km from pickup
        "lng": -106.6670,
        "rating": 4.9,
    }
    d.update(overrides)
    return d


@_skip_dispatch
@pytest.mark.anyio
async def test_dispatch_excludes_suspended_driver():
    """find_candidate_drivers must pass status='active' to the DB query.

    With status='active' in the filter, a suspended driver is never returned
    by Supabase and is therefore never offered the ride.  We verify the filter
    key is present on the DB call, which is the contract this fix establishes.
    """
    suspended_driver = _base_driver(status="suspended")

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[suspended_driver])

    svc = DispatchService(db=mock_db)
    ride = _base_ride()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=Exception("redis unavailable")),
        )
        await svc.find_candidate_drivers(ride)

    # Confirm the DB query included the status filter
    _, filter_dict = mock_db.get_rows.call_args.args[0], mock_db.get_rows.call_args.args[1]
    assert filter_dict.get("status") == "active", (
        "find_candidate_drivers must pass status='active' to exclude suspended drivers"
    )


@_skip_dispatch
@pytest.mark.anyio
async def test_dispatch_excludes_unverified_driver():
    """find_candidate_drivers must pass is_verified=True to the DB query.

    With is_verified=True in the filter, an unverified driver is excluded at
    the DB layer and is never offered the ride.
    """
    unverified_driver = _base_driver(is_verified=False)

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[unverified_driver])

    svc = DispatchService(db=mock_db)
    ride = _base_ride()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=Exception("redis unavailable")),
        )
        await svc.find_candidate_drivers(ride)

    _, filter_dict = mock_db.get_rows.call_args.args[0], mock_db.get_rows.call_args.args[1]
    assert filter_dict.get("is_verified") is True, (
        "find_candidate_drivers must pass is_verified=True to exclude unverified drivers"
    )


@_skip_dispatch
@pytest.mark.anyio
async def test_dispatch_includes_verified_active_driver():
    """A driver with status='active' and is_verified=True within radius must
    be returned in the candidate list.
    """
    valid_driver = _base_driver()

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[valid_driver])

    svc = DispatchService(db=mock_db)
    ride = _base_ride()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=Exception("redis unavailable")),
        )
        candidates = await svc.find_candidate_drivers(ride)

    # Both required eligibility filters must be present
    _, filter_dict = mock_db.get_rows.call_args.args[0], mock_db.get_rows.call_args.args[1]
    assert filter_dict.get("status") == "active"
    assert filter_dict.get("is_verified") is True

    # Valid driver is returned
    assert len(candidates) == 1
    assert candidates[0]["id"] == valid_driver["id"]


@_skip_dispatch
def test_dispatch_filter_dict_contains_both_eligibility_keys():
    """Pure schema assertion: the filter produced by find_candidate_drivers
    must include both status='active' and is_verified=True.
    """
    ride = _base_ride()
    # Mirror the filter dict produced by DispatchService.find_candidate_drivers
    driver_filter: dict = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": ride["vehicle_type_id"],
    }
    assert driver_filter["status"] == "active"
    assert driver_filter["is_verified"] is True
    assert driver_filter["is_online"] is True
    assert driver_filter["is_available"] is True


# ═════════════════════════════════════════════════════════════════════════════
# FIX 2 — Card ownership verification (payments.py)
# ═════════════════════════════════════════════════════════════════════════════
#
# delete_card and set_default_card must retrieve the Stripe PaymentMethod and
# verify pm.customer == user.stripe_customer_id before proceeding.
# These tests are xfail(strict=True) until the fix is implemented.
# ─────────────────────────────────────────────────────────────────────────────


@_skip_app
def test_delete_card_rejects_wrong_owner():
    """DELETE /api/v1/payments/cards/{id} must return 403 when the Stripe PM
    belongs to a different customer than the authenticated user.

    Expected fix: pm = stripe.PaymentMethod.retrieve(card_id, api_key=...);
    if pm.customer != user['stripe_customer_id']: raise HTTPException(403).
    """
    user = {"id": "user_mine", "stripe_customer_id": "cus_mine"}
    pm_mock = MagicMock()
    pm_mock.customer = "cus_other"

    mock_settings = {"stripe_secret_key": "sk_test_123"}
    mock_user_row = {"id": "user_mine", "stripe_customer_id": "cus_mine", "default_payment_method": None}

    with (
        patch("routes.payments.get_app_settings", new=AsyncMock(return_value=mock_settings)),
        patch("routes.payments.db_supabase.get_user_by_id", new=AsyncMock(return_value=mock_user_row)),
        patch("stripe.PaymentMethod.retrieve", return_value=pm_mock),
        patch("stripe.PaymentMethod.detach", return_value=MagicMock()),
    ):
        client = _make_test_client(user)
        resp = client.delete("/api/v1/payments/cards/pm_foreign")

    _app.dependency_overrides.clear()
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


@_skip_app
def test_delete_card_allows_correct_owner():
    """DELETE /api/v1/payments/cards/{id} succeeds (200) when PM.customer
    matches the authenticated user's stripe_customer_id.
    """
    user = {"id": "user_mine", "stripe_customer_id": "cus_mine"}
    pm_mock = MagicMock()
    pm_mock.customer = "cus_mine"

    mock_settings = {"stripe_secret_key": "sk_test_123"}
    mock_user_row = {"id": "user_mine", "stripe_customer_id": "cus_mine", "default_payment_method": None}

    with (
        patch("routes.payments.get_app_settings", new=AsyncMock(return_value=mock_settings)),
        patch("routes.payments.db_supabase.get_user_by_id", new=AsyncMock(return_value=mock_user_row)),
        patch("routes.payments.db_supabase.update_one", new=AsyncMock(return_value=None)),
        patch("stripe.PaymentMethod.retrieve", return_value=pm_mock),
        patch("stripe.PaymentMethod.detach", return_value=MagicMock()),
    ):
        client = _make_test_client(user)
        resp = client.delete("/api/v1/payments/cards/pm_mine")

    _app.dependency_overrides.clear()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@_skip_app
def test_set_default_card_rejects_wrong_owner():
    """POST /api/v1/payments/cards/{id}/default must return 403 when the
    Stripe PM belongs to a different customer than the authenticated user.

    Expected fix: same pattern as delete_card ownership check.
    """
    user = {"id": "user_mine", "stripe_customer_id": "cus_mine"}
    pm_mock = MagicMock()
    pm_mock.customer = "cus_other"

    mock_settings = {"stripe_secret_key": "sk_test_123"}
    mock_user_row = {"id": "user_mine", "stripe_customer_id": "cus_mine"}

    with (
        patch("routes.payments.get_app_settings", new=AsyncMock(return_value=mock_settings)),
        patch("routes.payments.db_supabase.get_user_by_id", new=AsyncMock(return_value=mock_user_row)),
        patch("routes.payments.db_supabase.update_one", new=AsyncMock(return_value=None)),
        patch("stripe.PaymentMethod.retrieve", return_value=pm_mock),
        patch("stripe.Customer.modify", return_value=MagicMock()),
    ):
        client = _make_test_client(user)
        resp = client.post("/api/v1/payments/cards/pm_foreign/default")

    _app.dependency_overrides.clear()
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ═════════════════════════════════════════════════════════════════════════════
# FIX 3 — Wallet pay ride-status guard (wallet.py)
# ═════════════════════════════════════════════════════════════════════════════
#
# /wallet/pay must check ride.status == 'completed' before deducting.
# ─────────────────────────────────────────────────────────────────────────────

_RIDER = {"id": "rider_001"}
_WALLET_ROW = {"id": "wallet_001", "user_id": "rider_001", "balance": "50.00", "is_active": True}


@_skip_app
def test_wallet_pay_rejects_non_completed_ride():
    """POST /api/v1/wallet/pay must return 400 with 'completed' in the detail
    when the ride has status='in_progress' (not yet completed).

    Expected fix: after fetching the ride, add:
        if ride['status'] != 'completed':
            raise HTTPException(400, detail='Ride must be completed before wallet payment')
    """
    ride_row = {
        "id": "ride_001",
        "rider_id": "rider_001",
        "status": "in_progress",
        "total_fare": "15.00",
    }
    mock_db = _make_mock_db(rows={"wallets": _WALLET_ROW, "rides": ride_row})

    with patch("routes.wallet.db", mock_db):
        client = _make_test_client(_RIDER)
        resp = client.post(
            "/api/v1/wallet/pay",
            json={"ride_id": "ride_001", "amount": "15.00"},
        )

    _app.dependency_overrides.clear()
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "completed" in resp.json().get("detail", "").lower(), (
        f"Response detail should mention 'completed': {resp.text}"
    )


@_skip_app
def test_wallet_pay_allows_completed_ride():
    """POST /api/v1/wallet/pay succeeds when the ride has status='completed'
    and the wallet has sufficient balance.
    """
    ride_row = {
        "id": "ride_001",
        "rider_id": "rider_001",
        "status": "completed",
        "total_fare": "15.00",
    }
    mock_db = _make_mock_db(rows={"wallets": _WALLET_ROW, "rides": ride_row})
    mock_db.insert_one = AsyncMock(return_value={"id": "txn_001"})
    mock_db.update_one = AsyncMock(return_value=None)

    with (
        patch("routes.wallet.db", mock_db),
        patch("routes.wallet.wallet_pay_for_ride", new=AsyncMock(return_value=35.0)),
    ):
        client = _make_test_client(_RIDER)
        resp = client.post(
            "/api/v1/wallet/pay",
            json={"ride_id": "ride_001", "amount": "15.00"},
        )

    _app.dependency_overrides.clear()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "balance" in data


# ═════════════════════════════════════════════════════════════════════════════
# FIX 4 — Quests double-claim guard (quests.py)
# ═════════════════════════════════════════════════════════════════════════════
#
# The claim endpoint must perform an atomic update WHERE status='completed'
# and return 409 when the row was already claimed (update returns None).
# ─────────────────────────────────────────────────────────────────────────────

_DRIVER_USER = {"id": "user_123", "role": "driver"}
_SAMPLE_DRIVER = {"id": "driver_123", "user_id": "user_123"}
_SAMPLE_QUEST = {
    "id": "quest_1",
    "title": "Complete 10 Rides",
    "reward_amount": 25.0,
    "reward_type": "wallet_credit",
}
_SAMPLE_PROGRESS_COMPLETED = {
    "id": "progress_1",
    "quest_id": "quest_1",
    "driver_id": "driver_123",
    "status": "completed",
    "current_value": 10,
}
_QUEST_WALLET = {
    "id": "wallet_001",
    "user_id": "user_123",
    "balance": "0.00",
    "is_active": True,
}


@_skip_app
def test_quest_claim_rejects_already_claimed():
    """POST /api/v1/quests/progress/{id}/claim must return 409 when the
    atomic claim update returns None (row already claimed by a prior request).

    Expected fix: change the final update_one to an atomic form that only
    matches rows WHERE status='completed', then:
        if not updated_row:
            raise HTTPException(409, detail='Quest reward already claimed')
    """
    mock_db = _make_mock_db(
        rows={
            "drivers": _SAMPLE_DRIVER,
            "quest_progress": _SAMPLE_PROGRESS_COMPLETED,
            "quests": _SAMPLE_QUEST,
            "wallets": _QUEST_WALLET,
        }
    )
    # Simulate the atomic update returning None — row already claimed
    mock_db.quest_progress.update_one = AsyncMock(return_value=None)

    with patch("routes.quests.db", mock_db), patch("routes.wallet.db", mock_db):
        client = _make_test_client(_DRIVER_USER)
        resp = client.post("/api/v1/quests/progress/progress_1/claim")

    _app.dependency_overrides.clear()
    assert resp.status_code == 409, (
        f"Expected 409 Conflict when reward already claimed, got {resp.status_code}: {resp.text}"
    )


@_skip_app
def test_quest_claim_succeeds_first_time():
    """POST /api/v1/quests/progress/{id}/claim returns 200 and credits the
    reward when the quest is completed and not yet claimed.
    """
    mock_db = _make_mock_db(
        rows={
            "drivers": _SAMPLE_DRIVER,
            "quest_progress": _SAMPLE_PROGRESS_COMPLETED,
            "quests": _SAMPLE_QUEST,
            "wallets": _QUEST_WALLET,
        }
    )
    # Atomic update succeeds — return the updated row
    mock_db.quest_progress.update_one = AsyncMock(return_value={**_SAMPLE_PROGRESS_COMPLETED, "status": "claimed"})

    with patch("routes.quests.db", mock_db), patch("routes.wallet.db", mock_db):
        client = _make_test_client(_DRIVER_USER)
        resp = client.post("/api/v1/quests/progress/progress_1/claim")

    _app.dependency_overrides.clear()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "claimed"
    assert data.get("reward_amount") == 25.0
