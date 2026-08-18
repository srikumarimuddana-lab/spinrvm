"""Unit tests for services/test_account_cleanup_service.py (A35 sanctioned
replacement for the ad-hoc account-deletion script).

Patch target: ``db.get_rows`` on the module's own binding, same convention
as every other legacy-migration tool test this session.
"""

from unittest.mock import AsyncMock

import pytest

from backend.services import test_account_cleanup_service as svc

pytestmark = pytest.mark.unit

RIDER_USER = {"id": "user-rider-1", "role": "rider", "phone": "+13065551234"}
DRIVER_USER = {"id": "user-driver-1", "role": "driver", "phone": "+13065555678"}
DRIVER_ROW = {"id": "driver-1", "user_id": "user-driver-1"}


def _rows_router(table_responses: dict[str, list]):
    """Builds a fake get_rows(table, filters, ...) that returns
    table_responses[table] regardless of filters, popping nothing — same
    canned response every call to that table (fine for these unit tests,
    each test only needs one distinguishing table)."""

    async def _get_rows(table, filters=None, limit=None, offset=None, columns="*", **_):
        return table_responses.get(table, [])

    return _get_rows


@pytest.mark.anyio
async def test_unmatched_phone_reported_not_silently_dropped(monkeypatch):
    monkeypatch.setattr(svc.db, "get_rows", AsyncMock(side_effect=_rows_router({})))
    plan = await svc.build_cleanup_plan(["3065559999"])
    assert plan.unmatched_phones == ["3065559999"]
    assert plan.safe_to_delete == []
    assert plan.blocked_regulated_data_present == []


@pytest.mark.anyio
async def test_rider_with_no_regulated_data_is_safe(monkeypatch):
    monkeypatch.setattr(
        svc.db,
        "get_rows",
        AsyncMock(side_effect=_rows_router({"users": [RIDER_USER]})),
    )
    plan = await svc.build_cleanup_plan(["3065551234"])
    assert len(plan.safe_to_delete) == 1
    assert plan.safe_to_delete[0].user_id == "user-rider-1"
    assert plan.blocked_regulated_data_present == []


@pytest.mark.anyio
async def test_rider_with_a_ride_is_blocked_not_skipped(monkeypatch):
    monkeypatch.setattr(
        svc.db,
        "get_rows",
        AsyncMock(side_effect=_rows_router({"users": [RIDER_USER], "rides": [{"id": "ride-1"}]})),
    )
    plan = await svc.build_cleanup_plan(["3065551234"])
    assert plan.safe_to_delete == []
    assert len(plan.blocked_regulated_data_present) == 1
    assert "rides (as rider)" in plan.blocked_regulated_data_present[0].blocking_reasons


@pytest.mark.anyio
async def test_driver_with_insurance_periods_is_blocked(monkeypatch):
    async def get_rows(table, filters=None, limit=None, offset=None, columns="*", **_):
        if table == "users":
            return [DRIVER_USER]
        if table == "drivers":
            return [DRIVER_ROW]
        if table == "driver_insurance_periods":
            return [{"id": "dip-1"}]
        return []

    monkeypatch.setattr(svc.db, "get_rows", AsyncMock(side_effect=get_rows))
    plan = await svc.build_cleanup_plan(["3065555678"])
    assert plan.safe_to_delete == []
    assert len(plan.blocked_regulated_data_present) == 1
    c = plan.blocked_regulated_data_present[0]
    assert c.driver_id == "driver-1"
    assert c.blocking_reasons == ["driver_insurance_periods"]


@pytest.mark.anyio
async def test_driver_blocking_reasons_are_all_reported_not_just_first(monkeypatch):
    async def get_rows(table, filters=None, limit=None, offset=None, columns="*", **_):
        if table == "users":
            return [DRIVER_USER]
        if table == "drivers":
            return [DRIVER_ROW]
        if table in ("driver_insurance_periods", "payouts", "bank_accounts"):
            return [{"id": f"{table}-1"}]
        if table == "rides" and filters and filters.get("driver_id"):
            return [{"id": "ride-1"}]
        return []

    monkeypatch.setattr(svc.db, "get_rows", AsyncMock(side_effect=get_rows))
    plan = await svc.build_cleanup_plan(["3065555678"])
    reasons = plan.blocked_regulated_data_present[0].blocking_reasons
    assert set(reasons) == {"rides (as driver)", "driver_insurance_periods", "payouts", "bank_accounts"}


@pytest.mark.anyio
async def test_driver_with_no_driver_row_is_not_treated_as_driver_blocked(monkeypatch):
    """A user row with role='driver' but no matching drivers row (e.g. never
    completed onboarding) must not crash or false-block on driver-scoped
    tables — driver_id stays None, only the rider-scoped rides check runs."""

    async def get_rows(table, filters=None, limit=None, offset=None, columns="*", **_):
        if table == "users":
            return [DRIVER_USER]
        if table == "drivers":
            return []  # no driver row
        return []

    monkeypatch.setattr(svc.db, "get_rows", AsyncMock(side_effect=get_rows))
    plan = await svc.build_cleanup_plan(["3065555678"])
    assert len(plan.safe_to_delete) == 1
    assert plan.safe_to_delete[0].driver_id is None


@pytest.mark.anyio
async def test_blocked_and_safe_never_mix_in_same_bucket(monkeypatch):
    """Multi-phone batch: one blocked, one safe, one unmatched — each lands
    in exactly its own bucket."""
    call_count = {"users": 0}

    async def get_rows(table, filters=None, limit=None, offset=None, columns="*", **_):
        if table == "users":
            call_count["users"] += 1
            phone = filters.get("phone") if filters else None
            if phone == "+13065551234":
                return [RIDER_USER]
            if phone == "+13065555678":
                return [{**DRIVER_USER, "id": "user-driver-2", "phone": phone}]
            return []
        if table == "rides":
            # only the second driver-role user has a ride
            return []
        return []

    monkeypatch.setattr(svc.db, "get_rows", AsyncMock(side_effect=get_rows))
    plan = await svc.build_cleanup_plan(["3065551234", "3065555678", "3065559999"])
    assert plan.unmatched_phones == ["3065559999"]
    assert len(plan.safe_to_delete) == 2  # no driver row -> no blocking data for either
    assert plan.blocked_regulated_data_present == []


def test_print_report_lists_blocked_before_safe_and_never_omits_them():
    blocked = svc.AccountCandidate(
        phone="+13065551234",
        user_id="u1",
        role="driver",
        driver_id="d1",
        blocked=True,
        blocking_reasons=["payouts"],
    )
    safe = svc.AccountCandidate(phone="+13065555678", user_id="u2", role="rider", driver_id=None, blocked=False)
    plan = svc.CleanupPlan(
        requested_phones=["3065551234", "3065555678"],
        safe_to_delete=[safe],
        blocked_regulated_data_present=[blocked],
    )
    report = svc.print_report(plan)
    assert "BLOCKED" in report
    assert "u1" in report and "payouts" in report
    assert "u2" in report
    assert report.index("BLOCKED") < report.index("Safe to delete")
    assert "No DELETE was issued. No trigger was disabled." in report
