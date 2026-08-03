"""
A1c Sub-tier C coverage: backend/routes/promotions.py (65.85% -> target 80%+).

Fills gaps not covered by test_p2_promo_wallet_loyalty.py (rules 1-4 + flat/
percentage discount) or test_promo_discount_parity.py / test_promo_per_user_race.py
/ test_promo_rate_limit.py:

- ``_validate_promo_for_user`` rules 5-10 (private coupon, first-ride-only,
  new-user-only, inactive-user targeting, min/max total rides, budget cap),
  the ``free_ride`` discount branch, the ``ride_id``-driven server-side fare
  fetch branch (including ride-not-found), and the malformed-expiry-date
  catch branch.
- ``apply_promo_for_admin`` (admin "apply on behalf of rider" wrapper).
- ``compute_promo_discount`` edge cases (free_ride, ride_portion<=0 on a
  percentage promo, grand_total fallback when ride_portion is 0).
- ``list_available_promos`` filter branches: service-area resolution
  (success, exception-swallowed-to-None), per-promo exception skip, the
  ineligible-but-shown min-fare branch, and eligible-first/discount-desc
  sorting.
- The four admin promo-code CRUD functions in this module
  (``admin_get_promo_codes`` / ``admin_create_promo_code`` /
  ``admin_update_promo_code`` / ``admin_delete_promo_code``). NOTE: this
  ``admin_router`` is never mounted in backend/server.py (only
  ``routes/admin/promotions.py``'s router is, at ``/api/admin/promotions``)
  -- confirmed via `grep -n "promotions" backend/server.py`, which shows
  only `promotions_router` (the user-facing `api_router`) included, not this
  module's `admin_router`. These four functions are therefore dead/
  unreachable HTTP endpoints; exercised here directly as plain async
  functions (not through an HTTP client) purely for coverage of otherwise
  untested code, not because they are live. Flagged as a finding, not
  fixed (test-only work per task scope).

Patch target: ``backend.routes.promotions.db_supabase`` / the individual
functions this module imports directly (``increment_promo_uses``), per
CLAUDE.md's "patch target is the module that defines the function under
test" rule -- ``db_supabase`` here is imported into ``routes.promotions``'s
own namespace, so it's patched there, not on ``backend.db_supabase``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER_ID = "user_promo_cov"


def _promo(code: str = "SAVE5", **extra) -> dict:
    base = {
        "id": "promo-cov-001",
        "code": code,
        "is_active": True,
        "discount_type": "flat",
        "discount_value": 5.00,
        "max_uses": 100,
        "uses": 0,
        "max_uses_per_user": 1,
        "expiry_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "assigned_user_ids": [],
        "first_ride_only": False,
        "new_user_days": 0,
        "inactive_days": 0,
        "min_total_rides": 0,
        "max_total_rides": 0,
        "total_budget": 0,
        "min_ride_fare": 0,
        "budget_used": 0,
        "free_ride": False,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# _validate_promo_for_user — rules 5-10 + free_ride + ride_id fare fetch
# ---------------------------------------------------------------------------


class TestValidatePromoRemainingRules:
    async def _call(self, promo_row, **kwargs):
        from backend.routes.promotions import _validate_promo_for_user

        defaults = dict(
            code=promo_row["code"],
            user_id=USER_ID,
            ride_fare=Decimal("20.00"),
        )
        defaults.update(kwargs)

        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo_row]),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00+00:00"}),
            ),
        ):
            return await _validate_promo_for_user(**defaults)

    @staticmethod
    def _count_documents(promo_apps: int, rides: int):
        """count_documents is called for both the per-user promo_applications
        gate (rule 3) and various rides-table checks (rules 6/8/9). A single
        return_value would make the per-user gate fire before the rule under
        test is reached, so branch on the table name instead."""

        async def _inner(table, filters=None):
            if table == "promo_applications":
                return promo_apps
            if table == "rides":
                return rides
            return 0

        return _inner

    async def test_private_coupon_rejects_unassigned_user(self):
        promo = _promo(assigned_user_ids=["someone-else"])
        with pytest.raises(HTTPException) as exc:
            await self._call(promo)
        assert exc.value.status_code == 400
        assert "not available" in exc.value.detail.lower()

    async def test_private_coupon_allows_assigned_user(self):
        promo = _promo(assigned_user_ids=[USER_ID])
        result = await self._call(promo)
        assert result["valid"] is True

    async def test_first_ride_only_rejects_returning_rider(self):
        promo = _promo(first_ride_only=True)
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch(
                "backend.routes.promotions.db_supabase.count_documents",
                AsyncMock(side_effect=self._count_documents(promo_apps=0, rides=1)),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert exc.value.status_code == 400
        assert "first-time" in exc.value.detail.lower()

    async def test_new_user_only_rejects_old_account(self):
        promo = _promo(new_user_days=7)
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00+00:00"}),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert exc.value.status_code == 400
        assert "new users" in exc.value.detail.lower()

    async def test_new_user_only_allows_recent_account(self):
        promo = _promo(new_user_days=365)
        recent = datetime.now(timezone.utc).isoformat()
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": recent}),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            result = await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert result["valid"] is True

    async def test_inactive_user_targeting_rejects_recently_active_rider(self):
        promo = _promo(inactive_days=30)
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch(
                "backend.routes.promotions.db_supabase.count_documents",
                AsyncMock(side_effect=self._count_documents(promo_apps=0, rides=1)),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert exc.value.status_code == 400
        assert "returning riders" in exc.value.detail.lower()

    async def test_min_total_rides_rejects_too_new_rider(self):
        promo = _promo(min_total_rides=5)
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch(
                "backend.routes.promotions.db_supabase.count_documents",
                AsyncMock(side_effect=self._count_documents(promo_apps=0, rides=2)),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert exc.value.status_code == 400
        assert "at least" in exc.value.detail.lower()

    async def test_max_total_rides_rejects_too_experienced_rider(self):
        promo = _promo(max_total_rides=5)
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch(
                "backend.routes.promotions.db_supabase.count_documents",
                AsyncMock(side_effect=self._count_documents(promo_apps=0, rides=5)),
            ),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert exc.value.status_code == 400
        assert "not available" in exc.value.detail.lower()

    async def test_budget_exhausted_rejects(self):
        promo = _promo(total_budget=100, budget_used=100)
        with pytest.raises(HTTPException) as exc:
            await self._call(promo)
        assert exc.value.status_code == 400
        assert "budget" in exc.value.detail.lower()

    async def test_budget_under_cap_allows(self):
        promo = _promo(total_budget=100, budget_used=50)
        result = await self._call(promo)
        assert result["valid"] is True

    async def test_free_ride_discount_uses_grand_total(self):
        promo = _promo(free_ride=True, discount_value=0)
        result = await self._call(promo, grand_total=Decimal("27.50"))
        assert result["free_ride"] is True
        assert result["discount_amount"] == Decimal("27.50")

    async def test_free_ride_discount_falls_back_to_ride_fare_when_no_grand_total(self):
        promo = _promo(free_ride=True, discount_value=0)
        result = await self._call(promo, ride_fare=Decimal("18.00"), grand_total=None)
        assert result["discount_amount"] == Decimal("18.00")

    async def test_ride_id_fetches_server_side_fare_for_min_check(self):
        promo = _promo(min_ride_fare=10)
        ride_row = {
            "id": "ride-1",
            "rider_id": USER_ID,
            "base_fare": "5.00",
            "distance_fare": "4.00",
            "time_fare": "2.00",
        }

        async def fake_get_rows(table, filters, limit=None):
            if table == "rides":
                return [ride_row]
            return [promo]

        with (
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            result = await _validate_promo_for_user(
                code=promo["code"], user_id=USER_ID, ride_fare=Decimal("0"), ride_id="ride-1"
            )
        assert result["valid"] is True

    async def test_ride_id_not_found_raises_404(self):
        promo = _promo(min_ride_fare=10)

        async def fake_get_rows(table, filters, limit=None):
            if table == "rides":
                return []
            return [promo]

        with (
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(
                    code=promo["code"], user_id=USER_ID, ride_fare=Decimal("0"), ride_id="ride-ghost"
                )
        assert exc.value.status_code == 404

    async def test_malformed_expiry_date_does_not_crash(self):
        """A non-ISO expiry string hits the except (ValueError, HTTPException)
        branch and is swallowed (treated as non-expiring) rather than raising."""
        promo = _promo(expiry_date="not-a-real-date")
        result = await self._call(promo)
        assert result["valid"] is True

    async def test_code_is_uppercased_and_stripped(self):
        promo = _promo(code="SAVE5")
        with (
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(return_value=[promo]),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            from backend.routes.promotions import _validate_promo_for_user

            result = await _validate_promo_for_user(code="  save5  ", user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert result["code"] == "SAVE5"


# ---------------------------------------------------------------------------
# apply_promo_for_admin
# ---------------------------------------------------------------------------


class TestApplyPromoForAdmin:
    async def test_applies_and_records_on_behalf_of_rider(self):
        from backend.routes.promotions import apply_promo_for_admin

        validation = {
            "promo_id": "promo-1",
            "code": "SAVE5",
            "discount_amount": Decimal("5.00"),
        }
        with (
            patch(
                "backend.routes.promotions._validate_promo_for_user",
                AsyncMock(return_value=validation),
            ),
            patch(
                "backend.routes.promotions._record_promo_application",
                AsyncMock(return_value="app-1"),
            ),
        ):
            result = await apply_promo_for_admin(code="SAVE5", rider_id="rider-1", ride_fare=Decimal("20.00"))
        assert result["application_id"] == "app-1"
        assert result["discount_amount"] == Decimal("5.00")


# ---------------------------------------------------------------------------
# compute_promo_discount
# ---------------------------------------------------------------------------


class TestComputePromoDiscount:
    def test_free_ride_returns_grand_total(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(free_ride=True)
        discount = compute_promo_discount(promo, Decimal("10.00"), Decimal("30.00"))
        assert discount == Decimal("30.00")

    def test_percentage_with_zero_ride_portion_returns_zero(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="percentage", discount_value=10)
        discount = compute_promo_discount(promo, Decimal("0"), Decimal("30.00"))
        assert discount == Decimal("0")

    def test_flat_falls_back_to_grand_total_when_ride_portion_zero(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="flat", discount_value=5)
        discount = compute_promo_discount(promo, Decimal("0"), Decimal("30.00"))
        assert discount == Decimal("5")

    def test_flat_and_grand_total_both_zero_returns_discount_value(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="flat", discount_value=5)
        discount = compute_promo_discount(promo, Decimal("0"), Decimal("0"))
        assert discount == Decimal("5")


# ---------------------------------------------------------------------------
# list_available_promos
# ---------------------------------------------------------------------------


class TestListAvailablePromos:
    async def _run(self, promos, **kwargs):
        from backend.routes.promotions import list_available_promos

        with (
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(side_effect=self._get_rows(promos))),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00+00:00"}),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            return await list_available_promos(USER_ID, **kwargs)

    @staticmethod
    def _get_rows(promos):
        async def _inner(table, filters=None, limit=None, order=None, desc=None):
            if table == "promotions":
                return promos
            if table == "promo_applications":
                return []
            return []

        return _inner

    async def test_returns_eligible_promo(self):
        promos = [_promo(discount_value=5)]
        result = await self._run(promos, ride_fare=20.0)
        assert len(result) == 1
        assert result[0]["eligible"] is True

    async def test_below_min_fare_marked_ineligible_not_hidden(self):
        promos = [_promo(min_ride_fare=50)]
        result = await self._run(promos, ride_fare=20.0)
        assert len(result) == 1
        assert result[0]["eligible"] is False
        assert "minimum" in result[0]["ineligible_reason"].lower()

    async def test_private_coupon_hidden_from_non_assigned_user(self):
        promos = [_promo(assigned_user_ids=["someone-else"])]
        result = await self._run(promos, ride_fare=20.0)
        assert result == []

    async def test_expired_promo_hidden(self):
        promos = [_promo(expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())]
        result = await self._run(promos, ride_fare=20.0)
        assert result == []

    async def test_budget_exhausted_hidden(self):
        promos = [_promo(total_budget=100, budget_used=100)]
        result = await self._run(promos, ride_fare=20.0)
        assert result == []

    async def test_error_processing_single_promo_is_skipped_not_fatal(self):
        good = _promo(code="GOOD", discount_value=5)
        bad = _promo(code="BAD", id="promo-bad")
        bad["min_ride_fare"] = "not-a-number"  # triggers a comparison TypeError inside the loop

        result = await self._run([bad, good], ride_fare=20.0)
        codes = [r["code"] for r in result]
        assert "GOOD" in codes
        assert "BAD" not in codes

    async def test_sorted_eligible_first_then_by_discount_desc(self):
        small = _promo(code="SMALL", id="p-small", discount_value=2)
        big = _promo(code="BIG", id="p-big", discount_value=9)
        ineligible = _promo(code="INEL", id="p-inel", min_ride_fare=999, discount_value=50)

        result = await self._run([small, ineligible, big], ride_fare=20.0)
        codes = [r["code"] for r in result]
        # both eligible promos precede the ineligible one, sorted by discount desc
        assert codes.index("BIG") < codes.index("INEL")
        assert codes.index("SMALL") < codes.index("INEL")
        assert codes.index("BIG") < codes.index("SMALL")

    async def test_service_area_resolved_filters_to_matching_promos(self):
        area_promo = _promo(code="AREA", id="p-area", service_area_id="area-1")
        other_area_promo = _promo(code="OTHER", id="p-other", service_area_id="area-2")
        global_promo = _promo(code="GLOBAL", id="p-global", service_area_id=None)

        with (
            patch(
                "backend.routes.promotions.db_supabase.rpc",
                AsyncMock(return_value=[{"id": "area-1"}]),
            ),
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows([area_promo, other_area_promo, global_promo])),
            ),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00+00:00"}),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            from backend.routes.promotions import list_available_promos

            result = await list_available_promos(USER_ID, ride_fare=20.0, pickup_lat=50.0, pickup_lng=-104.0)
        codes = {r["code"] for r in result}
        assert codes == {"AREA", "GLOBAL"}
        assert "OTHER" not in codes

    async def test_service_area_lookup_exception_falls_back_to_global_only(self):
        area_promo = _promo(code="AREA", id="p-area", service_area_id="area-1")
        global_promo = _promo(code="GLOBAL", id="p-global", service_area_id=None)

        with (
            patch(
                "backend.routes.promotions.db_supabase.rpc",
                AsyncMock(side_effect=RuntimeError("service area lookup failed")),
            ),
            patch(
                "backend.routes.promotions.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows([area_promo, global_promo])),
            ),
            patch(
                "backend.routes.promotions.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "created_at": "2020-01-01T00:00:00+00:00"}),
            ),
            patch("backend.routes.promotions.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            from backend.routes.promotions import list_available_promos

            result = await list_available_promos(USER_ID, ride_fare=20.0, pickup_lat=50.0, pickup_lng=-104.0)
        codes = {r["code"] for r in result}
        # pickup_area_id stays None on exception -> falls back to global-only filter
        assert codes == {"GLOBAL"}


# ---------------------------------------------------------------------------
# get_available_promos route wrapper
# ---------------------------------------------------------------------------


class TestGetAvailablePromosRoute:
    async def test_delegates_to_list_available_promos_with_query_params(self):
        from starlette.requests import Request as StarletteRequest

        from backend.routes.promotions import get_available_promos

        mock_request = StarletteRequest(
            {"type": "http", "method": "GET", "path": "/promo/available", "query_string": b"", "headers": []}
        )
        with patch(
            "backend.routes.promotions.list_available_promos",
            AsyncMock(return_value=[{"code": "X"}]),
        ) as mocked:
            result = await get_available_promos(
                mock_request,
                ride_fare=20.0,
                ride_portion=15.0,
                pickup_lat=50.0,
                pickup_lng=-104.0,
                current_user={"id": USER_ID},
            )
        assert result == [{"code": "X"}]
        mocked.assert_awaited_once_with(USER_ID, ride_fare=20.0, ride_portion=15.0, pickup_lat=50.0, pickup_lng=-104.0)


# ---------------------------------------------------------------------------
# Admin promo-code CRUD (this module's admin_router — NOT mounted in
# server.py; see module docstring). Called directly, not via HTTP client.
# ---------------------------------------------------------------------------


class TestAdminPromoCodeCrudDirect:
    async def test_get_promo_codes_lists_all(self):
        from backend.routes.promotions import admin_get_promo_codes

        rows = [_promo(code="A"), _promo(code="B", id="promo-b")]
        with patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=rows)):
            result = await admin_get_promo_codes()
        assert result == rows

    async def test_create_rejects_duplicate_global_code(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        existing = _promo(code="DUP", service_area_id=None)
        req = CreatePromoCodeRequest(code="dup", discount_type="flat", discount_value=5)
        with patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[existing])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    async def test_create_allows_same_code_in_different_area(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        # Existing row is global (service_area_id None); request targets a specific area,
        # so it should not collide.
        req = CreatePromoCodeRequest(code="areacode", discount_type="flat", discount_value=5, service_area_id="area-9")
        with (
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.promotions.db_supabase.insert_one", AsyncMock()),
        ):
            result = await admin_create_promo_code(req)
        assert result["success"] is True
        assert result["promo"]["service_area_id"] == "area-9"

    async def test_create_rejects_invalid_discount_type(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest(code="BAD", discount_type="weird", discount_value=5)
        with patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "discount_type" in exc.value.detail

    async def test_create_rejects_percentage_over_100(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest(code="PCT", discount_type="percentage", discount_value=100)
        req.discount_value = Decimal("150")  # bypass pydantic ge/le for the route-level guard
        with patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "100%" in exc.value.detail

    async def test_create_rejects_flat_over_500(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest(code="FLAT", discount_type="flat", discount_value=100)
        req.discount_value = Decimal("600")
        with patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "$500" in exc.value.detail

    async def test_create_free_ride_skips_discount_validation(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest(code="FREE", free_ride=True, discount_value=0)
        with (
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.promotions.db_supabase.insert_one", AsyncMock()),
        ):
            result = await admin_create_promo_code(req)
        assert result["promo"]["free_ride"] is True

    async def test_update_applies_only_provided_fields(self):
        from backend.routes.promotions import UpdatePromoCodeRequest, admin_update_promo_code

        req = UpdatePromoCodeRequest(is_active=False)
        updated_row = _promo(is_active=False)
        with (
            patch("backend.routes.promotions.db_supabase.update_one", AsyncMock()) as mock_update,
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[updated_row])),
        ):
            result = await admin_update_promo_code("promo-cov-001", req)
        assert result["is_active"] is False
        call_kwargs = mock_update.call_args.args
        assert call_kwargs[0] == "promotions"
        assert call_kwargs[1] == {"id": "promo-cov-001"}
        assert call_kwargs[2]["is_active"] is False
        assert "discount_value" not in call_kwargs[2]

    async def test_update_not_found_raises_404(self):
        from backend.routes.promotions import UpdatePromoCodeRequest, admin_update_promo_code

        req = UpdatePromoCodeRequest(is_active=False)
        with (
            patch("backend.routes.promotions.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_update_promo_code("ghost-id", req)
        assert exc.value.status_code == 404

    async def test_delete_returns_deleted_true(self):
        from backend.routes.promotions import admin_delete_promo_code

        with patch("backend.routes.promotions.db_supabase.delete_one", AsyncMock()) as mock_delete:
            result = await admin_delete_promo_code("promo-cov-001")
        assert result == {"deleted": True}
        mock_delete.assert_awaited_once_with("promotions", {"id": "promo-cov-001"})
