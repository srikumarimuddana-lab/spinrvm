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
