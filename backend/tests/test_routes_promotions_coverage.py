"""Coverage tests for backend/routes/promotions.py.

A1c Sub-tier C: test-only file, no application code changed. Written by
reading backend/routes/promotions.py directly (pytest was NOT run against
this file — coverage is estimated from source reading only).

Targets the largest previously-uncovered blocks:
  - CreatePromoCodeRequest.validate_discount_value field validator (66-75)
  - _validate_promo_for_user: naive-expiry normalization, ride_id fare
    lookup, private-coupon/first-ride/new-user/inactive-user/min-max-rides/
    budget rules, free_ride fallback to ride_fare (127-249)
  - apply_promo_for_admin (313-339)
  - compute_promo_discount ride_portion<=0 branch (405-430)
  - list_available_promos: service-area RPC success/failure, per-user usage
    map, every eligibility-rule "continue" branch, below-min-fare entry,
    and the per-promo try/except skip (433-604)
  - GET /promo/available route wrapper (607-624)
  - Admin promo-code CRUD under /admin/promo-codes: list/create (incl.
    duplicate-code and discount-cap guards)/update (incl. 404)/delete
    (636-723)

FOUND NOT FIXED (see TestExpiryNonStringBypass below):
  _validate_promo_for_user's rule 1 (expiry) is gated on
  ``isinstance(expiry, str)`` (promotions.py:129). If a promo row's
  ``expiry_date`` comes back as a ``datetime`` object instead of a string
  (e.g. a DB client that deserializes timestamptz columns to datetime,
  unlike list_available_promos's ``parse_iso_utc(...) if isinstance(str)
  else expiry`` handling at promotions.py:506, which handles both), the
  expiry check is silently skipped entirely and an expired promo validates
  successfully. Not fixed here per task instructions -- pinned as current
  (buggy) behavior only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER_ID = "user_promo_cov"
PROMO_MOD = "backend.routes.promotions"


def _promo(**extra) -> dict:
    base = {
        "id": "promo-cov-1",
        "code": "COVER10",
        "is_active": True,
        "free_ride": False,
        "discount_type": "flat",
        "discount_value": 5.00,
        "max_discount": None,
        "max_uses": 0,
        "max_uses_per_user": 1,
        "uses": 0,
        "expiry_date": None,
        "assigned_user_ids": [],
        "first_ride_only": False,
        "new_user_days": 0,
        "inactive_days": 0,
        "min_total_rides": 0,
        "max_total_rides": 0,
        "total_budget": 0,
        "budget_used": 0,
        "min_ride_fare": 0,
        "service_area_id": None,
        "description": "coverage promo",
    }
    base.update(extra)
    return base


def _mock_request():
    from starlette.requests import Request as StarletteRequest

    return StarletteRequest(
        {"type": "http", "method": "POST", "path": "/promo/validate", "query_string": b"", "headers": []}
    )


# ─────────────────────────────────────────────────────────────────────────
# CreatePromoCodeRequest validator (lines 66-75)
# ─────────────────────────────────────────────────────────────────────────


class TestCreatePromoCodeRequestValidator:
    def test_free_ride_bypasses_zero_discount_value(self):
        from backend.routes.promotions import CreatePromoCodeRequest

        req = CreatePromoCodeRequest(code="FREE1", free_ride=True, discount_value=Decimal("0"))
        assert req.discount_value == Decimal("0")

    def test_non_free_ride_zero_discount_value_rejected(self):
        from pydantic import ValidationError

        from backend.routes.promotions import CreatePromoCodeRequest

        with pytest.raises(ValidationError, match="greater than 0"):
            CreatePromoCodeRequest(code="ZERO", free_ride=False, discount_value=Decimal("0"))

    def test_percentage_over_100_rejected_by_validator(self):
        from pydantic import ValidationError

        from backend.routes.promotions import CreatePromoCodeRequest

        with pytest.raises(ValidationError, match="cannot exceed 100"):
            CreatePromoCodeRequest(code="OVER", discount_type="percentage", discount_value=Decimal("150"))

    def test_valid_flat_discount_accepted(self):
        from backend.routes.promotions import CreatePromoCodeRequest

        req = CreatePromoCodeRequest(code="OK5", discount_type="flat", discount_value=Decimal("5"))
        assert req.discount_value == Decimal("5")


# ─────────────────────────────────────────────────────────────────────────
# _validate_promo_for_user — additional rule branches
# ─────────────────────────────────────────────────────────────────────────


class TestValidatePromoForUserRules:
    async def _call(self, promo, **kwargs):
        from backend.routes.promotions import _validate_promo_for_user

        with (
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(side_effect=self._get_rows(promo, kwargs))),
            patch(f"{PROMO_MOD}.db_supabase.count_documents", AsyncMock(return_value=kwargs.get("count", 0))),
            patch(
                f"{PROMO_MOD}.db_supabase.get_user_by_id",
                AsyncMock(return_value=kwargs.get("user", {"id": USER_ID, "created_at": "2020-01-01T00:00:00"})),
            ),
        ):
            return await _validate_promo_for_user(
                code=promo["code"],
                user_id=USER_ID,
                ride_fare=kwargs.get("ride_fare", Decimal("20.00")),
                ride_id=kwargs.get("ride_id"),
                grand_total=kwargs.get("grand_total"),
            )

    @staticmethod
    def _get_rows(promo, kwargs):
        ride_rows = kwargs.get("ride_rows", None)

        async def _inner(table, *args, **kw):
            if table == "promotions":
                return [promo]
            if table == "rides" and ride_rows is not None:
                return ride_rows
            return []

        return _inner

    # -- naive-expiry normalization (line 133), not expired -> passes
    async def test_naive_expiry_in_future_normalized_to_utc(self):
        future_naive = (datetime.now(timezone.utc) + timedelta(days=5)).replace(tzinfo=None).isoformat()
        promo = _promo(expiry_date=future_naive)
        result = await self._call(promo)
        assert result["valid"] is True

    # -- malformed expiry string: ValueError swallowed, no expiry enforcement (lines 136-138)
    async def test_malformed_expiry_string_does_not_block(self):
        promo = _promo(expiry_date="not-a-date")
        result = await self._call(promo)
        assert result["valid"] is True

    # -- ride_id branch of min_fare check (168-176): ride found
    async def test_min_fare_checked_against_server_ride_when_ride_id_given(self):
        promo = _promo(min_ride_fare=Decimal("10.00"))
        ride_rows = [{"id": "ride-1", "rider_id": USER_ID, "base_fare": 3, "distance_fare": 4, "time_fare": 5}]
        result = await self._call(promo, ride_id="ride-1", ride_rows=ride_rows, ride_fare=Decimal("0"))
        assert result["valid"] is True

    async def test_min_fare_ride_id_not_found_raises_404(self):
        promo = _promo(min_ride_fare=Decimal("10.00"))
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, ride_id="ghost", ride_rows=[])
        assert exc.value.status_code == 404

    async def test_min_fare_ride_id_below_min_raises_400(self):
        promo = _promo(min_ride_fare=Decimal("50.00"))
        ride_rows = [{"id": "ride-1", "rider_id": USER_ID, "base_fare": 1, "distance_fare": 1, "time_fare": 1}]
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, ride_id="ride-1", ride_rows=ride_rows)
        assert exc.value.status_code == 400

    # -- private coupon (186)
    async def test_private_coupon_not_assigned_raises_400(self):
        promo = _promo(assigned_user_ids=["someone-else"])
        with pytest.raises(HTTPException) as exc:
            await self._call(promo)
        assert exc.value.status_code == 400
        assert "not available" in exc.value.detail.lower()

    async def test_private_coupon_assigned_passes(self):
        promo = _promo(assigned_user_ids=[USER_ID])
        result = await self._call(promo)
        assert result["valid"] is True

    # -- first_ride_only (190-192)
    # max_uses_per_user=0 (unlimited) so the shared count_documents mock's
    # return value only drives the first_ride_only rule, not rule 3 (per-
    # user limit), which would otherwise fire first on the same mocked count.
    async def test_first_ride_only_blocked_when_rides_exist(self):
        promo = _promo(first_ride_only=True, max_uses_per_user=0)
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, count=1)
        assert exc.value.status_code == 400
        assert "first-time" in exc.value.detail.lower()

    async def test_first_ride_only_passes_when_no_rides(self):
        promo = _promo(first_ride_only=True, max_uses_per_user=0)
        result = await self._call(promo, count=0)
        assert result["valid"] is True

    # -- new_user_days (197-201)
    async def test_new_user_days_blocks_old_account(self):
        promo = _promo(new_user_days=30)
        old_user = {"id": USER_ID, "created_at": "2020-01-01T00:00:00Z"}
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, user=old_user)
        assert exc.value.status_code == 400
        assert "new users" in exc.value.detail.lower()

    async def test_new_user_days_passes_for_recent_account(self):
        promo = _promo(new_user_days=30)
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = await self._call(promo, user={"id": USER_ID, "created_at": recent})
        assert result["valid"] is True

    async def test_new_user_days_no_user_found_skips_check(self):
        promo = _promo(new_user_days=30)
        result = await self._call(promo, user=None)
        assert result["valid"] is True

    # -- inactive_days (206-219)
    async def test_inactive_days_blocks_recently_active_rider(self):
        # max_uses_per_user=0 (unlimited) so the shared count_documents mock's
        # return value only drives the inactive_days rule, not rule 3
        # (per-user limit), which would otherwise fire first on count=1.
        promo = _promo(inactive_days=14, max_uses_per_user=0)
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, count=1)
        assert exc.value.status_code == 400
        assert "returning riders" in exc.value.detail.lower()

    async def test_inactive_days_passes_when_no_recent_rides(self):
        promo = _promo(inactive_days=14)
        result = await self._call(promo, count=0)
        assert result["valid"] is True

    # -- min/max total rides (225-235)
    async def test_min_total_rides_not_met_raises_400(self):
        # max_uses_per_user=0 (unlimited) so the shared count_documents mock's
        # return value only drives the min_total_rides rule, not rule 3.
        promo = _promo(min_total_rides=5, max_uses_per_user=0)
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, count=2)
        assert exc.value.status_code == 400
        assert "at least" in exc.value.detail.lower()

    async def test_max_total_rides_exceeded_raises_400(self):
        # max_uses_per_user=0 (unlimited) so the shared count_documents mock's
        # return value only drives the max_total_rides rule, not rule 3.
        promo = _promo(max_total_rides=5, max_uses_per_user=0)
        with pytest.raises(HTTPException) as exc:
            await self._call(promo, count=5)
        assert exc.value.status_code == 400
        assert "not available" in exc.value.detail.lower()

    async def test_min_max_total_rides_within_window_passes(self):
        promo = _promo(min_total_rides=1, max_total_rides=10, max_uses_per_user=0)
        result = await self._call(promo, count=3)
        assert result["valid"] is True

    # -- budget (240)
    async def test_budget_exhausted_raises_400(self):
        promo = _promo(total_budget=Decimal("100"), budget_used=Decimal("100"))
        with pytest.raises(HTTPException) as exc:
            await self._call(promo)
        assert exc.value.status_code == 400
        assert "budget" in exc.value.detail.lower()

    # -- free_ride fallback: grand_total None -> uses ride_fare (line 249)
    async def test_free_ride_without_grand_total_falls_back_to_ride_fare(self):
        promo = _promo(free_ride=True, discount_type="flat", discount_value=Decimal("0"))
        result = await self._call(promo, ride_fare=Decimal("42.00"), grand_total=None)
        assert result["free_ride"] is True
        assert result["discount_amount"] == Decimal("42.00")

    async def test_free_ride_with_grand_total_uses_grand_total(self):
        promo = _promo(free_ride=True, discount_type="flat", discount_value=Decimal("0"))
        result = await self._call(promo, ride_fare=Decimal("20.00"), grand_total=Decimal("27.50"))
        assert result["discount_amount"] == Decimal("27.50")


class TestExpiryNonStringBypass:
    """FOUND NOT FIXED: expiry_date as a datetime object skips the expiry
    rule entirely (isinstance(expiry, str) gate at promotions.py:129),
    unlike list_available_promos which handles both str and datetime.
    Pinning current (buggy) behavior, not fixing it.
    """

    async def test_expired_datetime_object_is_not_rejected(self):
        from backend.routes.promotions import _validate_promo_for_user

        expired_dt = datetime.now(timezone.utc) - timedelta(days=10)
        promo = _promo(expiry_date=expired_dt)  # datetime, not str

        async def _get_rows(table, *a, **kw):
            return [promo] if table == "promotions" else []

        with (
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(f"{PROMO_MOD}.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch(f"{PROMO_MOD}.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            # Does NOT raise, even though the promo is 10 days expired.
            result = await _validate_promo_for_user(code=promo["code"], user_id=USER_ID, ride_fare=Decimal("20.00"))
        assert result["valid"] is True


# ─────────────────────────────────────────────────────────────────────────
# apply_promo_for_admin (313-339)
# ─────────────────────────────────────────────────────────────────────────


class TestApplyPromoForAdmin:
    async def test_delegates_to_validate_and_record(self):
        from backend.routes.promotions import apply_promo_for_admin

        validation = {
            "promo_id": "promo-1",
            "code": "COVER10",
            "discount_amount": Decimal("5.00"),
        }
        with (
            patch(f"{PROMO_MOD}._validate_promo_for_user", AsyncMock(return_value=validation)),
            patch(f"{PROMO_MOD}._record_promo_application", AsyncMock(return_value="app-99")),
        ):
            result = await apply_promo_for_admin(
                code="cover10", rider_id="rider-1", ride_fare=Decimal("20.00"), ride_id="ride-1"
            )

        assert result == {
            "promo_id": "promo-1",
            "code": "COVER10",
            "discount_amount": Decimal("5.00"),
            "application_id": "app-99",
        }


# ─────────────────────────────────────────────────────────────────────────
# compute_promo_discount (405-430)
# ─────────────────────────────────────────────────────────────────────────


class TestComputePromoDiscount:
    def test_free_ride_returns_grand_total(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(free_ride=True)
        assert compute_promo_discount(promo, Decimal("10"), Decimal("15")) == Decimal("15")

    def test_percentage_zero_ride_portion_returns_zero(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="percentage", discount_value=Decimal("20"))
        assert compute_promo_discount(promo, Decimal("0"), Decimal("15")) == Decimal("0")

    def test_flat_zero_ride_portion_falls_back_to_grand_total_cap(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="flat", discount_value=Decimal("100"))
        assert compute_promo_discount(promo, Decimal("0"), Decimal("15")) == Decimal("15")

    def test_flat_zero_ride_portion_and_zero_grand_total_returns_raw_value(self):
        from backend.routes.promotions import compute_promo_discount

        promo = _promo(discount_type="flat", discount_value=Decimal("7"))
        assert compute_promo_discount(promo, Decimal("0"), Decimal("0")) == Decimal("7")


# ─────────────────────────────────────────────────────────────────────────
# list_available_promos / GET /promo/available
# ─────────────────────────────────────────────────────────────────────────


class _PatchTuple(tuple):
    """A tuple of unstarted `patch()` objects that also works as a no-op
    context manager, so `with self._patches(...) as patches:` can wrap the
    manual `for p in patches: p.start()/p.stop()` pattern used below."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestListAvailablePromos:
    def _patches(self, promos, *, rpc_result=None, rpc_error=None, apps=None, count=0, user=None):
        async def _get_rows(table, *args, **kwargs):
            if table == "promotions":
                return promos
            if table == "promo_applications":
                return apps or []
            return []

        async def _rpc(*args, **kwargs):
            if rpc_error:
                raise rpc_error
            return rpc_result or []

        return _PatchTuple(
            (
                patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
                patch(f"{PROMO_MOD}.db_supabase.rpc", AsyncMock(side_effect=_rpc)),
                patch(f"{PROMO_MOD}.db_supabase.count_documents", AsyncMock(return_value=count)),
                patch(
                    f"{PROMO_MOD}.db_supabase.get_user_by_id",
                    AsyncMock(
                        return_value=user if user is not None else {"id": USER_ID, "created_at": "2020-01-01T00:00:00"}
                    ),
                ),
            )
        )

    async def test_pickup_resolves_service_area_and_filters(self):
        from backend.routes.promotions import list_available_promos

        area_promo = _promo(id="area-promo", service_area_id="area-1")
        global_promo = _promo(id="global-promo", service_area_id=None, code="GLOBAL")
        other_area_promo = _promo(id="other-area", service_area_id="area-2", code="OTHER")
        promos = [area_promo, global_promo, other_area_promo]

        with self._patches(promos, rpc_result=[{"id": "area-1"}]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0, pickup_lat=52.1, pickup_lng=-106.6)
            finally:
                for p in patches:
                    p.stop()

        ids = {e["promo_id"] for e in result}
        assert "area-promo" in ids
        assert "global-promo" in ids
        assert "other-area" not in ids

    async def test_pickup_rpc_failure_falls_back_to_global_only(self):
        from backend.routes.promotions import list_available_promos

        global_promo = _promo(id="global-only", service_area_id=None, code="GLOB")
        area_promo = _promo(id="area-x", service_area_id="area-9", code="AREAX")

        with self._patches([global_promo, area_promo], rpc_error=RuntimeError("boom")) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0, pickup_lat=1.0, pickup_lng=2.0)
            finally:
                for p in patches:
                    p.stop()

        ids = {e["promo_id"] for e in result}
        assert ids == {"global-only"}

    async def test_no_pickup_shows_only_global_promos(self):
        from backend.routes.promotions import list_available_promos

        global_promo = _promo(id="g1", service_area_id=None)
        area_promo = _promo(id="a1", service_area_id="area-1", code="AR1")

        with self._patches([global_promo, area_promo]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()

        ids = {e["promo_id"] for e in result}
        assert ids == {"g1"}

    async def test_per_user_usage_map_excludes_exhausted_promo(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="used-up", max_uses_per_user=1)
        apps = [{"promo_id": "used-up", "user_id": USER_ID}]

        with self._patches([promo], apps=apps) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()

        assert result == []

    async def test_expired_promo_excluded(self):
        from backend.routes.promotions import list_available_promos

        expired = _promo(id="expired-1", expiry_date=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        with self._patches([expired]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_max_uses_reached_excluded(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="maxed", max_uses=1, uses=1)
        with self._patches([promo]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_private_coupon_excluded_when_not_assigned(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="private-1", assigned_user_ids=["other-user"])
        with self._patches([promo]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_first_ride_only_excluded_for_returning_rider(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="first-ride", first_ride_only=True)
        with self._patches([promo], count=3) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_new_user_days_excluded_for_old_account(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="new-user", new_user_days=10)
        old_user = {"id": USER_ID, "created_at": "2018-01-01T00:00:00Z"}
        with self._patches([promo], user=old_user) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_inactive_days_excluded_for_recently_active_rider(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="inactive", inactive_days=7)
        with self._patches([promo], count=1) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_min_total_rides_excluded_when_below_threshold(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="min-rides", min_total_rides=5)
        with self._patches([promo], count=1) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_max_total_rides_excluded_when_at_or_above_threshold(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="max-rides", max_total_rides=2)
        with self._patches([promo], count=2) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_budget_exhausted_excluded(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="budget-out", total_budget=Decimal("50"), budget_used=Decimal("50"))
        with self._patches([promo]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()
        assert result == []

    async def test_below_min_fare_marked_ineligible_not_hidden(self):
        from backend.routes.promotions import list_available_promos

        promo = _promo(id="min-fare", min_ride_fare=Decimal("50.00"))
        with self._patches([promo]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0, ride_portion=20.0)
            finally:
                for p in patches:
                    p.stop()

        assert len(result) == 1
        assert result[0]["eligible"] is False
        assert "minimum ride fare" in result[0]["ineligible_reason"].lower()

    async def test_promo_row_error_is_skipped_not_fatal(self):
        """A malformed promo row (missing 'id') raises KeyError inside the
        per-promo try block and is skipped rather than failing the whole call."""
        from backend.routes.promotions import list_available_promos

        broken = _promo()
        del broken["id"]  # p["id"] access inside the loop raises KeyError
        good = _promo(id="good-1", code="GOOD")

        with self._patches([broken, good]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0)
            finally:
                for p in patches:
                    p.stop()

        ids = {e["promo_id"] for e in result}
        assert ids == {"good-1"}

    async def test_sorted_eligible_first_then_biggest_discount(self):
        from backend.routes.promotions import list_available_promos

        cheap_eligible = _promo(id="cheap", code="CHEAP", discount_type="flat", discount_value=Decimal("2"))
        rich_eligible = _promo(id="rich", code="RICH", discount_type="flat", discount_value=Decimal("15"))
        ineligible = _promo(id="ineligible", code="INEL", min_ride_fare=Decimal("999"))

        with self._patches([cheap_eligible, rich_eligible, ineligible]) as patches:
            for p in patches:
                p.start()
            try:
                result = await list_available_promos(USER_ID, ride_fare=20.0, ride_portion=20.0)
            finally:
                for p in patches:
                    p.stop()

        ordered_ids = [e["promo_id"] for e in result]
        assert ordered_ids == ["rich", "cheap", "ineligible"]


class TestGetAvailablePromosRoute:
    async def test_route_delegates_to_list_available_promos(self):
        from backend.routes.promotions import get_available_promos

        with patch(f"{PROMO_MOD}.list_available_promos", AsyncMock(return_value=[{"promo_id": "p1"}])) as mock_list:
            result = await get_available_promos(
                request=_mock_request(),
                ride_fare=25.0,
                ride_portion=20.0,
                pickup_lat=52.0,
                pickup_lng=-106.0,
                current_user={"id": USER_ID},
            )

        assert result == [{"promo_id": "p1"}]
        mock_list.assert_awaited_once_with(
            USER_ID, ride_fare=25.0, ride_portion=20.0, pickup_lat=52.0, pickup_lng=-106.0
        )


# ─────────────────────────────────────────────────────────────────────────
# Admin promo-code CRUD (/admin/promo-codes)
# ─────────────────────────────────────────────────────────────────────────


class TestAdminGetPromoCodes:
    async def test_returns_rows_from_db(self):
        from backend.routes.promotions import admin_get_promo_codes

        rows = [_promo(id="p1"), _promo(id="p2", code="OTHER")]
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=rows)) as mock_get:
            result = await admin_get_promo_codes()

        assert result == rows
        mock_get.assert_awaited_once_with("promotions", order="created_at", desc=True, limit=500)


class TestAdminCreatePromoCode:
    def _req(self, **overrides):
        from backend.routes.promotions import CreatePromoCodeRequest

        payload = {"code": "newcode", "discount_type": "flat", "discount_value": Decimal("5")}
        payload.update(overrides)
        return CreatePromoCodeRequest(**payload)

    async def test_duplicate_global_code_raises_400(self):
        from backend.routes.promotions import admin_create_promo_code

        existing = [_promo(code="NEWCODE", service_area_id=None)]
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=existing)):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(self._req())
        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    async def test_duplicate_in_same_area_raises_400(self):
        from backend.routes.promotions import admin_create_promo_code

        existing = [_promo(code="NEWCODE", service_area_id="area-1")]
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=existing)):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(self._req(service_area_id="area-1"))
        assert exc.value.status_code == 400
        assert "service area" in exc.value.detail

    async def test_same_code_different_area_allowed(self):
        from backend.routes.promotions import admin_create_promo_code

        # get_rows filtered by (code, area) lookup — no row for this area.
        with (
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(f"{PROMO_MOD}.db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
        ):
            result = await admin_create_promo_code(self._req(service_area_id="area-2"))

        assert result["success"] is True
        assert result["promo"]["service_area_id"] == "area-2"
        mock_insert.assert_awaited_once()

    async def test_invalid_discount_type_raises_400(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest.model_construct(
            code="BADTYPE",
            free_ride=False,
            discount_type="bogus",
            discount_value=Decimal("5"),
            max_discount=None,
            max_uses=100,
            max_uses_per_user=1,
            expiry_date=None,
            is_active=True,
            description=None,
            service_area_id=None,
        )
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "discount_type" in exc.value.detail

    async def test_percentage_over_100_raises_400_at_route_level(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        # Bypass the pydantic field_validator (already covers 66-75) so the
        # route-level P1-4 guard (line 665-666) is exercised independently.
        req = CreatePromoCodeRequest.model_construct(
            code="PCTOVER",
            free_ride=False,
            discount_type="percentage",
            discount_value=Decimal("150"),
            max_discount=None,
            max_uses=100,
            max_uses_per_user=1,
            expiry_date=None,
            is_active=True,
            description=None,
            service_area_id=None,
        )
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "100%" in exc.value.detail

    async def test_flat_over_500_raises_400_at_route_level(self):
        from backend.routes.promotions import CreatePromoCodeRequest, admin_create_promo_code

        req = CreatePromoCodeRequest.model_construct(
            code="FLATOVER",
            free_ride=False,
            discount_type="flat",
            discount_value=Decimal("501"),
            max_discount=None,
            max_uses=100,
            max_uses_per_user=1,
            expiry_date=None,
            is_active=True,
            description=None,
            service_area_id=None,
        )
        with patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_create_promo_code(req)
        assert exc.value.status_code == 400
        assert "$500" in exc.value.detail

    async def test_free_ride_bypasses_discount_type_checks(self):
        from backend.routes.promotions import admin_create_promo_code

        req = self._req(free_ride=True, discount_type="bogus", discount_value=Decimal("0"))
        with (
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(f"{PROMO_MOD}.db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
        ):
            result = await admin_create_promo_code(req)

        assert result["success"] is True
        assert result["promo"]["free_ride"] is True
        mock_insert.assert_awaited_once()

    async def test_create_success_uppercases_code_and_defaults_uses_zero(self):
        from backend.routes.promotions import admin_create_promo_code

        with (
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(f"{PROMO_MOD}.db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
        ):
            result = await admin_create_promo_code(self._req(code="  lower10  "))

        assert result["promo"]["code"] == "LOWER10"
        assert result["promo"]["uses"] == 0
        inserted_table, inserted_doc = mock_insert.call_args[0]
        assert inserted_table == "promotions"
        assert inserted_doc["code"] == "LOWER10"


class TestAdminUpdatePromoCode:
    async def test_update_writes_only_provided_fields(self):
        from backend.routes.promotions import UpdatePromoCodeRequest, admin_update_promo_code

        req = UpdatePromoCodeRequest(discount_value=Decimal("9"), is_active=False)
        updated_row = _promo(id="promo-1", discount_value=Decimal("9"), is_active=False)

        with (
            patch(f"{PROMO_MOD}.db_supabase.update_one", AsyncMock(return_value=None)) as mock_update,
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[updated_row])),
        ):
            result = await admin_update_promo_code("promo-1", req)

        assert result == updated_row
        call_table, call_match, call_data = mock_update.call_args[0]
        assert call_table == "promotions"
        assert call_match == {"id": "promo-1"}
        assert call_data["discount_value"] == Decimal("9")
        assert call_data["is_active"] is False
        assert "description" not in call_data  # untouched field omitted

    async def test_update_not_found_raises_404(self):
        from backend.routes.promotions import UpdatePromoCodeRequest, admin_update_promo_code

        req = UpdatePromoCodeRequest(is_active=False)
        with (
            patch(f"{PROMO_MOD}.db_supabase.update_one", AsyncMock(return_value=None)),
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_update_promo_code("ghost", req)
        assert exc.value.status_code == 404


class TestAdminDeletePromoCode:
    async def test_delete_calls_db_and_returns_deleted_true(self):
        from backend.routes.promotions import admin_delete_promo_code

        with patch(f"{PROMO_MOD}.db_supabase.delete_one", AsyncMock(return_value=None)) as mock_delete:
            result = await admin_delete_promo_code("promo-1")

        assert result == {"deleted": True}
        mock_delete.assert_awaited_once_with("promotions", {"id": "promo-1"})


# ─────────────────────────────────────────────────────────────────────────
# E5 kill switch: promo_redemption_enabled
# ─────────────────────────────────────────────────────────────────────────


class TestPromoRedemptionKillSwitch:
    """_validate_promo_for_user is the single shared chokepoint both
    POST /promo/apply (rider self-service) and apply_promo_for_admin
    (admin apply-on-behalf-of-rider) funnel through -- one flag check here
    covers both."""

    async def test_flag_off_raises_503_before_any_promo_lookup(self):
        from backend.routes.promotions import _validate_promo_for_user

        with (
            patch(f"{PROMO_MOD}.get_app_settings", AsyncMock(return_value={"promo_redemption_enabled": False})),
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock()) as mock_get_rows,
        ):
            with pytest.raises(HTTPException) as exc:
                await _validate_promo_for_user(code="COVER10", user_id=USER_ID, ride_fare=Decimal("20.00"))

        assert exc.value.status_code == 503
        mock_get_rows.assert_not_awaited()

    async def test_flag_missing_key_defaults_to_enabled(self):
        """A settings dict with no promo_redemption_enabled key (legacy
        row) must still proceed -- the flag defaults to enabled."""
        from backend.routes.promotions import _validate_promo_for_user

        promo = _promo()
        with (
            patch(f"{PROMO_MOD}.get_app_settings", AsyncMock(return_value={})),
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[promo])),
            patch(f"{PROMO_MOD}.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            result = await _validate_promo_for_user(code="COVER10", user_id=USER_ID, ride_fare=Decimal("20.00"))

        assert result["valid"] is True

    async def test_settings_lookup_error_fails_open(self):
        """A settings-read error must never itself block redemption."""
        from backend.routes.promotions import _validate_promo_for_user

        promo = _promo()
        with (
            patch(f"{PROMO_MOD}.get_app_settings", AsyncMock(side_effect=RuntimeError("settings down"))),
            patch(f"{PROMO_MOD}.db_supabase.get_rows", AsyncMock(return_value=[promo])),
            patch(f"{PROMO_MOD}.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            result = await _validate_promo_for_user(code="COVER10", user_id=USER_ID, ride_fare=Decimal("20.00"))

        assert result["valid"] is True
