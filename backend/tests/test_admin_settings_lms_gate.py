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


# ── SOS on-call paging (ACTION_ITEMS.md B15(b)) — same privilege shape ─────
# as the LMS pair: changing the webhook destination or its routing key is a
# credential-reveal-equivalent action (SSRF + exfil of safety-incident data).

_EXISTING_ROW_WITH_PAGING = {
    "id": "app_settings",
    "sos_paging_webhook_url": "https://events.pagerduty.com/v2/enqueue",
    "sos_paging_routing_key": "real-routing-key",
}


async def test_non_super_admin_cannot_change_sos_paging_webhook_url():
    req = admin_settings.SettingsUpdateRequest(sos_paging_webhook_url="https://evil.example.com/collect")
    p1, p2, p3 = _patched_db(_EXISTING_ROW_WITH_PAGING)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


async def test_non_super_admin_cannot_change_sos_paging_routing_key():
    req = admin_settings.SettingsUpdateRequest(sos_paging_routing_key="attacker-known-key")
    p1, p2, p3 = _patched_db(_EXISTING_ROW_WITH_PAGING)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


async def test_super_admin_can_change_sos_paging_fields():
    req = admin_settings.SettingsUpdateRequest(
        sos_paging_webhook_url="https://events2.pagerduty.com/v2/enqueue",
        sos_paging_routing_key="rotated-routing-key",
    )
    p1, p2, p3 = _patched_db(_EXISTING_ROW_WITH_PAGING)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("super_admin"))
    assert "audit_log_id" in result
    payload = update_one.await_args.args[2]
    assert payload["sos_paging_webhook_url"] == "https://events2.pagerduty.com/v2/enqueue"
    assert payload["sos_paging_routing_key"] == "rotated-routing-key"


def test_sos_paging_routing_key_masked_on_get():
    masked = admin_settings._mask_credentials(
        {"sos_paging_routing_key": "R0123456789ABCDEF0123456789ABCD", "sos_paging_webhook_url": "https://x"}
    )
    assert masked["sos_paging_routing_key"] == "R0123456*****"
    # webhook_url is plain, like lms_api_base_url — not in _CREDENTIAL_FIELDS.
    assert masked["sos_paging_webhook_url"] == "https://x"


@pytest.mark.unit
def test_sos_paging_webhook_url_requires_https():
    with pytest.raises(ValidationError):
        admin_settings.SettingsUpdateRequest(sos_paging_webhook_url="http://evil.example.com/collect")


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    ["https://events.pagerduty.com/v2/enqueue", "http://localhost:3000", "http://127.0.0.1:3000", ""],
)
def test_sos_paging_webhook_url_accepts_https_and_localhost(url):
    req = admin_settings.SettingsUpdateRequest(sos_paging_webhook_url=url)
    assert req.sos_paging_webhook_url == url


def test_credential_reveal_allows_sos_paging_routing_key_for_super_admin():
    assert "sos_paging_routing_key" in admin_settings._CREDENTIAL_FIELDS
    # webhook_url stays revealable-list-free — it's not masked, so there's
    # nothing to "reveal"; GET already returns it plain.
    assert "sos_paging_webhook_url" not in admin_settings._CREDENTIAL_FIELDS
