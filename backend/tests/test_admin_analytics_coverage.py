"""Coverage tests for backend/routes/admin/analytics.py.

Targets the endpoints not already exercised by test_admin_surge_history.py
and test_analytics_geohash.py: cancellation-reasons, driver-acceptance,
overview (incl. Redis cache), dashboard, demand-forecast(+summary), and
driver-offer-stats/-trends. Read-only aggregation endpoints, so tests focus
on: happy-path shape, empty/degenerate aggregates (division-by-zero guards),
and the 503-on-DB-error path required by CLAUDE.md's "surface errors loudly"
convention.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard"],
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


# ── cancellation-reasons ──────────────────────────────────────────────


class TestCancellationReasons:
    # ACTION_ITEMS.md D7: this endpoint is now cached the same way /overview
    # is (redis_get/redis_set around the RPC) — every test here explicitly
    # patches both, same convention as TestAnalyticsOverview below, so a
    # cached result from one test can't leak into another via the shared
    # module-level in-memory redis fallback (no REDIS_URL in the test env).

    def test_happy_path_computes_pct(self, admin_client):
        bd = {
            "total": 10,
            "reasons": [{"reason": "no_driver", "count": 5}, {"reason": "rider_cancel", "count": 5}],
            "by_party": [{"party": "rider", "count": 5}, {"party": "system", "count": 5}],
            "hourly": {"3": 2},
        }
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[bd])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons", params={"date_range": "7d"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cancellations"] == 10
        assert data["reasons"][0]["pct"] == 50.0
        assert len(data["hourly_distribution"]) == 24
        assert data["hourly_distribution"][3]["count"] == 2

    def test_zero_total_no_division_error(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert resp.status_code == 200
        assert resp.json()["total_cancellations"] == 0

    def test_rpc_error_returns_503(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert resp.status_code == 503

    def test_cache_hit_returns_cached_payload_without_rpc(self, admin_client):
        import json

        cached = json.dumps({"date_range": "30d", "total_cancellations": 999})
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=cached)),
            patch(
                "backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=AssertionError("should not be called"))
            ),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert resp.status_code == 200
        assert resp.json()["total_cancellations"] == 999

    def test_corrupt_cache_falls_through_to_fresh_fetch(self, admin_client):
        bd = {"total": 2, "reasons": [{"reason": "no_driver", "count": 2}]}
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value="{not json")),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[bd])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert resp.status_code == 200
        assert resp.json()["total_cancellations"] == 2

    def test_different_service_area_gets_its_own_cache_key(self, admin_client):
        """A cached result for one service_area_id must not be served for a
        different one — regression pin for the cache key including it."""
        seen_keys = []

        async def fake_get(key):
            seen_keys.append(key)
            return None

        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(side_effect=fake_get)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            admin_client.get("/api/admin/analytics/cancellation-reasons", params={"service_area_id": "area-1"})
            admin_client.get("/api/admin/analytics/cancellation-reasons", params={"service_area_id": "area-2"})
        assert seen_keys[0] != seen_keys[1]

    def test_redis_set_failure_does_not_break_response(self, admin_client):
        """Redis being unavailable must not turn a successful fetch into a 500."""
        bd = {"total": 1, "reasons": [{"reason": "no_driver", "count": 1}]}
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[bd])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            resp = admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert resp.status_code == 200


# ── driver-acceptance ─────────────────────────────────────────────────


class TestDriverAcceptance:
    def test_happy_path_ranks_and_summarizes(self, admin_client):
        drivers = [
            {"id": "d1", "user_id": "u1", "service_area_id": "area-1", "rating": 4.9, "is_online": True},
            {"id": "d2", "user_id": "u2", "service_area_id": "area-1", "rating": 4.5, "is_online": False},
        ]
        acc_rows = [
            {"driver_id": "d1", "total_rides": 10, "completed": 9, "cancelled_by_driver": 1},
            {"driver_id": "d2", "total_rides": 10, "completed": 5, "cancelled_by_driver": 5},
        ]
        users = [
            {"id": "u1", "first_name": "Ann", "last_name": "A"},
            {"id": "u2", "first_name": "Bob", "last_name": "B"},
        ]
        with (
            patch("backend.routes.admin.analytics.db.get_rows", AsyncMock(side_effect=[drivers, users])),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=acc_rows)),
        ):
            resp = admin_client.get("/api/admin/analytics/driver-acceptance", params={"service_area_id": "area-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_drivers"] == 2
        # d1 has 90% acceptance, ranked first
        assert data["drivers"][0]["driver_id"] == "d1"
        assert data["drivers"][0]["acceptance_rate"] == 90.0
        assert data["low_performer_count"] == 1  # d2 below 70%

    def test_no_drivers_matching_area_returns_empty(self, admin_client):
        with patch("backend.routes.admin.analytics.db.get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get(
                "/api/admin/analytics/driver-acceptance", params={"service_area_id": "no-such-area"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_drivers"] == 0
        assert data["avg_acceptance_rate"] == 0

    def test_drivers_fetch_error_returns_503(self, admin_client):
        with patch("backend.routes.admin.analytics.db.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
            resp = admin_client.get("/api/admin/analytics/driver-acceptance")
        assert resp.status_code == 503

    def test_rpc_error_returns_503(self, admin_client):
        drivers = [{"id": "d1", "user_id": "u1"}]
        with (
            patch("backend.routes.admin.analytics.db.get_rows", AsyncMock(return_value=drivers)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            resp = admin_client.get("/api/admin/analytics/driver-acceptance")
        assert resp.status_code == 503


# ── overview (with Redis cache) ───────────────────────────────────────


class TestAnalyticsOverview:
    def test_cache_hit_returns_cached_payload_without_rpc(self, admin_client):
        import json

        cached = json.dumps({"date_range": "30d", "total_rides": 999})
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=cached)),
            patch(
                "backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=AssertionError("should not be called"))
            ),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 200
        assert resp.json()["total_rides"] == 999

    def test_corrupt_cache_falls_through_to_fresh_fetch(self, admin_client):
        ov = {"total": 4, "completed": 2, "cancelled": 1, "total_revenue": "10.00", "total_tips": "1.00"}
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value="{not json")),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[ov])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 200
        assert resp.json()["total_rides"] == 4
        assert resp.json()["avg_fare"] == 5.0

    def test_no_rides_avoids_division_by_zero(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["completion_rate"] == 0
        assert data["avg_fare"] == 0

    def test_rpc_error_returns_503(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 503

    def test_redis_set_failure_does_not_break_response(self, admin_client):
        """Redis being unavailable must not turn a successful fetch into a 500."""
        ov = {"total": 1, "completed": 1, "total_revenue": "5.00"}
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[ov])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 200


# ── dashboard ──────────────────────────────────────────────────────────


class TestDashboardOverview:
    def test_happy_path_aggregates_counts_and_money(self, admin_client):
        money_row = {
            "ride_volume": "100.00",
            "driver_earnings": "100.00",
            "spinr_pass_earnings": "10.00",
            "platform_revenue": "10.00",
        }
        with (
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=3)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[money_row])),
        ):
            resp = admin_client.get("/api/admin/analytics/dashboard", params={"range": "today"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["drivers"]["total"] == 3
        assert data["money"]["ride_volume"] == "100.00"

    def test_money_rpc_failure_degrades_to_null_money_not_500(self, admin_client):
        with (
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            resp = admin_client.get("/api/admin/analytics/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["money"]["ride_volume"] is None
        assert data["rides"]["total"] == 0

    def test_service_area_filter_applied_to_counts(self, admin_client):
        mock_count = AsyncMock(return_value=1)
        with (
            patch("backend.db_supabase.count_documents", mock_count),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{}])),
        ):
            resp = admin_client.get(
                "/api/admin/analytics/dashboard", params={"service_area_id": "area-9", "range": "7d"}
            )
        assert resp.status_code == 200
        # the drivers-total count (first call) is scoped by service area
        first_call_filters = mock_count.call_args_list[0].args[1]
        assert first_call_filters.get("service_area_id") == "area-9"

    def test_ride_counts_exclude_legacy_imported_rides(self, admin_client):
        """migration 341: rides_total and every per-status breakdown count
        must exclude legacy-imported rides, same predicate as
        admin_ride_money_rollup (302) — a legacy ride's created_at is its
        true historical old-app date, so it can sit inside a 24h/7d window
        and silently inflate these homepage stat cards otherwise (found in
        this session's migration-data audit)."""
        mock_count = AsyncMock(return_value=0)
        with (
            patch("backend.db_supabase.count_documents", mock_count),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{}])),
        ):
            resp = admin_client.get("/api/admin/analytics/dashboard", params={"range": "today"})
        assert resp.status_code == 200

        rides_calls = [c for c in mock_count.call_args_list if c.args[0] == "rides"]
        # rides_total (windowed, no status filter) + one call per breakdown
        # status — all but rides_active (unwindowed, status $in active
        # statuses — no legacy row is ever in an active status) must carry
        # the exclusion.
        windowed_rides_calls = [c for c in rides_calls if "$and" in c.args[1]]
        assert windowed_rides_calls, "expected at least one windowed rides count call"
        for call in windowed_rides_calls:
            assert call.args[1].get("legacy_import_metadata") == {"$eq": {}}


# ── demand forecast ────────────────────────────────────────────────────


class TestDemandForecast:
    def test_forecast_endpoint_delegates_to_util(self, admin_client):
        with patch(
            "backend.utils.demand_forecast.forecast_demand",
            AsyncMock(return_value=[{"hour": 1, "predicted_rides": 5}]),
        ):
            resp = admin_client.get(
                "/api/admin/analytics/demand-forecast", params={"area_id": "area-1", "hours_ahead": 12}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours_ahead"] == 12
        assert data["forecast"] == [{"hour": 1, "predicted_rides": 5}]

    def test_forecast_summary_endpoint_delegates_to_util(self, admin_client):
        with patch(
            "backend.utils.demand_forecast.get_forecast_summary",
            AsyncMock(return_value={"trend": "up"}),
        ):
            resp = admin_client.get("/api/admin/analytics/demand-forecast/summary")
        assert resp.status_code == 200
        assert resp.json() == {"trend": "up"}


# ── driver-offer-stats / driver-offer-trends ────────────────────────────


class TestDriverOfferStats:
    def test_happy_path_computes_rates(self, admin_client):
        rows = [
            {
                "driver_id": "d1",
                "offered": 10,
                "accepted": 8,
                "declined": 1,
                "ignored": 1,
                "preempted": 0,
                "pending": 0,
                "avg_response_secs": 4.2,
            }
        ]
        drivers = [{"id": "d1", "user_id": "u1", "service_area_id": "area-1", "rating": 4.8, "is_online": True}]
        users = [{"id": "u1", "first_name": "Cy", "last_name": "D"}]
        with (
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=rows)),
            patch(
                "backend.routes.admin.analytics._fetch_rows_in_chunks",
                AsyncMock(side_effect=[drivers, users]),
            ),
        ):
            resp = admin_client.get("/api/admin/analytics/driver-offer-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drivers"][0]["accept_rate"] == 80.0
        assert data["totals"]["offered"] == 10

    def test_no_rows_returns_empty(self, admin_client):
        with patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[])):
            resp = admin_client.get("/api/admin/analytics/driver-offer-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_drivers"] == 0
        assert data["drivers"] == []

    def test_rpc_error_returns_503(self, admin_client):
        with patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))):
            resp = admin_client.get("/api/admin/analytics/driver-offer-stats")
        assert resp.status_code == 503

    def test_driver_fetch_error_returns_503(self, admin_client):
        rows = [
            {"driver_id": "d1", "offered": 1, "accepted": 1, "declined": 0, "ignored": 0, "preempted": 0, "pending": 0}
        ]
        with (
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=rows)),
            patch(
                "backend.routes.admin.analytics._fetch_rows_in_chunks",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            resp = admin_client.get("/api/admin/analytics/driver-offer-stats")
        assert resp.status_code == 503


class TestDriverOfferTrends:
    def test_happy_path(self, admin_client):
        rows = [{"day": "2026-07-01", "offered": 5, "accepted": 3, "declined": 1, "ignored": 1, "preempted": 0}]
        with patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=rows)):
            resp = admin_client.get("/api/admin/analytics/driver-offer-trends", params={"driver_id": "d1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_chart"][0]["date"] == "2026-07-01"
        assert data["driver_id"] == "d1"

    def test_rpc_error_returns_503(self, admin_client):
        with patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))):
            resp = admin_client.get("/api/admin/analytics/driver-offer-trends")
        assert resp.status_code == 503


# ── driver-acceptance: pagination / search / sort reachability ────────


def _acc_fixture(n_good: int, n_low: int):
    """n_good drivers at 100% acceptance, n_low at 20% (low performers).

    Mirrors the shape that caused the original bug: the default sort is
    acceptance-rate DESC, so every low performer lands after the good ones.
    """
    drivers, acc, users = [], [], []
    for i in range(n_good):
        did = f"good{i}"
        drivers.append({"id": did, "user_id": f"u{did}", "rating": 4.9, "is_online": True})
        acc.append({"driver_id": did, "total_rides": 10, "completed": 10, "cancelled_by_driver": 0})
        users.append({"id": f"u{did}", "first_name": "Good", "last_name": str(i)})
    for i in range(n_low):
        did = f"low{i}"
        drivers.append({"id": did, "user_id": f"u{did}", "rating": 3.1, "is_online": False})
        acc.append({"driver_id": did, "total_rides": 10, "completed": 2, "cancelled_by_driver": 8})
        users.append({"id": f"u{did}", "first_name": "Low", "last_name": str(i)})
    return drivers, acc, users


class TestDriverAcceptancePagination:
    """Regression cover for the summary/table mismatch.

    `low_performer_count` counted across every driver while the response
    returned only the first `limit` rows of an acceptance-rate DESC sort — so
    the drivers the card counted were exactly the rows the slice dropped.
    """

    def _call(self, admin_client, drivers, acc, users, **params):
        with (
            patch(
                "backend.routes.admin.analytics.db.get_rows",
                AsyncMock(side_effect=[drivers, users]),
            ),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=acc)),
        ):
            return admin_client.get("/api/admin/analytics/driver-acceptance", params=params)

    def test_low_performers_are_off_the_default_page_but_still_counted(self, admin_client):
        """The exact failure mode: counted, but not on page 1."""
        drivers, acc, users = _acc_fixture(n_good=60, n_low=3)
        resp = self._call(admin_client, drivers, acc, users, limit=50)
        assert resp.status_code == 200
        data = resp.json()
        assert data["low_performer_count"] == 3
        assert data["total_drivers"] == 63
        # Page 1 is all high performers — this is why the filter below matters.
        assert all(d["acceptance_rate"] == 100.0 for d in data["drivers"])
        assert data["has_more"] is True

    def test_low_performers_only_filter_reaches_them(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=60, n_low=3)
        resp = self._call(admin_client, drivers, acc, users, limit=50, low_performers_only=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_drivers"] == 3
        assert data["has_more"] is False
        assert {d["driver_id"] for d in data["drivers"]} == {"low0", "low1", "low2"}

    def test_ascending_sort_reaches_them(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=60, n_low=3)
        resp = self._call(admin_client, drivers, acc, users, limit=5, order="asc")
        data = resp.json()
        assert [d["driver_id"] for d in data["drivers"][:3]] == ["low0", "low1", "low2"]

    def test_offset_pages_through_the_full_set(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=4, n_low=2)
        first = self._call(admin_client, drivers, acc, users, limit=4, offset=0).json()
        drivers2, acc2, users2 = _acc_fixture(n_good=4, n_low=2)
        second = self._call(admin_client, drivers2, acc2, users2, limit=4, offset=4).json()
        assert first["has_more"] is True
        assert second["has_more"] is False
        assert second["returned"] == 2
        # No driver appears on both pages, and together they cover everything.
        ids = {d["driver_id"] for d in first["drivers"]} | {d["driver_id"] for d in second["drivers"]}
        assert len(ids) == 6

    def test_summary_covers_full_set_not_just_the_page(self, admin_client):
        """A page-scoped average would drift as you paginate."""
        drivers, acc, users = _acc_fixture(n_good=8, n_low=2)
        page1 = self._call(admin_client, drivers, acc, users, limit=2, offset=0).json()
        drivers2, acc2, users2 = _acc_fixture(n_good=8, n_low=2)
        page5 = self._call(admin_client, drivers2, acc2, users2, limit=2, offset=8).json()
        assert page1["avg_acceptance_rate"] == page5["avg_acceptance_rate"] == 84.0
        assert page1["low_performer_count"] == page5["low_performer_count"] == 2
        assert page1["total_drivers"] == page5["total_drivers"] == 10

    def test_search_filters_by_driver_name(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=3, n_low=1)
        data = self._call(admin_client, drivers, acc, users, search="low").json()
        assert data["total_drivers"] == 1
        assert data["drivers"][0]["driver_id"] == "low0"

    def test_search_is_case_insensitive(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=2, n_low=1)
        data = self._call(admin_client, drivers, acc, users, search="LOW").json()
        assert data["total_drivers"] == 1

    def test_min_rides_filter_excludes_idle_drivers(self, admin_client):
        drivers = [
            {"id": "busy", "user_id": "ub", "rating": 4.0, "is_online": True},
            {"id": "idle", "user_id": "ui", "rating": 4.0, "is_online": True},
        ]
        acc = [{"driver_id": "busy", "total_rides": 9, "completed": 9, "cancelled_by_driver": 0}]
        users = [
            {"id": "ub", "first_name": "Busy", "last_name": "D"},
            {"id": "ui", "first_name": "Idle", "last_name": "D"},
        ]
        data = self._call(admin_client, drivers, acc, users, min_rides=1).json()
        assert data["total_drivers"] == 1
        assert data["drivers"][0]["driver_id"] == "busy"

    def test_scan_truncated_flag_is_false_below_the_cap(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=2, n_low=0)
        assert self._call(admin_client, drivers, acc, users).json()["scan_truncated"] is False

    def test_scan_truncated_flag_set_at_the_cap(self, admin_client):
        from backend.routes.admin.analytics import _DRIVER_SCAN_CAP

        drivers, acc, users = _acc_fixture(n_good=_DRIVER_SCAN_CAP, n_low=0)
        assert self._call(admin_client, drivers, acc, users).json()["scan_truncated"] is True

    def test_threshold_is_reported_so_ui_cannot_drift(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=1, n_low=1)
        data = self._call(admin_client, drivers, acc, users).json()
        assert data["low_performer_threshold"] == {"rate_below": 70.0, "min_rides": 5}

    def test_offset_past_the_end_returns_empty_page_not_an_error(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=2, n_low=0)
        data = self._call(admin_client, drivers, acc, users, offset=500).json()
        assert data["drivers"] == []
        assert data["returned"] == 0
        assert data["has_more"] is False
        assert data["total_drivers"] == 2

    def test_invalid_sort_column_is_rejected(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=1, n_low=0)
        assert self._call(admin_client, drivers, acc, users, sort_by="; DROP TABLE").status_code == 422


# ── service-area scoping + Regina bucketing (migration 350) ───────────


class TestAnalyticsAreaScopeAndTimezone:
    """Cover for migration 350: the overview gained a service-area scope, and
    both aggregates moved from UTC to America/Regina day/hour buckets."""

    def test_overview_forwards_service_area_to_the_rpc(self, admin_client):
        rpc = AsyncMock(return_value=[{"total": 3, "completed": 3}])
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", rpc),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/overview", params={"service_area_id": "saskatoon"})
        assert resp.status_code == 200
        assert rpc.await_args.args[1]["p_service_area_id"] == "saskatoon"
        assert resp.json()["service_area_id"] == "saskatoon"

    def test_overview_passes_null_area_when_unscoped(self, admin_client):
        """'All areas' must reach the RPC as NULL, not the string 'all'."""
        rpc = AsyncMock(return_value=[{"total": 1}])
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", rpc),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            resp = admin_client.get("/api/admin/analytics/overview")
        assert resp.status_code == 200
        assert rpc.await_args.args[1]["p_service_area_id"] is None
        assert resp.json()["service_area_id"] is None

    def test_overview_cache_key_is_per_area(self, admin_client):
        """Two areas must not share one cached payload."""
        seen = []

        async def _get(key):
            seen.append(key)
            return None

        with (
            patch("backend.routes.admin.analytics.redis_get", _get),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{"total": 1}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            admin_client.get("/api/admin/analytics/overview", params={"service_area_id": "a1"})
            admin_client.get("/api/admin/analytics/overview", params={"service_area_id": "a2"})
            admin_client.get("/api/admin/analytics/overview")
        assert len(set(seen)) == 3, f"cache keys collided: {seen}"

    def test_cache_key_version_bumped_off_the_utc_buckets(self, admin_client):
        """Entries cached under UTC bucketing must not be served after the switch."""
        seen = []

        async def _get(key):
            seen.append(key)
            return None

        with (
            patch("backend.routes.admin.analytics.redis_get", _get),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{"total": 1}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            admin_client.get("/api/admin/analytics/overview")
            admin_client.get("/api/admin/analytics/cancellation-reasons")
        assert all(":v2:" in k for k in seen), seen

    def test_both_endpoints_report_the_bucketing_timezone(self, admin_client):
        """The UI renders bare hour labels; without this it cannot say which zone."""
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[{"total": 0}])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            ov = admin_client.get("/api/admin/analytics/overview").json()
            cx = admin_client.get("/api/admin/analytics/cancellation-reasons").json()
        assert ov["timezone"] == "America/Regina"
        assert cx["timezone"] == "America/Regina"


class TestReginaBucketingMigration:
    """Static checks on migration 350 — it is applied by a runner we cannot
    exercise here, and silently dropping migration 349's legacy-import
    exclusion while rewriting these functions would re-skew live KPIs."""

    @staticmethod
    def _sql() -> str:
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "migrations" / "350_analytics_regina_buckets_and_area_scope.sql"
        return p.read_text()

    @staticmethod
    def _body(sql: str) -> str:
        return "\n".join(ln for ln in sql.split("\n") if not ln.lstrip().startswith("--"))

    def test_legacy_import_exclusion_survives_the_rewrite(self):
        assert self._body(self._sql()).count("legacy_import_metadata = '{}'::jsonb") == 2

    def test_no_utc_bucketing_remains(self):
        body = self._body(self._sql())
        assert "AT TIME ZONE 'UTC'" not in body
        assert body.count("America/Regina") >= 3

    def test_both_functions_stay_revoked_from_public_roles(self):
        body = self._body(self._sql())
        assert body.count("REVOKE EXECUTE") == 2
        # Naming only anon/authenticated is a NO-OP: Postgres grants EXECUTE to
        # PUBLIC on CREATE FUNCTION, and both roles inherit it from there.
        # Verified on Postgres 16 — has_function_privilege('anon', ...) stayed
        # true until PUBLIC was revoked.
        assert body.count("FROM PUBLIC, anon, authenticated") == 2
        # service_role does not inherit EXECUTE any other way, and the backend
        # calls these through it.
        assert body.count("GRANT  EXECUTE") == 2

    def test_overview_signature_change_drops_the_old_arity_first(self):
        """CREATE OR REPLACE cannot change a signature — without the DROP this
        would silently leave two overloads and PostgREST could pick either."""
        body = self._body(self._sql())
        assert "DROP FUNCTION IF EXISTS public.admin_analytics_overview(timestamptz);" in body
        assert "p_service_area_id text DEFAULT NULL" in body

    def test_area_predicate_present_on_both_functions(self):
        body = self._body(self._sql())
        assert body.count("p_service_area_id IS NULL OR service_area_id::text = p_service_area_id") == 2


# ── marketplace funnel + supply utilization (migration 351) ───────────


_UNSET = object()  # distinguishes "no payload given" from an empty {} payload


class TestMarketplaceFunnel:
    FUNNEL = {
        "requested": 100,
        "matched": 90,
        "accepted": 80,
        "completed": 72,
        "cancelled": 20,
        "in_flight": 8,
        "no_supply": 6,
        "cancels_by_party": {"rider": 9, "driver": 2, "system": 6, "unknown": 3},
        "cancels_unattributed_fallback": 3,
        "daily": [{"date": "2026-08-19", "requested": 50}],
    }

    def _call(self, admin_client, payload=_UNSET, **params):
        # Sentinel, not `payload or DEFAULT` — an intentionally empty {} payload
        # is falsy and would otherwise silently fall back to the fixture.
        body = self.FUNNEL if payload is _UNSET else payload
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[body])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            return admin_client.get("/api/admin/analytics/marketplace-funnel", params=params)

    def test_stages_and_dropoff(self, admin_client):
        data = self._call(admin_client).json()
        assert [s["count"] for s in data["stages"]] == [100, 90, 80, 72]
        assert data["dropoff"] == {
            "request_to_match": 10,
            "match_to_accept": 10,
            "accept_to_complete": 8,
        }

    def test_rates_are_computed_against_requested(self, admin_client):
        r = self._call(admin_client).json()["rates"]
        assert r["match_rate"] == 90.0
        assert r["fulfilment_rate"] == 72.0
        assert r["rider_cancel_rate"] == 9.0
        assert r["driver_cancel_rate"] == 2.0
        assert r["unmet_demand_rate"] == 6.0

    def test_kpis_carry_claude_md_targets_and_verdicts(self, admin_client):
        kpis = {k["key"]: k for k in self._call(admin_client).json()["kpis"]}
        # match rate 90 >= target 85 -> meeting
        assert kpis["match_rate"]["target"] == 85.0
        assert kpis["match_rate"]["meeting_target"] is True
        # rider cancels 9 > target 8 -> NOT meeting (max-direction)
        assert kpis["rider_cancel_rate"]["direction"] == "max"
        assert kpis["rider_cancel_rate"]["meeting_target"] is False
        # driver cancels 2 <= target 3 -> meeting
        assert kpis["driver_cancel_rate"]["meeting_target"] is True

    def test_unattributed_fallback_count_is_surfaced(self, admin_client):
        """Operators must be able to see how much of the split is string-matched."""
        assert self._call(admin_client).json()["cancels_unattributed_fallback"] == 3

    def test_empty_window_does_not_divide_by_zero(self, admin_client):
        data = self._call(admin_client, payload={}).json()
        assert data["rates"]["match_rate"] == 0.0
        assert data["rates"]["fulfilment_rate"] == 0.0
        assert all(s["count"] == 0 for s in data["stages"])

    def test_service_area_is_forwarded(self, admin_client):
        rpc = AsyncMock(return_value=[self.FUNNEL])
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", rpc),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            admin_client.get("/api/admin/analytics/marketplace-funnel", params={"service_area_id": "regina"})
        assert rpc.await_args.args[1]["p_service_area_id"] == "regina"

    def test_rpc_error_returns_503_not_a_half_valid_payload(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            resp = admin_client.get("/api/admin/analytics/marketplace-funnel")
        assert resp.status_code == 503

    def test_cache_hit_skips_the_rpc(self, admin_client):
        import json

        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=json.dumps({"cached": True}))),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=AssertionError("should not run"))),
        ):
            assert admin_client.get("/api/admin/analytics/marketplace-funnel").json() == {"cached": True}


class TestSupplyUtilization:
    SUPPLY = {
        "idle_seconds": 36000,  # 10h
        "en_route_seconds": 7200,  # 2h
        "on_trip_seconds": 28800,  # 8h
        "online_seconds": 72000,  # 20h
        "utilization_pct": 40.0,
        "engaged_pct": 50.0,
        "active_drivers": 4,
        "daily": [],
    }

    def _call(self, admin_client, payload=_UNSET, **params):
        body = self.SUPPLY if payload is _UNSET else payload
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[body])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            return admin_client.get("/api/admin/analytics/supply-utilization", params=params)

    def test_seconds_convert_to_hours(self, admin_client):
        data = self._call(admin_client).json()
        assert data["online_hours"] == 20.0
        assert data["on_trip_hours"] == 8.0
        assert data["idle_hours"] == 10.0
        assert data["en_route_hours"] == 2.0

    def test_reports_both_utilization_readings(self, admin_client):
        """utilization_pct and engaged_pct must not be conflated."""
        data = self._call(admin_client).json()
        assert data["utilization_pct"] == 40.0
        assert data["engaged_pct"] == 50.0

    def test_utilization_kpi_against_the_55_percent_target(self, admin_client):
        kpi = self._call(admin_client).json()["kpis"][0]
        assert kpi["key"] == "utilization_pct"
        assert kpi["target"] == 55.0
        assert kpi["meeting_target"] is False  # 40 < 55

    def test_avg_online_hours_per_driver(self, admin_client):
        assert self._call(admin_client).json()["avg_online_hours_per_driver"] == 5.0

    def test_no_active_drivers_does_not_divide_by_zero(self, admin_client):
        data = self._call(admin_client, payload={"online_seconds": 0, "active_drivers": 0}).json()
        assert data["avg_online_hours_per_driver"] == 0.0
        assert data["online_hours"] == 0.0

    def test_window_bounds_are_forwarded_to_the_rpc(self, admin_client):
        """The RPC clamps open periods to p_end, so it must receive one."""
        rpc = AsyncMock(return_value=[self.SUPPLY])
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", rpc),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            admin_client.get("/api/admin/analytics/supply-utilization")
        args = rpc.await_args.args[1]
        assert args["p_start"] and args["p_end"]
        assert args["p_end"] > args["p_start"]

    def test_rpc_error_returns_503(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            assert admin_client.get("/api/admin/analytics/supply-utilization").status_code == 503


class TestMarketplaceMigration351:
    """Static checks on migration 351 — no database is available to run it."""

    @staticmethod
    def _body() -> str:
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "migrations" / "351_marketplace_funnel_and_supply_fns.sql"
        return "\n".join(ln for ln in p.read_text().split("\n") if not ln.lstrip().startswith("--"))

    def test_funnel_excludes_legacy_imports(self):
        assert "legacy_import_metadata = '{}'::jsonb" in self._body()

    def test_buckets_are_regina_never_utc(self):
        body = self._body()
        assert "AT TIME ZONE 'UTC'" not in body
        assert body.count("America/Regina") == 2

    def test_both_functions_are_locked_down(self):
        body = self._body()
        assert body.count("REVOKE EXECUTE") == 2
        assert body.count("FROM PUBLIC, anon, authenticated") == 2
        assert body.count("GRANT  EXECUTE") == 2
        assert body.count("SECURITY DEFINER") == 2
        assert body.count("SET search_path = public, pg_catalog") == 2

    def test_ships_the_index_for_its_own_new_query_pattern(self):
        """driver_insurance_periods had no started_at index before this."""
        assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dip_started_at" in self._body()

    def test_periods_are_clamped_to_the_window(self):
        """Without the clamp, a driver online for a month inflates a 7-day window."""
        body = self._body()
        assert "GREATEST(p.started_at, p_start)" in body
        assert "LEAST(COALESCE(p.ended_at, p_end), p_end)" in body

    def test_period_zero_is_excluded_from_online_time(self):
        """Period 0 is app-off — counting it as online would sink utilization."""
        assert "p.period > 0" in self._body()

    def test_cancellation_attribution_prefers_structured_columns(self):
        """Migration 38 added these expressly to stop reason-string parsing."""
        body = self._body()
        assert "cancelled_by IN ('rider', 'driver', 'admin', 'system')" in body
        assert "cancellation_type = 'no_drivers_found'" in body


# ── efficiency + financial (migration 352) ────────────────────────────


class TestEfficiencyMetrics:
    EFF = {
        "matched_sample": 120,
        "time_to_match_p50_secs": 42.0,
        "time_to_match_p95_secs": 118.0,
        "pickup_sample": 110,
        "time_to_pickup_p50_secs": 300.0,
        "time_to_pickup_p95_secs": 720.0,
        "eta_sample": 95,
        "eta_error_p50_secs": -15.0,
        "eta_error_p95_secs": 180.0,
        "eta_on_time_pct": 63.2,
        "deadhead_km": 840.5,
        "paid_km": 4200.0,
        "deadhead_ratio_pct": 20.0,
    }

    def _call(self, admin_client, payload=_UNSET, **params):
        body = self.EFF if payload is _UNSET else payload
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=[body])),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            return admin_client.get("/api/admin/analytics/efficiency", params=params)

    def test_percentiles_ship_with_their_sample_size(self, admin_client):
        """A P95 over 11 rides is not a fleet statistic — the caller must see N."""
        d = self._call(admin_client).json()
        assert d["time_to_match"] == {"p50_secs": 42.0, "p95_secs": 118.0, "sample": 120}
        assert d["pickup_eta_error"]["sample"] == 95

    def test_missing_percentile_stays_null_not_zero(self, admin_client):
        """An empty window must not report a 0-second time-to-match."""
        d = self._call(admin_client, payload={}).json()
        assert d["time_to_match"]["p50_secs"] is None
        assert d["time_to_match"]["sample"] == 0

    def test_negative_eta_error_is_preserved(self, admin_client):
        """Negative = beat the promise; clamping it would hide good performance."""
        assert self._call(admin_client).json()["pickup_eta_error"]["p50_secs"] == -15.0

    def test_deadhead_block(self, admin_client):
        d = self._call(admin_client).json()["deadhead"]
        assert d["unpaid_km"] == 840.5 and d["paid_km"] == 4200.0 and d["ratio_pct"] == 20.0

    def test_rpc_error_returns_503(self, admin_client):
        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            assert admin_client.get("/api/admin/analytics/efficiency").status_code == 503


class TestFinancialMetrics:
    FIN = {
        "completed_rides": 200,
        "gross_bookings": "5000.00",
        "avg_fare": "25.00",
        "tips": "300.50",
        "tax": "550.00",
        "discounts": "120.25",
        "surge_rides": 40,
        "surge_pct": 20.0,
        "avg_surge_multiplier": 1.5,
        "surge_revenue": "410.10",
        "corporate_rides": 50,
        "corporate_bookings": "1500.00",
        "consumer_rides": 150,
        "consumer_bookings": "3500.00",
        "unique_riders": 90,
        "repeat_riders": 36,
        "repeat_rate_pct": 40.0,
        "daily": [],
    }
    SUPPLY = {"online_seconds": 360000}  # 100h

    def _call(self, admin_client, fin=_UNSET, supply=_UNSET, **params):
        fin_body = self.FIN if fin is _UNSET else fin
        sup_body = self.SUPPLY if supply is _UNSET else supply

        async def _rpc(name, args):
            if name == "admin_financial_metrics":
                return [fin_body]
            if name == "admin_supply_utilization":
                if isinstance(sup_body, Exception):
                    raise sup_body
                return [sup_body]
            raise AssertionError(f"unexpected rpc {name}")

        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", _rpc),
            patch("backend.routes.admin.analytics.redis_set", AsyncMock()),
        ):
            return admin_client.get("/api/admin/analytics/financial", params=params)

    def test_money_fields_round_trip_through_decimal(self, admin_client):
        d = self._call(admin_client).json()
        assert d["gross_bookings"] == 5000.00
        assert d["avg_fare"] == 25.00
        assert d["tips"] == 300.50

    def test_gross_bookings_is_not_named_revenue(self, admin_client):
        """Drivers keep 100% of the fare — this must never read as company revenue."""
        d = self._call(admin_client).json()
        assert "gross_bookings" in d
        assert "revenue" not in d
        assert "total_revenue" not in d

    def test_bookings_per_online_hour_combines_the_supply_ledger(self, admin_client):
        # 5000.00 over 100 online hours
        assert self._call(admin_client).json()["bookings_per_online_hour"] == 50.0

    def test_supply_failure_nulls_the_derived_field_but_keeps_the_rest(self, admin_client):
        """Unknown must not render as zero, and must not fail the endpoint."""
        d = self._call(admin_client, supply=RuntimeError("supply down")).json()
        assert d["bookings_per_online_hour"] is None
        assert d["online_hours"] is None
        assert d["gross_bookings"] == 5000.00  # financials still valid

    def test_zero_online_hours_does_not_divide_by_zero(self, admin_client):
        d = self._call(admin_client, supply={"online_seconds": 0}).json()
        assert d["bookings_per_online_hour"] is None

    def test_surge_and_mix_blocks(self, admin_client):
        d = self._call(admin_client).json()
        assert d["surge"]["pct_of_rides"] == 20.0
        assert d["surge"]["avg_multiplier"] == 1.5
        assert d["mix"]["corporate_rides"] == 50
        assert d["mix"]["consumer_bookings"] == 3500.00

    def test_repeat_rate_declares_its_basis(self, admin_client):
        """Within-window repeat share is not a retention cohort; say so."""
        assert self._call(admin_client).json()["riders"]["repeat_rate_basis"] == "within_window"

    def test_empty_window_is_all_zeros_not_an_error(self, admin_client):
        d = self._call(admin_client, fin={}, supply={"online_seconds": 0}).json()
        assert d["gross_bookings"] == 0.0
        assert d["completed_rides"] == 0

    def test_financial_rpc_error_returns_503(self, admin_client):
        async def _rpc(name, args):
            raise RuntimeError("boom")

        with (
            patch("backend.routes.admin.analytics.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.admin.analytics.db.rpc", _rpc),
        ):
            assert admin_client.get("/api/admin/analytics/financial").status_code == 503


class TestMarketplaceMigration352:
    """Static checks on migration 352 — no database is available to run it."""

    @staticmethod
    def _body() -> str:
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "migrations" / "352_efficiency_and_financial_fns.sql"
        return "\n".join(ln for ln in p.read_text().split("\n") if not ln.lstrip().startswith("--"))

    def test_both_functions_exclude_legacy_imports(self):
        assert self._body().count("legacy_import_metadata = '{}'::jsonb") == 2

    def test_both_functions_are_locked_down(self):
        body = self._body()
        assert body.count("REVOKE EXECUTE") == 2
        assert body.count("FROM PUBLIC, anon, authenticated") == 2
        assert body.count("GRANT  EXECUTE") == 2
        assert body.count("SECURITY DEFINER") == 2
        assert body.count("SET search_path = public, pg_catalog") == 2

    def test_no_utc_bucketing(self):
        assert "AT TIME ZONE 'UTC'" not in self._body()

    def test_deadhead_is_a_ratio_of_sums_not_a_mean_of_ratios(self):
        """One short trip with a long approach must not dominate."""
        assert "SUM(pickup_to_driver_km) / SUM(actual_distance_km)" in self._body()

    def test_percentiles_ship_alongside_sample_counts(self):
        body = self._body()
        for k in ("matched_sample", "pickup_sample", "eta_sample"):
            assert k in body

    def test_money_divisions_stay_in_numeric(self):
        """Float division on fare columns would violate the Decimal-only rule."""
        body = self._body()
        assert "::text::numeric" in body
        assert "ROUND(SUM(fare) / COUNT(*), 2)" in body

    def test_eta_error_uses_the_accepted_offers_promise(self):
        body = self._body()
        assert "o.status = 'accepted'" in body
        assert "o.eta_seconds" in body


# ── metric definitions: completion vs acceptance ──────────────────────


class TestDriverMetricNaming:
    """The endpoint reports completed/assigned, which is a COMPLETION rate.

    Calling it "acceptance" mislabels a driver who accepts every offer but
    whose riders cancel. True acceptance lives in the ride_offers ledger.
    """

    def _call(self, admin_client, drivers, acc, users, **params):
        with (
            patch("backend.routes.admin.analytics.db.get_rows", AsyncMock(side_effect=[drivers, users])),
            patch("backend.routes.admin.analytics.db.rpc", AsyncMock(return_value=acc)),
        ):
            return admin_client.get("/api/admin/analytics/driver-acceptance", params=params)

    def test_reports_completion_rate_and_keeps_the_old_alias(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=1, n_low=0)
        d = self._call(admin_client, drivers, acc, users).json()["drivers"][0]
        assert d["completion_rate"] == 100.0
        assert d["acceptance_rate"] == d["completion_rate"], "alias must not diverge"

    def test_payload_declares_which_metric_it_is(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=1, n_low=0)
        data = self._call(admin_client, drivers, acc, users).json()
        assert data["metric"] == "completion_rate"
        assert data["true_acceptance_source"].endswith("/driver-offer-stats")

    def test_active_average_excludes_idle_drivers(self, admin_client):
        """The old average counted zero-ride drivers at 0%, sinking the figure."""
        drivers = [
            {"id": "busy", "user_id": "ub", "rating": 4.8, "is_online": True},
            *[{"id": f"idle{i}", "user_id": f"ui{i}", "rating": 4.0, "is_online": False} for i in range(9)],
        ]
        acc = [{"driver_id": "busy", "total_rides": 10, "completed": 9, "cancelled_by_driver": 1}]
        users = [{"id": "ub", "first_name": "Busy", "last_name": "D"}] + [
            {"id": f"ui{i}", "first_name": "Idle", "last_name": str(i)} for i in range(9)
        ]
        data = self._call(admin_client, drivers, acc, users).json()
        # One driver at 90%, nine idle at 0%.
        assert data["avg_completion_rate_active"] == 90.0
        assert data["avg_completion_rate_all"] == 9.0
        assert data["avg_acceptance_rate"] == 9.0, "deprecated alias keeps the old all-driver meaning"

    def test_drivers_with_rides_is_distinct_from_total(self, admin_client):
        """'Total Active Drivers' was never active drivers — both are reported now."""
        drivers = [
            {"id": "busy", "user_id": "ub", "is_online": True},
            {"id": "idle", "user_id": "ui", "is_online": False},
        ]
        acc = [{"driver_id": "busy", "total_rides": 3, "completed": 3, "cancelled_by_driver": 0}]
        users = [
            {"id": "ub", "first_name": "B", "last_name": "D"},
            {"id": "ui", "first_name": "I", "last_name": "D"},
        ]
        data = self._call(admin_client, drivers, acc, users).json()
        assert data["total_drivers"] == 2
        assert data["drivers_with_rides"] == 1

    def test_deprecated_sort_key_still_sorts(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=2, n_low=2)
        data = self._call(admin_client, drivers, acc, users, sort_by="acceptance_rate", order="asc").json()
        assert data["drivers"][0]["completion_rate"] == 20.0

    def test_new_sort_key_is_the_default(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=2, n_low=2)
        data = self._call(admin_client, drivers, acc, users).json()
        assert data["sort_by"] == "completion_rate"
        assert data["drivers"][0]["completion_rate"] == 100.0

    def test_low_performer_rule_reads_the_new_key(self, admin_client):
        drivers, acc, users = _acc_fixture(n_good=1, n_low=2)
        assert self._call(admin_client, drivers, acc, users).json()["low_performer_count"] == 2


# ── migration 353: SECURITY DEFINER lockdown sweep ────────────────────


class TestSecurityDefinerLockdownMigration:
    """Migration 353 codifies a production hotfix.

    18 SECURITY DEFINER functions in `public` were `anon`-executable in
    production because `REVOKE ... FROM anon, authenticated` is a no-op —
    Postgres grants EXECUTE to PUBLIC on CREATE FUNCTION. The grants were
    corrected directly against the live database, so this migration is a
    no-op there; it exists so an environment rebuilt from migrations does
    not come back vulnerable.
    """

    @staticmethod
    def _body() -> str:
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "migrations" / "353_revoke_public_execute_on_security_definer_fns.sql"
        return "\n".join(ln for ln in p.read_text().split("\n") if not ln.lstrip().startswith("--"))

    def test_revokes_from_public_not_just_the_named_roles(self):
        """Naming only anon/authenticated is precisely the bug being fixed."""
        assert "FROM PUBLIC, anon, authenticated" in self._body()

    def test_grants_service_role_in_the_same_loop_as_the_revoke(self):
        """A revoke without this strands the backend, which is service_role."""
        body = self._body()
        assert "GRANT EXECUTE ON FUNCTION %s TO service_role" in body
        revoke_at = body.index("REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC")
        grant_at = body.index("GRANT EXECUTE ON FUNCTION %s TO service_role")
        assert grant_at > revoke_at, "grant must follow the revoke inside the loop body"

    def test_scoped_to_security_definer_functions_only(self):
        """A blanket sweep over every function would be far wider than the defect."""
        body = self._body()
        assert "p.prosecdef" in body
        assert "n.nspname = 'public'" in body

    def test_sweeps_rather_than_hardcoding_a_name_list(self):
        """The defect is a copied pattern, so a fixed list would go stale."""
        body = self._body()
        assert "FOR f IN" in body
        assert "wallet_pay_for_ride" not in body, "should not hardcode signatures in the executable body"

    def test_names_each_function_it_touches(self):
        """Silent bulk privilege changes are not auditable."""
        assert "RAISE NOTICE 'migration 353: locked down %'" in self._body()

    def test_reports_a_clean_no_op(self):
        """Expected outcome against production, which is already corrected."""
        assert "no-op" in self._body()

    def test_has_a_failing_post_condition(self):
        """A half-applied lockdown must not report success."""
        body = self._body()
        assert "RAISE EXCEPTION" in body
        assert "post-condition failed" in body

    def test_checks_both_anon_and_authenticated(self):
        body = self._body()
        assert body.count("has_function_privilege('anon'") >= 2
        assert body.count("has_function_privilege('authenticated'") >= 2
