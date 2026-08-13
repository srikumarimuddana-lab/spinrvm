"""Tests for routes/admin/compliance.py — the Compliance & Tax Reporting
module's GST/PST remittance and per-trip insurance-billing endpoints.

Mirrors the direct-patch style used by test_admin_users_search.py rather
than the full mock_supabase_client fixture, since these tests exercise the
route module's own aggregation logic, not the Supabase query builder.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes.admin import compliance
except ImportError:  # pragma: no cover
    from routes.admin import compliance  # type: ignore

pytestmark = pytest.mark.unit

_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
_END = datetime(2026, 7, 31, tzinfo=timezone.utc)


def _patch_get_rows(side_effect):
    return patch("backend.routes.admin.compliance.db_supabase.get_rows", AsyncMock(side_effect=side_effect))


class TestGstPstRows:
    def test_sums_gst_and_pst_separately(self):
        async def get_rows_side(table, filters=None, **kw):
            assert table == "rides"
            return [
                {
                    "id": "r1",
                    "ride_completed_at": "2026-07-05T10:00:00Z",
                    "tax_breakdown": {"GST": {"amount": 5.0, "rate": 5}, "PST": {"amount": 6.0, "rate": 6}},
                }
            ]

        with _patch_get_rows(get_rows_side):
            rows, gst_total, pst_total, hst_total, truncated = asyncio.run(compliance._gst_pst_rows(_START, _END))

        assert gst_total == Decimal("5.0")
        assert pst_total == Decimal("6.0")
        assert hst_total == Decimal("0")
        assert not truncated
        assert rows == [
            {
                "month": "2026-07",
                "gst": "5.00",
                "pst": "6.00",
                "hst": "0.00",
                "unrecognized_tax": "0.00",
                "total_tax": "11.00",
            }
        ]

    def test_hst_is_tracked_not_dropped_into_other(self):
        """Regression: an earlier draft matched labels via substring
        ('GST' in label), which silently folded HST-labeled amounts (a real
        production label for hst_enabled service areas — features.py's
        _apply_tax) into an 'other' bucket excluded from any named total.
        HST must land in its own recognized bucket."""

        async def get_rows_side(table, filters=None, **kw):
            return [
                {
                    "id": "r1",
                    "ride_completed_at": "2026-07-05T10:00:00Z",
                    "tax_breakdown": {"HST": {"amount": 13.0, "rate": 13}},
                }
            ]

        with _patch_get_rows(get_rows_side):
            rows, gst_total, pst_total, hst_total, truncated = asyncio.run(compliance._gst_pst_rows(_START, _END))

        assert hst_total == Decimal("13.0")
        assert gst_total == Decimal("0")
        assert pst_total == Decimal("0")
        assert rows[0]["hst"] == "13.00"
        assert rows[0]["unrecognized_tax"] == "0.00"

    def test_unrecognized_label_is_isolated_not_dropped(self):
        async def get_rows_side(table, filters=None, **kw):
            return [
                {
                    "id": "r1",
                    "ride_completed_at": "2026-07-05T10:00:00Z",
                    "tax_breakdown": {"MysteryFee": {"amount": 2.0, "rate": 0}},
                }
            ]

        with _patch_get_rows(get_rows_side):
            rows, gst_total, pst_total, hst_total, truncated = asyncio.run(compliance._gst_pst_rows(_START, _END))

        # Not counted toward any named tax total (would misstate a filing)...
        assert gst_total == Decimal("0")
        assert pst_total == Decimal("0")
        assert hst_total == Decimal("0")
        # ...but not silently lost either — visible in its own column.
        assert rows[0]["unrecognized_tax"] == "2.00"
        assert rows[0]["total_tax"] == "2.00"

    def test_groups_by_calendar_month(self):
        async def get_rows_side(table, filters=None, **kw):
            return [
                {"id": "r1", "ride_completed_at": "2026-07-05T10:00:00Z", "tax_breakdown": {"GST": {"amount": 1.0}}},
                {"id": "r2", "ride_completed_at": "2026-07-20T10:00:00Z", "tax_breakdown": {"GST": {"amount": 2.0}}},
                {"id": "r3", "ride_completed_at": "2026-08-01T10:00:00Z", "tax_breakdown": {"GST": {"amount": 3.0}}},
            ]

        with _patch_get_rows(get_rows_side):
            rows, *_rest = asyncio.run(compliance._gst_pst_rows(_START, _END))

        by_month = {r["month"]: r["gst"] for r in rows}
        assert by_month == {"2026-07": "3.00", "2026-08": "3.00"}

    def test_truncation_flag_set_at_row_limit(self):
        fake_rides = [
            {"id": f"r{i}", "ride_completed_at": "2026-07-05T10:00:00Z", "tax_breakdown": {}}
            for i in range(compliance._ROW_LIMIT)
        ]

        async def get_rows_side(table, filters=None, **kw):
            return fake_rides

        with _patch_get_rows(get_rows_side):
            *_rest, truncated = asyncio.run(compliance._gst_pst_rows(_START, _END))

        assert truncated is True

    def test_no_float_leaks_into_arithmetic(self):
        """_d() must intercept the float amounts Supabase returns from the
        tax_breakdown JSONB column before any '+=' — CLAUDE.md's Decimal-
        only rule."""

        async def get_rows_side(table, filters=None, **kw):
            return [
                {
                    "id": "r1",
                    "ride_completed_at": "2026-07-05T10:00:00Z",
                    "tax_breakdown": {"GST": {"amount": 0.1, "rate": 5}},
                }
            ] * 3  # 0.1 + 0.1 + 0.1 must equal exactly 0.3 under Decimal, not 0.30000000000000004

        with _patch_get_rows(get_rows_side):
            _rows, gst_total, *_rest = asyncio.run(compliance._gst_pst_rows(_START, _END))

        assert gst_total == Decimal("0.3")
        assert isinstance(gst_total, Decimal)


class TestInsuranceBillingDetailRows:
    """_insurance_billing_detail_rows backs both the SGI and Knight Archer
    billing endpoints — reads driver_period_distances (GPS-measured
    per-period distance), not rides.distance_km, so each phase shows its
    own real leg distance rather than a de-duplicated ride total."""

    def test_joins_driver_name_and_labels_phase(self):
        """Regression: the drivers table's real column is `name` (verified
        against real staging schema — `full_name` does not exist), not
        first_name/last_name concatenation alone."""

        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_period_distances":
                return [
                    {
                        "driver_id": "d1",
                        "ride_id": "r1",
                        "period": 2,
                        "distance_km": 1.5,
                        "started_at": "2026-07-01T09:00:00Z",
                    }
                ]
            if table == "drivers":
                return [{"id": "d1", "name": "Jane Doe", "first_name": "Jane", "last_name": "Doe"}]
            return []

        with _patch_get_rows(get_rows_side):
            rows, grand_total_km, truncated, _groups = asyncio.run(
                compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"))
            )

        assert not truncated
        assert grand_total_km == Decimal("1.5")
        # driver_id/ride_id are intentionally excluded from the rendered
        # report (same product decision as the retired insurance-period-
        # audit report) -- driver_name + trip_date already identify the row.
        assert rows == [
            {
                "driver_name": "Jane Doe",
                "license_number": "",
                "vehicle": "",
                "trip_date": "2026-07-01 09:00 UTC",
                "phase": "2 — En route to pickup (primary commercial)",
                "phase_km": "1.500",
                "rate_per_km": "$0.110",
                "amount": "$0.16",
            }
        ]

    def test_falls_back_to_first_last_name_when_name_is_null(self):
        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_period_distances":
                return [
                    {
                        "driver_id": "d1",
                        "ride_id": "r1",
                        "period": 3,
                        "distance_km": 5.0,
                        "started_at": "2026-07-01T09:10:00Z",
                    }
                ]
            if table == "drivers":
                return [{"id": "d1", "name": None, "first_name": "Jane", "last_name": "Doe"}]
            return []

        with _patch_get_rows(get_rows_side):
            rows, *_rest = asyncio.run(compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.011")))

        assert rows[0]["driver_name"] == "Jane Doe"

    def test_each_phase_kept_as_its_own_row_not_summed(self):
        """Regression vs. the retired aggregate report: a ride with both a
        Period 2 leg and a Period 3 leg must produce TWO rows with their
        own distances, not one de-duplicated/summed row."""

        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_period_distances":
                return [
                    {
                        "driver_id": "d1",
                        "ride_id": "r1",
                        "period": 2,
                        "distance_km": 1.5,
                        "started_at": "2026-07-01T09:00:00Z",
                    },
                    {
                        "driver_id": "d1",
                        "ride_id": "r1",
                        "period": 3,
                        "distance_km": 12.5,
                        "started_at": "2026-07-01T09:10:00Z",
                    },
                ]
            if table == "drivers":
                return [{"id": "d1", "name": "Jane Doe"}]
            return []

        with _patch_get_rows(get_rows_side):
            rows, grand_total_km, _truncated, groups = asyncio.run(
                compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"))
            )

        assert len(rows) == 2
        assert grand_total_km == Decimal("14.0")
        assert {r["phase_km"] for r in rows} == {"1.500", "12.500"}

        # Both phase rows share one ride_id -> one group, with a parent
        # ("All phases") row summing both legs' km/amount, and both phase
        # rows as its children -- this is what backs the xlsx collapsible
        # view (report_branding.write_branded_grouped_table).
        assert len(groups) == 1
        parent, children = groups[0]
        assert parent["phase"] == "All phases"
        assert parent["phase_km"] == "14.000"
        assert parent["amount"] == f"${(Decimal('14.0') * Decimal('0.11')).quantize(Decimal('0.01'))}"
        assert len(children) == 2
        assert children == [r for r in rows]

    def test_empty_result_returns_empty_list_not_error(self):
        async def get_rows_side(table, filters=None, **kw):
            return []

        with _patch_get_rows(get_rows_side):
            rows, grand_total_km, truncated, groups = asyncio.run(
                compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"))
            )

        assert rows == []
        assert grand_total_km == Decimal("0")
        assert truncated is False
        assert groups == []

    def test_unknown_driver_falls_back_to_id(self):
        """A distance row whose driver was hard-deleted (or the drivers
        batch fetch missed it) must still show up in the billing export —
        never silently dropped — using the raw driver_id as a fallback."""

        async def get_rows_side(table, filters=None, **kw):
            if table == "driver_period_distances":
                return [
                    {
                        "driver_id": "d-missing",
                        "ride_id": "r1",
                        "period": 2,
                        "distance_km": 1.0,
                        "started_at": "2026-07-01T09:00:00Z",
                    }
                ]
            return []  # drivers lookup returns nothing

        with _patch_get_rows(get_rows_side):
            rows, *_rest = asyncio.run(compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11")))

        assert rows[0]["driver_name"] == "d-missing"


class TestLogComplianceExport:
    def test_writes_audit_row(self):
        captured = {}

        async def insert_one_side(table, doc):
            captured["table"] = table
            captured["doc"] = doc
            return "audit-id-1"

        with patch("backend.routes.admin.compliance.db_supabase.insert_one", AsyncMock(side_effect=insert_one_side)):
            asyncio.run(
                compliance._log_compliance_export({"id": "admin1"}, "gst_pst_remittance", {"date_range": "30d"}, 3)
            )

        assert captured["table"] == "compliance_export_events"
        assert captured["doc"]["admin_user_id"] == "admin1"
        assert captured["doc"]["report_type"] == "gst_pst_remittance"
        assert captured["doc"]["row_count"] == 3

    def test_failure_does_not_raise(self):
        """A failed audit-log write must not block the admin from getting
        their report (CLAUDE.md: audit logging is best-effort, but the
        failure itself must still surface loudly via logger.error — not
        tested here directly, covered by code review)."""

        async def insert_one_side(table, doc):
            raise RuntimeError("db unavailable")

        with patch("backend.routes.admin.compliance.db_supabase.insert_one", AsyncMock(side_effect=insert_one_side)):
            # Must not raise.
            asyncio.run(compliance._log_compliance_export({"id": "admin1"}, "gst_pst_remittance", {}, 0))


class TestParseServiceAreaIds:
    """The Compliance page's Service Area multi-select arrives as a
    comma-separated string; None means "every area", which must stay
    byte-identical to the pre-filter behaviour of every report."""

    @pytest.mark.parametrize("raw", [None, "", "   ", ",", " , , "])
    def test_blank_means_no_filter(self, raw):
        assert compliance._parse_service_area_ids(raw) is None

    def test_strips_whitespace_and_drops_empties(self):
        assert compliance._parse_service_area_ids(" a , ,b ") == ["a", "b"]

    def test_dedupes_and_sorts(self):
        """Order-independence is load-bearing, not cosmetic: this list is
        part of the dual-approval gate's params key, so {b,a} must match an
        approval already granted for {a,b} rather than demanding a second
        one for the same export."""
        assert compliance._parse_service_area_ids("b,a,b") == ["a", "b"]


class TestServiceAreaScopeLabel:
    def test_unfiltered_states_all_areas_rather_than_staying_silent(self):
        assert asyncio.run(compliance._service_area_scope_label(None)) == "All service areas"

    def test_resolves_names_in_requested_order(self):
        async def get_rows_side(table, filters=None, **kw):
            assert table == "service_areas"
            return [{"id": "a2", "name": "Regina"}, {"id": "a1", "name": "Saskatoon"}]

        with _patch_get_rows(get_rows_side):
            label = asyncio.run(compliance._service_area_scope_label(["a1", "a2"]))

        assert label == "Service areas: Saskatoon, Regina"

    def test_falls_back_to_ids_when_name_lookup_fails(self):
        """The rows are already correctly filtered by id at this point, so a
        failed *name* lookup must not fail the export — but the scope still
        has to appear on the document rather than silently vanishing."""

        async def get_rows_side(table, filters=None, **kw):
            raise RuntimeError("db unavailable")

        with _patch_get_rows(get_rows_side):
            label = asyncio.run(compliance._service_area_scope_label(["a1", "a2"]))

        assert label == "Service areas: a1, a2"


class TestServiceAreaFiltering:
    """Every report except T4A accepts a service-area scope. Ride-based
    reports filter the ride's own area; driver-based reports filter the
    driver's home area (the convention migrations 164/165/194 use)."""

    def test_gst_pst_filters_rides_by_the_rides_own_area(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured[table] = filters
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._gst_pst_rows(_START, _END, ["a1", "a2"]))

        assert captured["rides"]["service_area_id"] == {"$in": ["a1", "a2"]}

    def test_gst_pst_omits_the_filter_entirely_when_unscoped(self):
        """Not "filter by every id" — the key must be absent, so an
        unfiltered export issues exactly the query it issued before this
        feature existed."""
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured[table] = filters
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._gst_pst_rows(_START, _END))

        assert "service_area_id" not in captured["rides"]

    def test_airport_trips_filters_rides_by_the_rides_own_area(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured[table] = filters
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._airport_trips_rows(_START, _END, ["a1"]))

        assert captured["rides"]["service_area_id"] == {"$in": ["a1"]}

    def test_driver_roster_filters_drivers_by_home_area(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured[table] = filters
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._driver_roster_rows(None, service_area_ids=["a1"]))

        assert captured["drivers"]["service_area_id"] == {"$in": ["a1"]}

    def test_insurance_billing_scopes_distances_to_drivers_in_those_areas(self):
        """driver_period_distances has no service area of its own, so the
        driver ids are resolved first and passed as $in — PostgREST cannot
        filter a child table by an embedded parent."""
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured.setdefault(table, []).append(filters)
            if table == "drivers" and "service_area_id" in (filters or {}):
                return [{"id": "d1"}, {"id": "d2"}]
            if table == "driver_period_distances":
                return []
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"), ["a1"]))

        assert captured["drivers"][0]["service_area_id"] == {"$in": ["a1"]}
        assert captured["driver_period_distances"][0]["driver_id"] == {"$in": ["d1", "d2"]}

    def test_insurance_billing_returns_empty_when_no_driver_is_in_the_selected_areas(self):
        """The load-bearing guard: an empty `$in` list would widen back to
        every driver, so falling through here would bill the insurer for
        the very areas the admin excluded. Must return empty AND must never
        reach the distances query at all."""
        tables_queried = []

        async def get_rows_side(table, filters=None, **kw):
            tables_queried.append(table)
            return []  # no drivers in the selected areas

        with _patch_get_rows(get_rows_side):
            rows, total_km, truncated, groups = asyncio.run(
                compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"), ["a-empty"])
            )

        assert rows == []
        assert total_km == Decimal("0")
        assert groups == []
        assert "driver_period_distances" not in tables_queried

    def test_insurance_billing_unscoped_does_not_resolve_drivers_by_area(self):
        captured = {}

        async def get_rows_side(table, filters=None, **kw):
            captured.setdefault(table, []).append(filters)
            return []

        with _patch_get_rows(get_rows_side):
            asyncio.run(compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11")))

        assert "driver_id" not in captured["driver_period_distances"][0]

    def test_area_driver_scope_truncation_propagates_to_the_report(self):
        """Hitting the driver cap means the driver set is short, so the
        billing total under-reports. That must surface as the report's own
        truncation marker, not be silently absorbed."""

        async def get_rows_side(table, filters=None, **kw):
            if table == "drivers" and "service_area_id" in (filters or {}):
                return [{"id": f"d{i}"} for i in range(compliance._ROW_LIMIT)]
            return []

        with _patch_get_rows(get_rows_side):
            _rows, _km, truncated, _groups = asyncio.run(
                compliance._insurance_billing_detail_rows(_START, _END, Decimal("0.11"), ["a1"])
            )

        assert truncated is True
