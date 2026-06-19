"""Unit tests for the admin referral analytics endpoint.

Covers the redemption funnel (qualified/redeemed/processing/failed), amounts
paid (total + average over the two reward sides), the daily redemption trend,
the rider/driver kind split, the service-area + date filters, and the
top-of-funnel `total_referred` being suppressed when an area filter is active.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from backend.routes.admin import drivers as admin_drivers


def _rows_router(payouts, users):
    """Return an AsyncMock for db_supabase.get_rows that dispatches by table."""

    async def _impl(table, *args, **kwargs):
        if table == "referral_payouts":
            return payouts
        if table == "users":
            return users
        return []

    return AsyncMock(side_effect=_impl)


def _call(**kwargs):
    params = {"source": "driver", "service_area_id": None, "start": None, "end": None, "admin": {"role": "admin"}}
    params.update(kwargs)
    return asyncio.run(admin_drivers.admin_get_referral_analytics(**params))


class TestReferralAnalytics:
    def test_funnel_counts_and_amounts(self):
        payouts = [
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-01T10:00:00Z", "created_at": "2026-06-01T09:00:00Z"},
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-02T10:00:00Z", "created_at": "2026-06-02T09:00:00Z"},
            {"status": "processing", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": None, "created_at": "2026-06-03T09:00:00Z"},
            {"status": "failed", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": None, "created_at": "2026-06-03T09:30:00Z"},
        ]
        users = [
            {"referred_by": "d1", "referral_code_used": "DRIVERABC", "created_at": "2026-06-01T00:00:00Z"},
            {"referred_by": "d1", "referral_code_used": "DRIVERABC", "created_at": "2026-06-02T00:00:00Z"},
            {"referred_by": "r1", "referral_code_used": "RIDEXYZ", "created_at": "2026-06-02T00:00:00Z"},  # rider — excluded for driver source
        ]
        with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, users)):
            res = _call(source="driver")
        f = res["funnel"]
        assert f["qualified"] == 4
        assert f["redeemed"] == 2
        assert f["processing"] == 1
        assert f["failed"] == 1
        assert f["total_paid"] == "20.00"
        assert f["avg_paid"] == "10.00"
        assert f["total_referred"] == 2  # only driver-coded sign-ups
        assert f["redemption_rate"] == 1.0  # 2 redeemed / 2 referred

    def test_rider_source_counts_both_reward_sides(self):
        payouts = [
            {"status": "paid", "referrer_reward": "5.00", "referee_reward": "5.00", "paid_at": "2026-06-01T10:00:00Z", "created_at": "2026-06-01T09:00:00Z"},
        ]
        with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, [])):
            res = _call(source="rider")
        # Both sides of a rider referral are real money paid out.
        assert res["funnel"]["total_paid"] == "10.00"
        assert res["source"] == "rider"

    def test_date_filter_excludes_out_of_window(self):
        payouts = [
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-05-01T10:00:00Z", "created_at": "2026-05-01T09:00:00Z"},
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-15T10:00:00Z", "created_at": "2026-06-15T09:00:00Z"},
        ]
        with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, [])):
            res = _call(start="2026-06-01", end="2026-06-30")
        assert res["funnel"]["qualified"] == 1
        assert res["funnel"]["redeemed"] == 1
        assert [t["date"] for t in res["trend"]] == ["2026-06-15"]

    def test_service_area_filter_suppresses_total_referred(self):
        payouts = [
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-01T10:00:00Z", "created_at": "2026-06-01T09:00:00Z"},
        ]
        captured = {}

        async def _impl(table, filt=None, *args, **kwargs):
            if table == "referral_payouts":
                captured["filt"] = filt
                return payouts
            if table == "users":
                raise AssertionError("users must not be queried when an area filter is active")
            return []

        with patch.object(admin_drivers.db_supabase, "get_rows", AsyncMock(side_effect=_impl)):
            res = _call(service_area_id="area-regina")
        assert res["funnel"]["total_referred"] is None
        assert res["funnel"]["redemption_rate"] is None
        assert captured["filt"]["service_area_id"] == "area-regina"
        assert captured["filt"]["kind"] == "driver"

    def test_trend_aggregates_per_day(self):
        payouts = [
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-01T10:00:00Z", "created_at": "2026-06-01T09:00:00Z"},
            {"status": "paid", "referrer_reward": "10.00", "referee_reward": "0", "paid_at": "2026-06-01T18:00:00Z", "created_at": "2026-06-01T09:00:00Z"},
        ]
        with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, [])):
            res = _call()
        assert res["trend"] == [{"date": "2026-06-01", "redeemed": 2, "paid": "20.00"}]
