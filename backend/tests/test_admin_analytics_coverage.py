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
