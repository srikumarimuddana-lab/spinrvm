"""Tests for the super_admin gate on LMS integration settings.

The LMS base URL receives lms_api_key on every training lookup, so changing
either field is privileged like a credential reveal (PR #2073 review):
a settings-module admin repointing the URL could exfiltrate the key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routes.admin import settings as admin_settings

_EXISTING_ROW = {
    "id": "app_settings",
    "lms_api_base_url": "https://training.spinr.ca",
    "lms_api_key": "real-secret",
}


def _admin(role: str) -> dict:
    return {"id": "admin-1", "role": role}


def _patched_db(existing_row: dict | None):
    return (
        patch.object(
            admin_settings.db_supabase,
            "get_rows",
            new=AsyncMock(return_value=[existing_row] if existing_row else []),
        ),
        patch.object(admin_settings.db_supabase, "update_one", new=AsyncMock()),
        patch.object(admin_settings.db_supabase, "insert_one", new=AsyncMock()),
    )


async def test_non_super_admin_cannot_change_lms_base_url():
    req = admin_settings.SettingsUpdateRequest(lms_api_base_url="https://evil.example.com")
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


async def test_non_super_admin_cannot_change_lms_api_key():
    req = admin_settings.SettingsUpdateRequest(lms_api_key="attacker-known-key")
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


async def test_non_super_admin_unchanged_lms_fields_still_save():
    """The frontend ships the full object back; identical values must pass."""
    req = admin_settings.SettingsUpdateRequest(
        lms_api_base_url="https://training.spinr.ca",
        company_name="Spinr",
    )
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert "audit_log_id" in result
    update_one.assert_awaited_once()


async def test_super_admin_can_change_lms_fields():
    req = admin_settings.SettingsUpdateRequest(
        lms_api_base_url="https://training2.spinr.ca",
        lms_api_key="rotated-secret",
    )
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("super_admin"))
    assert "audit_log_id" in result
    payload = update_one.await_args.args[2]
    assert payload["lms_api_base_url"] == "https://training2.spinr.ca"
    assert payload["lms_api_key"] == "rotated-secret"


@pytest.mark.unit
def test_lms_base_url_requires_https():
    with pytest.raises(ValidationError):
        admin_settings.SettingsUpdateRequest(lms_api_base_url="http://evil.example.com")


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    ["https://training.spinr.ca", "http://localhost:3000", "http://127.0.0.1:3000", ""],
)
def test_lms_base_url_accepts_https_and_localhost(url):
    req = admin_settings.SettingsUpdateRequest(lms_api_base_url=url)
    assert req.lms_api_base_url == url
