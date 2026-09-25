"""N23: only a super_admin may CHANGE the admin money-control settings
(admin_money_daily_cap_per_admin, admin_money_alert_threshold,
admin_dispute_refunds_enabled). A settings-module admin raising their own cap
or switching refunds on would defeat the control.

The dashboard round-trips the whole settings object on every save, so an
UNCHANGED value from a non-super-admin must still save; that path is pinned
here too.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routes.admin.settings import SettingsUpdateRequest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_ADMIN = {"id": "admin-1", "role": "admin", "modules": ["settings"]}
_SUPER = {"id": "super-1", "role": "super_admin"}
_STORED = {
    "id": "app_settings",
    "admin_money_daily_cap_per_admin": 100.0,
    "admin_money_alert_threshold": "50.00",
    "admin_dispute_refunds_enabled": False,
}


@pytest.fixture
def db(monkeypatch):
    from routes.admin import settings

    write = AsyncMock()
    monkeypatch.setattr(settings.db_supabase, "get_rows", AsyncMock(return_value=[dict(_STORED)]))
    monkeypatch.setattr(settings.db_supabase, "update_one", write)
    monkeypatch.setattr(settings.db_supabase, "insert_one", AsyncMock())
    return settings, write


@pytest.mark.parametrize(
    "change",
    [
        {"admin_money_daily_cap_per_admin": Decimal("100000.00")},
        {"admin_money_alert_threshold": Decimal("50.01")},
        {"admin_dispute_refunds_enabled": True},
    ],
)
async def test_non_super_admin_cannot_change_money_fields(db, change):
    settings, write = db
    with pytest.raises(HTTPException) as exc:
        await settings.admin_update_settings(SettingsUpdateRequest(**change), admin=dict(_ADMIN))
    assert exc.value.status_code == 403
    assert next(iter(change)) in exc.value.detail
    write.assert_not_awaited()


async def test_non_super_admin_cannot_set_cap_from_null(monkeypatch, db):
    settings, write = db
    monkeypatch.setattr(settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}]))
    with pytest.raises(HTTPException) as exc:
        await settings.admin_update_settings(
            SettingsUpdateRequest(admin_money_daily_cap_per_admin=Decimal("500")), admin=dict(_ADMIN)
        )
    assert exc.value.status_code == 403
    write.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"admin_money_daily_cap_per_admin": Decimal("100000.00")},
        {"admin_money_alert_threshold": Decimal("50.01")},
        {"admin_dispute_refunds_enabled": True},
    ],
)
async def test_super_admin_can_change_money_fields(db, change):
    settings, write = db
    await settings.admin_update_settings(SettingsUpdateRequest(**change), admin=dict(_SUPER))
    field, value = next(iter(change.items()))
    written = write.await_args.args[2][field]
    assert written == (value if isinstance(value, bool) else float(value))


async def test_non_super_admin_unchanged_round_trip_still_saves(db):
    """The dashboard sends every field back; unchanged money fields must not 403
    a save of an unrelated setting."""
    settings, write = db
    await settings.admin_update_settings(
        SettingsUpdateRequest(
            admin_money_daily_cap_per_admin=Decimal("100.00"),
            admin_money_alert_threshold=Decimal("50"),
            admin_dispute_refunds_enabled=False,
            surge_engine_enabled=False,
        ),
        admin=dict(_ADMIN),
    )
    assert write.await_args.args[2]["surge_engine_enabled"] is False


async def test_non_super_admin_false_flag_before_migration_still_saves(monkeypatch, db):
    """Row without the column (migration 475 not yet applied) reads as None;
    the dashboard's default false must not count as a change."""
    settings, write = db
    monkeypatch.setattr(settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}]))
    await settings.admin_update_settings(
        SettingsUpdateRequest(admin_dispute_refunds_enabled=False, surge_engine_enabled=True), admin=dict(_ADMIN)
    )
    write.assert_awaited_once()
