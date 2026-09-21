"""Tests for PostHog session-replay app_settings (admin kill switch).

Dark-launched: default off, no SDK init on the apps until both
posthog_session_replay_enabled is true AND posthog_api_key is set.
LogRocket is intentionally left in place and untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_app_settings_defaults_replay_off_and_empty_key():
    from backend.schemas import AppSettings

    settings = AppSettings()
    assert settings.posthog_session_replay_enabled is False
    assert settings.posthog_api_key == ""
    assert settings.posthog_host == "https://us.i.posthog.com"


def test_settings_update_request_round_trips_posthog_fields():
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(
        posthog_session_replay_enabled=True,
        posthog_api_key="phc_test",
        posthog_host="https://eu.i.posthog.com",
    )
    dumped = req.model_dump(exclude_none=True)
    assert dumped["posthog_session_replay_enabled"] is True
    assert dumped["posthog_api_key"] == "phc_test"
    assert dumped["posthog_host"] == "https://eu.i.posthog.com"


def test_settings_update_request_omitted_posthog_fields_are_excluded():
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest()
    dumped = req.model_dump(exclude_none=True)
    assert "posthog_session_replay_enabled" not in dumped
    assert "posthog_api_key" not in dumped
    assert "posthog_host" not in dumped


@pytest.mark.anyio
async def test_public_settings_defaults_replay_off():
    from backend.routes import settings as settings_mod

    with patch.object(settings_mod, "get_app_settings", AsyncMock(return_value={})):
        result = await settings_mod.get_public_settings()

    assert result["posthog_session_replay_enabled"] is False
    assert result["posthog_api_key"] == ""
    assert result["posthog_host"] == "https://us.i.posthog.com"


@pytest.mark.anyio
async def test_public_settings_reflects_posthog_when_enabled():
    from backend.routes import settings as settings_mod

    with patch.object(
        settings_mod,
        "get_app_settings",
        AsyncMock(
            return_value={
                "posthog_session_replay_enabled": True,
                "posthog_api_key": "phc_live",
                "posthog_host": "https://eu.i.posthog.com",
            }
        ),
    ):
        result = await settings_mod.get_public_settings()

    assert result["posthog_session_replay_enabled"] is True
    assert result["posthog_api_key"] == "phc_live"
    assert result["posthog_host"] == "https://eu.i.posthog.com"


@pytest.mark.anyio
async def test_non_super_admin_can_change_posthog_replay_flag():
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(admin_settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}])),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(posthog_session_replay_enabled=True),
            admin={"id": "admin-1", "role": "admin"},
        )

    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert payload["posthog_session_replay_enabled"] is True


def test_posthog_flag_is_not_masked_as_a_credential():
    from backend.routes.admin.settings import _CREDENTIAL_FIELDS, _SUPER_ADMIN_ONLY_FIELDS

    assert "posthog_session_replay_enabled" not in _CREDENTIAL_FIELDS
    assert "posthog_session_replay_enabled" not in _SUPER_ADMIN_ONLY_FIELDS
    assert "posthog_api_key" not in _CREDENTIAL_FIELDS
    assert "posthog_host" not in _CREDENTIAL_FIELDS
    assert "posthog_api_key" in _SUPER_ADMIN_ONLY_FIELDS
    assert "posthog_host" in _SUPER_ADMIN_ONLY_FIELDS


def test_posthog_host_requires_https():
    from backend.routes.admin.settings import SettingsUpdateRequest

    with pytest.raises(ValidationError):
        SettingsUpdateRequest(posthog_host="http://evil.example.com")


@pytest.mark.parametrize(
    "host",
    ["https://us.i.posthog.com", "https://eu.i.posthog.com", "http://localhost:8000", ""],
)
def test_posthog_host_accepts_https_and_localhost(host):
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(posthog_host=host)
    assert req.posthog_host == host


def test_posthog_api_key_rejects_personal_key():
    from backend.routes.admin.settings import SettingsUpdateRequest

    with pytest.raises(ValidationError):
        SettingsUpdateRequest(posthog_api_key="phx_personal")


def test_posthog_api_key_accepts_empty_and_project_key():
    from backend.routes.admin.settings import SettingsUpdateRequest

    assert SettingsUpdateRequest(posthog_api_key="").posthog_api_key == ""
    assert SettingsUpdateRequest(posthog_api_key="  phc_live  ").posthog_api_key == "phc_live"


_EXISTING_POSTHOG_ROW = {
    "id": "app_settings",
    "posthog_session_replay_enabled": False,
    "posthog_api_key": "phc_existing",
    "posthog_host": "https://us.i.posthog.com",
}


def _admin(role: str) -> dict:
    return {"id": "admin-1", "role": role}


def _patched_db(existing_row: dict | None):
    from backend.routes.admin import settings as admin_settings

    return (
        patch.object(
            admin_settings.db_supabase,
            "get_rows",
            new=AsyncMock(return_value=[existing_row] if existing_row else []),
        ),
        patch.object(admin_settings.db_supabase, "update_one", new=AsyncMock()),
        patch.object(admin_settings.db_supabase, "insert_one", new=AsyncMock()),
    )


@pytest.mark.anyio
async def test_non_super_admin_cannot_change_posthog_host():
    from backend.routes.admin import settings as admin_settings

    req = admin_settings.SettingsUpdateRequest(posthog_host="https://evil.example.com")
    p1, p2, p3 = _patched_db(_EXISTING_POSTHOG_ROW)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_non_super_admin_cannot_change_posthog_api_key():
    from backend.routes.admin import settings as admin_settings

    req = admin_settings.SettingsUpdateRequest(posthog_api_key="phc_attacker")
    p1, p2, p3 = _patched_db(_EXISTING_POSTHOG_ROW)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_non_super_admin_unchanged_posthog_destination_still_saves():
    from backend.routes.admin import settings as admin_settings

    req = admin_settings.SettingsUpdateRequest(
        posthog_host="https://us.i.posthog.com",
        posthog_api_key="phc_existing",
        posthog_session_replay_enabled=True,
    )
    p1, p2, p3 = _patched_db(_EXISTING_POSTHOG_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert "audit_log_id" in result
    update_one.assert_awaited_once()
    payload = update_one.await_args.args[2]
    assert payload["posthog_session_replay_enabled"] is True


@pytest.mark.anyio
async def test_super_admin_can_change_posthog_destination():
    from backend.routes.admin import settings as admin_settings

    req = admin_settings.SettingsUpdateRequest(
        posthog_host="https://eu.i.posthog.com",
        posthog_api_key="phc_rotated",
    )
    p1, p2, p3 = _patched_db(_EXISTING_POSTHOG_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("super_admin"))
    assert "audit_log_id" in result
    payload = update_one.await_args.args[2]
    assert payload["posthog_host"] == "https://eu.i.posthog.com"
    assert payload["posthog_api_key"] == "phc_rotated"
