"""Admin PUT round-trip test for the driver_turn_by_turn_enabled flag.

`driver_turn_by_turn_enabled` (schemas.AppSettings) is the dark-launch gate
for driver-app in-app turn-by-turn navigation
(docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md). It
existed on AppSettings since Phase 1 PR A (#5289), but until this PR nothing
could ever turn it ON: PUT /api/admin/settings builds its update payload from
SettingsUpdateRequest, and that model never declared the field, so any admin
attempt to set it was silently dropped (model_config extra="ignore") -- GET
already worked fine via the schema-default merge in get_app_settings().

Follows the same wiring as test_dispatch_direct_pool_flag_settings.py: a
plain boolean on SettingsUpdateRequest, no credential masking, no
super-admin gate, backed by migration 417's column (see
test_settings_column_parity.py for why a column is required, not optional,
for any field accepted by the API).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_FLAG = "driver_turn_by_turn_enabled"


def test_defaults_to_false():
    """AppSettings' schema default is what every reader falls back to when
    the row doesn't exist yet or the column predates the row."""
    from backend.schemas import AppSettings

    assert AppSettings().driver_turn_by_turn_enabled is False


def test_settings_update_request_round_trips_the_flag():
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(**{_FLAG: True})
    assert req.model_dump(exclude_none=True) == {_FLAG: True}


def test_omitted_flag_is_excluded_from_the_update_payload():
    """'None = leave unchanged' convention -- a save that doesn't mention
    the flag must not reset it."""
    from backend.routes.admin.settings import SettingsUpdateRequest

    dumped = SettingsUpdateRequest().model_dump(exclude_none=True)
    assert _FLAG not in dumped


@pytest.mark.anyio
async def test_admin_put_round_trip_persists_true():
    """End-to-end through admin_update_settings: True in -> True in the
    Postgres update payload. Not a credential/super-admin-only field, so a
    plain "admin" role must be able to flip it."""
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(admin_settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}])),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        result = await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(**{_FLAG: True}),
            admin={"id": "admin-1", "role": "admin"},
        )

    assert result["message"] == "Settings updated"
    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert _table == "settings"
    assert _filter == {"id": "app_settings"}
    assert payload[_FLAG] is True


@pytest.mark.anyio
async def test_admin_put_round_trip_persists_false():
    """The other direction -- flipping it back off must round-trip too."""
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(
            admin_settings.db_supabase,
            "get_rows",
            AsyncMock(return_value=[{"id": "app_settings", _FLAG: True}]),
        ),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(**{_FLAG: False}),
            admin={"id": "admin-1", "role": "admin"},
        )

    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert payload[_FLAG] is False


def test_flag_is_not_masked_as_a_credential():
    """Sanity check it wasn't accidentally added to the credential-masking
    set -- it's a boolean feature gate, not a secret."""
    from backend.routes.admin.settings import _CREDENTIAL_FIELDS, _SUPER_ADMIN_ONLY_FIELDS

    assert _FLAG not in _CREDENTIAL_FIELDS
    assert _FLAG not in _SUPER_ADMIN_ONLY_FIELDS


def test_migration_417_adds_the_column_with_false_default():
    """See test_settings_column_parity.py's module docstring: any field
    SettingsUpdateRequest accepts without a matching `settings` column 500s
    the WHOLE save (PGRST204) on first use, not just this field."""
    import re
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "417_settings_driver_turn_by_turn_enabled.sql"
    ).read_text(encoding="utf-8")
    match = re.search(rf"{_FLAG}\s+BOOLEAN NOT NULL DEFAULT (TRUE|FALSE)", sql, re.IGNORECASE)
    assert match, f"{_FLAG} not declared with an explicit boolean default in migration 417"
    assert match.group(1).upper() == "FALSE", (
        f"{_FLAG} must default FALSE -- applying the migration must not silently enable "
        "the feature for every ride in progress at deploy time."
    )
