"""Unit tests for utils.vehicle_history.record_vehicle_changes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from utils.vehicle_history import TRACKED_FIELDS, record_vehicle_changes
except ImportError:
    from backend.utils.vehicle_history import TRACKED_FIELDS, record_vehicle_changes  # type: ignore[no-redef]


@pytest.mark.anyio
async def test_records_only_changed_tracked_fields():
    before = {"license_plate": "OLD123", "vehicle_make": "Toyota", "vehicle_color": "Blue"}
    after = {"license_plate": "NEW999", "vehicle_make": "Toyota", "city": "Regina"}  # plate changed; make same; city untracked
    inserts: list = []

    async def _insert(table, row):
        inserts.append((table, row))

    with patch("db_supabase.insert_one", AsyncMock(side_effect=_insert)):
        n = await record_vehicle_changes("d1", before, after, changed_by_user_id="admin1", role="admin")

    assert n == 1
    table, row = inserts[0]
    assert table == "driver_vehicle_history"
    assert row["field"] == "license_plate"
    assert row["old_value"] == "OLD123"
    assert row["new_value"] == "NEW999"
    assert row["changed_by_role"] == "admin"
    assert row["driver_id"] == "d1"


@pytest.mark.anyio
async def test_no_change_writes_nothing():
    before = {"license_plate": "SAME", "vehicle_make": "Honda"}
    after = {"license_plate": "SAME", "vehicle_make": "Honda"}
    with patch("db_supabase.insert_one", AsyncMock()) as ins:
        n = await record_vehicle_changes("d1", before, after, role="driver")
    assert n == 0
    ins.assert_not_awaited()


@pytest.mark.anyio
async def test_invalid_role_coerced_to_system():
    inserts: list = []
    with patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, r: inserts.append(r))):
        await record_vehicle_changes("d1", {"license_plate": "A"}, {"license_plate": "B"}, role="hacker")
    assert inserts[0]["changed_by_role"] == "system"


@pytest.mark.anyio
async def test_db_failure_is_swallowed():
    with patch("db_supabase.insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
        n = await record_vehicle_changes("d1", {"license_plate": "A"}, {"license_plate": "B"})
    assert n == 0  # never raises — must not block the underlying update


def test_tracked_fields_cover_vehicle_and_plate():
    for f in ("vehicle_make", "vehicle_model", "license_plate", "vehicle_type_id"):
        assert f in TRACKED_FIELDS
