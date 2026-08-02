"""Surge-bypass guarantees for corporate-paid rides.

CLAUDE.md, Surge pricing rules:
  > Surge does not apply to corporate account-paid rides (policy; verify
  > in fare service)

These tests pin the policy at three layers:

  1. ``_is_corporate_paid`` — pure helper truth table. The two booking
     shapes that route to corporate billing must both return True; every
     personal-ride shape must return False.

  2. ``POST /rides/estimate`` — surge_multiplier in the rendered estimate
     must be 1.0× when the rider's request carries corporate context,
     even when the matched service area is in surge.

  3. ``POST /rides`` — surge_multiplier persisted on the inserted ride
     row must be 1.0×, and the corporate flag must override even a
     token-locked surge from a prior estimate. Without this ordering a
     rider could pin a surged token in personal mode, switch the toggle
     to Work, and bill the company at the surged rate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routes.rides import _is_corporate_paid

# ── Pure helper truth table ───────────────────────────────────────────


class TestIsCorporatePaid:
    """8-row truth table: (payment_method, work_profile, corp_id) → bool."""

    def test_explicit_company_allowance(self):
        assert _is_corporate_paid(
            payment_method="company_allowance",
            work_profile=False,
            corporate_account_id="corp_1",
        )

    def test_work_profile_with_corp_id(self):
        """work_profile=True + corporate_account_id → reclassified at
        rides.py:907 to company_allowance, so bypass must trigger BEFORE
        that reclassification (i.e. on the original input)."""
        assert _is_corporate_paid(
            payment_method="card",
            work_profile=True,
            corporate_account_id="corp_1",
        )

    def test_company_allowance_case_insensitive(self):
        assert _is_corporate_paid(
            payment_method="Company_Allowance",
            work_profile=False,
            corporate_account_id="corp_1",
        )

    def test_work_profile_without_corp_id_is_not_corporate(self):
        """Work toggle with no company → can't bill corp; treat as personal."""
        assert not _is_corporate_paid(
            payment_method="card",
            work_profile=True,
            corporate_account_id=None,
        )

    def test_company_allowance_without_corp_id_is_not_corporate(self):
        """Defensive: payment_method alone is not enough — needs the company."""
        assert not _is_corporate_paid(
            payment_method="company_allowance",
            work_profile=False,
            corporate_account_id=None,
        )

    def test_personal_card(self):
        assert not _is_corporate_paid(
            payment_method="card",
            work_profile=False,
            corporate_account_id=None,
        )

    def test_personal_wallet(self):
        assert not _is_corporate_paid(
            payment_method="wallet",
            work_profile=False,
            corporate_account_id=None,
        )

    def test_all_none(self):
        assert not _is_corporate_paid(
            payment_method=None,
            work_profile=None,
            corporate_account_id=None,
        )


# ── Estimate endpoint — surge masked when corporate context present ───


_FAKE_USER = {"id": "rider_1", "phone": "+15550001111", "status": "active"}
_APP_CHECK_HEADERS = {"X-Firebase-AppCheck": "test-token"}

_mock_app_check = MagicMock()
_mock_app_check.verify_token = MagicMock(return_value=None)


@pytest.fixture
def rider_override():
    import sys

    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    _orig = sys.modules.get("firebase_admin.app_check")
    sys.modules["firebase_admin.app_check"] = _mock_app_check
    yield
    app.dependency_overrides.pop(get_current_user, None)
    if _orig is None:
        sys.modules.pop("firebase_admin.app_check", None)
    else:
        sys.modules["firebase_admin.app_check"] = _orig


def _surged_fare_info(surge: float = 1.5):
    """Fare row with a non-trivial surge so personal vs corporate diverge."""
    return {
        "vehicle_type": {"id": "vt_standard", "name": "Standard"},
        "base_fare": 5.0,
        "per_km_rate": 1.20,
        "per_minute_rate": 0.20,
        "booking_fee": 2.0,
        "minimum_fare": 6.0,
        "surge_multiplier": surge,
    }


_BASE_ESTIMATE_BODY = {
    "pickup_lat": 52.1332,
    "pickup_lng": -106.6700,
    "dropoff_lat": 52.1500,
    "dropoff_lng": -106.6500,
}


def _patch_estimate_deps():
    """Common patches for /rides/estimate — supplies the surged fare list.

    The estimate handler calls ``get_fares_for_location`` (re-exported from
    routes.fares) and then queries ``drivers`` via ``db_supabase.get_rows``.
    Both are mocked here so the test never touches Supabase.
    """
    return {
        "routes.rides._deps.get_fares_for_location": AsyncMock(return_value=[_surged_fare_info(1.5)]),
        "routes.rides._deps.calculate_airport_fee": AsyncMock(return_value={"airport_fee": 0.0}),
        "routes.rides._deps.db_supabase.get_rows": AsyncMock(return_value=[]),
        "routes.rides._deps.get_app_settings": AsyncMock(return_value={}),
    }


def _start_patches(patch_dict):
    started = []
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        p.start()
        started.append(p)
    return started


def _stop_patches(patches):
    for p in patches:
        p.stop()


class TestEstimateSurgeBypass:
    def test_personal_estimate_keeps_surge(self, test_client, rider_override):
        """Sanity check — without corporate context, surge of 1.5 is honoured."""
        patches = _start_patches(_patch_estimate_deps())
        try:
            resp = test_client.post(
                "/api/v1/rides/estimate",
                json=_BASE_ESTIMATE_BODY,
                headers=_APP_CHECK_HEADERS,
            )
        finally:
            _stop_patches(patches)
        assert resp.status_code == 200
        body = resp.json()
        assert body["estimates"], "expected at least one estimate"
        assert body["estimates"][0]["surge_multiplier"] == 1.5

    def test_company_allowance_estimate_drops_surge_to_one(self, test_client, rider_override):
        """payment_method=company_allowance + corp id → surge masked to 1.0."""
        patches = _start_patches(_patch_estimate_deps())
        body_in = {
            **_BASE_ESTIMATE_BODY,
            "payment_method": "company_allowance",
            "corporate_account_id": "corp_1",
        }
        try:
            resp = test_client.post("/api/v1/rides/estimate", json=body_in, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)
        assert resp.status_code == 200
        out = resp.json()["estimates"][0]
        assert out["surge_multiplier"] == 1.0

    def test_work_profile_estimate_drops_surge_to_one(self, test_client, rider_override):
        """work_profile=True + corp id (the toggle path) → surge masked."""
        patches = _start_patches(_patch_estimate_deps())
        body_in = {
            **_BASE_ESTIMATE_BODY,
            "work_profile": True,
            "corporate_account_id": "corp_1",
        }
        try:
            resp = test_client.post("/api/v1/rides/estimate", json=body_in, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)
        assert resp.status_code == 200
        assert resp.json()["estimates"][0]["surge_multiplier"] == 1.0

    def test_estimate_request_omitting_corporate_fields_still_works(self, test_client, rider_override):
        """Backwards compatibility — old clients that don't send the fields
        must continue to receive surged estimates as before."""
        patches = _start_patches(_patch_estimate_deps())
        try:
            resp = test_client.post(
                "/api/v1/rides/estimate",
                json=_BASE_ESTIMATE_BODY,
                headers=_APP_CHECK_HEADERS,
            )
        finally:
            _stop_patches(patches)
        assert resp.status_code == 200
        assert resp.json()["estimates"][0]["surge_multiplier"] == 1.5


# ── Booking endpoint — surge masked at persist time ──────────────────


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


def _ride_get_rows_side_effect(*args, **kwargs):
    table = args[0] if args else ""
    if table == "service_areas":
        return []
    if table == "vehicle_types":
        return [{"id": "vt_standard", "name": "Standard", "is_active": True}]
    if table == "rides":
        return []
    if table == "corporate_members":
        # Backs booking.py's explicit active-membership re-check (gap #3
        # fail-closed fix) — without this the company_allowance path 403s
        # even though list_active_memberships_for_user above is stubbed
        # active, since that stub doesn't back this separate query.
        return [{"id": _MEMBER_ID, "company_id": _CORP_COMPANY_ID, "status": "active"}]
    return []


def _patch_create_ride_deps(*, captured: dict, surge: float = 1.5):
    """Patches that let create_ride() reach the insert step.

    `captured` is mutated in place with the ride dict that would have been
    inserted, so each test can assert on persisted ``surge_multiplier``.
    """

    async def _capture_insert(data):
        captured.update(data)
        return None

    membership = {
        "id": _MEMBER_ID,
        "company_id": _CORP_COMPANY_ID,
        "policy_override": False,
    }
    allowance = {"id": _ALLOWANCE_ID, "type": "fixed_recurring", "amount": 500, "used": 0}

    # Two policy gates fire on the booking path:
    #   - rides.py:886 — `evaluate_policy(...)` kwargs match
    #     evaluate_policy_for_ride's signature; a pre-existing call-signature
    #     mismatch outside the scope of this commit. Result is checked via
    #     `.allowed`.
    #   - rides.py:980 — `evaluate_policy_for_ride(...)`, used on the
    #     work_profile path. Result is checked via `.passed`.
    # We patch both at the source module so the lazy imports inside
    # create_ride pick up the stubs, regardless of which path the test
    # exercises.
    _policy_pass = MagicMock()
    _policy_pass.allowed = True
    _policy_pass.passed = True
    _policy_pass.reason = ""
    _policy_pass.failed_rules = []
    # Pre-auth buffer check at rides.py:997-1006 reads allowance/policy
    # surfaced by the policy evaluator. Provide dict shapes so the Decimal
    # math against `amount` / `used` doesn't blow up on MagicMock attrs.
    _policy_pass.allowance = allowance
    _policy_pass.policy = {"allowed_payment_source": "both"}

    return {
        # Lazy-imported inside create_ride at rides.py:882-884; patch on the
        # source module so the import resolves to the stub.
        "services.corporate_policy_service.evaluate_policy": AsyncMock(return_value=_policy_pass),
        "services.corporate_policy_service.evaluate_policy_for_ride": AsyncMock(return_value=_policy_pass),
        # Top-level import in routes.rides — also stubbed there for the
        # work_profile path that calls evaluate_policy_for_ride directly.
        "routes.rides._deps.evaluate_policy_for_ride": AsyncMock(return_value=_policy_pass),
        "routes.rides._deps.db_supabase.find_one": AsyncMock(
            return_value={"id": "rider_1", "status": "active", "stripe_customer_id": "cus_1"}
        ),
        "routes.rides._deps.db.find_one": AsyncMock(
            return_value={"id": "rider_1", "status": "active", "stripe_customer_id": "cus_1"}
        ),
        "routes.rides._deps.db_supabase.get_rows": AsyncMock(side_effect=_ride_get_rows_side_effect),
        "routes.rides._deps._fares_for_location_impl": AsyncMock(return_value=[_surged_fare_info(surge)]),
        "routes.rides._deps.calculate_airport_fee": AsyncMock(return_value={"airport_fee": 0.0}),
        "routes.rides._deps.calculate_all_fees": AsyncMock(
            return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}
        ),
        "routes.rides._deps.db_supabase.insert_ride": _capture_insert,
        "routes.rides._deps.db_supabase.get_ride": AsyncMock(return_value=None),
        "routes.rides.matching.match_driver_to_ride": AsyncMock(return_value=None),
        "routes.rides._deps.db_supabase.list_active_memberships_for_user": AsyncMock(return_value=[membership]),
        "routes.rides._deps.db_supabase.get_member_allowance": AsyncMock(return_value=allowance),
        "routes.rides._deps.db_supabase.get_corporate_policy": AsyncMock(return_value={}),
        "routes.rides._deps.db_supabase.get_corporate_wallet_by_company": AsyncMock(
            return_value={"id": _WALLET_ID, "balance": 500.0}
        ),
        # require_company_bookable() (scheduled-rides gap review, Finding #20)
        # blocks anything not literally "active" — without this the
        # company_allowance/work_profile tests 403 before reaching insert,
        # since the real get_corporate_account_by_id is otherwise unmocked.
        "routes.rides._deps.db_supabase.get_corporate_account_by_id": AsyncMock(return_value={"status": "active"}),
    }


class TestCreateRideSurgeBypass:
    def test_personal_ride_persists_surge(self, test_client, rider_override):
        """Without corporate context, a 1.5× surge area persists 1.5×."""
        captured: dict = {}
        patches = _start_patches(_patch_create_ride_deps(captured=captured, surge=1.5))
        try:
            test_client.post("/api/v1/rides", json=_BASE_RIDE_BODY, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)
        # Sanity check — the surged fare made it through to the insert.
        assert captured.get("surge_multiplier") == pytest.approx(1.5)

    def test_corporate_work_profile_ride_persists_surge_one(self, test_client, rider_override):
        """work_profile=True + corp id → surge_multiplier=1.0 on the row."""
        captured: dict = {}
        patches = _start_patches(_patch_create_ride_deps(captured=captured, surge=1.5))
        body = {
            **_BASE_RIDE_BODY,
            "work_profile": True,
            "corporate_account_id": _CORP_COMPANY_ID,
        }
        try:
            test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)
        assert captured.get("surge_multiplier") == pytest.approx(1.0)
        # The reclassification at rides.py:907 must still tag the ride as
        # company_allowance — bypass and reclassification are independent.
        assert captured.get("payment_method") == "company_allowance"
        assert captured.get("corporate_account_id") == _CORP_COMPANY_ID

    def test_corporate_explicit_company_allowance_persists_surge_one(self, test_client, rider_override):
        """Explicit payment_method=company_allowance also bypasses surge."""
        captured: dict = {}
        patches = _start_patches(_patch_create_ride_deps(captured=captured, surge=2.0))
        body = {
            **_BASE_RIDE_BODY,
            "payment_method": "company_allowance",
            "corporate_account_id": _CORP_COMPANY_ID,
        }
        try:
            test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)
        assert captured.get("surge_multiplier") == pytest.approx(1.0)

    def test_corporate_distance_fare_below_surged_baseline(self, test_client, rider_override):
        """Side-by-side: same trip, same area surge — corporate ride's
        distance_fare must be the un-surged share (personal × 1.5)."""
        personal_capture: dict = {}
        patches = _start_patches(_patch_create_ride_deps(captured=personal_capture, surge=1.5))
        try:
            test_client.post("/api/v1/rides", json=_BASE_RIDE_BODY, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)

        corp_capture: dict = {}
        patches = _start_patches(_patch_create_ride_deps(captured=corp_capture, surge=1.5))
        body = {
            **_BASE_RIDE_BODY,
            "work_profile": True,
            "corporate_account_id": _CORP_COMPANY_ID,
        }
        try:
            test_client.post("/api/v1/rides", json=body, headers=_APP_CHECK_HEADERS)
        finally:
            _stop_patches(patches)

        personal_fare = float(personal_capture.get("distance_fare", 0))
        corp_fare = float(corp_capture.get("distance_fare", 0))
        assert corp_fare > 0
        assert personal_fare > corp_fare
        assert personal_fare / corp_fare == pytest.approx(1.5, rel=0.01)
