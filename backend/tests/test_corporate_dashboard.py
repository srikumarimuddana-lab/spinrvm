"""Role-scoped dashboard tests (Phase 6 / M6.1)."""

from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u1", "phone": "+15550001111"}
_ROUTE = "routes.corporate_company_dashboard"


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _guard(role="admin", member_id="m1"):
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": role, "id": member_id}]),
    )


def _ride(id_, status, created, total="10.00", member="m1", booked_by=None):
    return {
        "id": id_,
        "corporate_account_id": "c1",
        "status": status,
        "created_at": created,
        "grand_total": total,
        "corporate_member_id": member,
        "corporate_booked_by_member_id": booked_by or member,
    }


_NOW = "2099-12-31T12:00:00+00:00"  # always inside every bucket
_OLD = "2000-01-01T12:00:00+00:00"  # overall only


def test_admin_stats_buckets_and_groups(test_client, rider_override):
    rides = [
        _ride("r1", "completed", _NOW, "25.00"),
        _ride("r2", "searching", _NOW),
        _ride("r3", "in_progress", _NOW),
        _ride("r4", "scheduled", _NOW),
        _ride("r5", "completed", _OLD, "75.00"),
    ]

    async def fake_rows(table, filters, **kw):
        if table == "rides" and kw.get("offset", 0) == 0:
            return rides
        return []

    with (
        _guard("admin"),
        patch(
            f"{_ROUTE}.db_supabase.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "timezone": "America/Regina"}),
        ),
        patch(f"{_ROUTE}.db_supabase.get_rows", AsyncMock(side_effect=fake_rows)),
    ):
        resp = test_client.get("/company/c1/dashboard/stats")
    assert resp.status_code == 200, resp.text
    st = resp.json()["stats"]
    assert st["total_bookings"]["overall"] == 5
    assert st["total_bookings"]["today"] == 4  # r5 is old
    assert st["pending_bookings"]["overall"] == 2  # Q15: scheduled + searching
    assert st["active_rides"]["overall"] == 1
    assert st["completed_rides"]["overall"] == 2
    assert st["expenses"]["overall"] == "100.00"  # 25 + 75, Decimal string
    assert st["expenses"]["today"] == "25.00"


def test_member_scope_merges_booked_by_and_for(test_client, rider_override):
    calls = []

    async def fake_rows(table, filters, **kw):
        if table == "corporate_sections":
            return []  # not a head
        if table == "rides":
            calls.append(dict(filters))
            if kw.get("offset", 0) > 0:
                return []
            if "corporate_member_id" in filters:
                return [_ride("r-for", "completed", _NOW, member="m1")]
            return [_ride("r-by", "searching", _NOW, member="m-other", booked_by="m1")]
        return []

    with (
        _guard("member", "m1"),
        patch(
            f"{_ROUTE}.db_supabase.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "timezone": "America/Regina"}),
        ),
        patch(f"{_ROUTE}.db_supabase.get_rows", AsyncMock(side_effect=fake_rows)),
    ):
        resp = test_client.get("/company/c1/dashboard/stats")
    assert resp.status_code == 200, resp.text
    st = resp.json()["stats"]
    assert st["total_bookings"]["overall"] == 2  # Q16: by ∪ for
    filter_keys = {k for f in calls for k in f if k.startswith("corporate_")}
    assert "corporate_member_id" in filter_keys
    assert "corporate_booked_by_member_id" in filter_keys


def test_detail_scoped_404_for_foreign_member(test_client, rider_override):
    ride = _ride("r9", "completed", _NOW, member="m-else", booked_by="m-else")

    async def fake_rows(table, filters, **kw):
        return []  # not a head; no legs

    with (
        _guard("member", "m1"),
        patch(f"{_ROUTE}.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(f"{_ROUTE}.db_supabase.get_rows", AsyncMock(side_effect=fake_rows)),
    ):
        resp = test_client.get("/company/c1/bookings/r9")
    assert resp.status_code == 404


def test_detail_returns_timeline_fare_and_legs(test_client, rider_override):
    ride = {
        **_ride("r1", "completed", _NOW, "45.50"),
        "ride_requested_at": "2099-12-31T11:00:00+00:00",
        "ride_completed_at": "2099-12-31T11:30:00+00:00",
        "base_fare": 5.0,
        "tax_amount": 2.17,
        "tax_breakdown": {"gst": 2.17},
        "driver_id": "d1",
        "payment_status": "paid",
    }

    async def fake_rows(table, filters, **kw):
        if table == "ride_payment_sources":
            return [
                {
                    "allowance_debit_amount": 20,
                    "section_debit_amount": 15.5,
                    "section_id": "s1",
                    "master_fallback_amount": 10,
                }
            ]
        if table == "drivers":
            return [
                {"first_name": "Alex", "vehicle_make": "Toyota", "vehicle_model": "Camry", "license_plate": "ABC123"}
            ]
        return []

    with (
        _guard("admin"),
        patch(f"{_ROUTE}.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(f"{_ROUTE}.db_supabase.get_rows", AsyncMock(side_effect=fake_rows)),
    ):
        resp = test_client.get("/company/c1/bookings/r1")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [e["event"] for e in data["timeline"]] == ["requested", "completed"]
    assert data["fare"]["tax_breakdown"] == {"gst": 2.17}  # GST line item
    assert data["payment_source"]["section"] == 15.5  # frozen leg attribution
    assert data["driver"]["license_plate"] == "ABC123"
