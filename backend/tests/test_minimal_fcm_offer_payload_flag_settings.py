"""Admin PUT round-trip test for the #1231 finding 15 minimal-FCM-offer flag.

`minimal_fcm_offer_payload_enabled` (schemas.AppSettings) is the kill switch
for dropping precise pickup/dropoff coordinates and rider_rating from the
ride-offer FCM `data` payload (backend/routes/rides/matching.py's
`_FCM_EXCLUDE`). It follows the same wiring as
`dispatch_direct_pool_enabled` (test_dispatch_direct_pool_flag_settings.py):
a plain boolean on SettingsUpdateRequest, no credential masking, no
super-admin gate, backed by migration 424's column.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_FLAG = "minimal_fcm_offer_payload_enabled"


def test_defaults_to_false():
    """AppSettings' schema default is what every reader falls back to when
    the row doesn't exist yet or the column predates the row."""
    from backend.schemas import AppSettings

    assert AppSettings().minimal_fcm_offer_payload_enabled is False


def test_settings_update_request_round_trips_the_flag():
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(**{_FLAG: True})
    assert req.model_dump(exclude_none=True) == {_FLAG: True}


def test_omitted_flag_is_excluded_from_the_update_payload():
    """'None = leave unchanged' convention (same as every other kill switch
    in this repo) -- a save that doesn't mention the flag must not reset it."""
    from backend.routes.admin.settings import SettingsUpdateRequest

    dumped = SettingsUpdateRequest().model_dump(exclude_none=True)
    assert _FLAG not in dumped


@pytest.mark.anyio
async def test_admin_put_round_trip_persists_true():
    """End-to-end through admin_update_settings: True in -> True in the
    Postgres update payload. Not gated behind super_admin -- it's a plain
    rollout switch, same posture as dispatch_direct_pool_enabled."""
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
    """The other direction -- flipping it back off (the rollback action)
    must round-trip too, not just the initial True set."""
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
    set -- it's a boolean rollback switch, not a secret."""
    from backend.routes.admin.settings import _CREDENTIAL_FIELDS, _SUPER_ADMIN_ONLY_FIELDS

    assert _FLAG not in _CREDENTIAL_FIELDS
    assert _FLAG not in _SUPER_ADMIN_ONLY_FIELDS


def test_migration_424_adds_the_column_with_false_default():
    """See test_settings_column_parity.py's module docstring: any field
    SettingsUpdateRequest accepts without a matching `settings` column 500s
    the WHOLE save (PGRST204) on first use, not just this field."""
    import re
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "424_settings_minimal_fcm_offer_payload_enabled.sql"
    ).read_text(encoding="utf-8")
    match = re.search(rf"{_FLAG}\s+BOOLEAN NOT NULL DEFAULT (TRUE|FALSE)", sql, re.IGNORECASE)
    assert match, f"{_FLAG} not declared with an explicit boolean default in migration 424"
    assert match.group(1).upper() == "FALSE", (
        f"{_FLAG} must default FALSE -- applying the migration must not silently start "
        "stripping fields from a live FCM payload without a human flipping the flag."
    )
