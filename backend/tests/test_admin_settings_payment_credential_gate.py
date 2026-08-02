"""Tests for the super_admin gate on payment/messaging credential settings.

Corporate + admin portal review, High #4: stripe_secret_key,
stripe_webhook_secret, stripe_connect_webhook_secret, twilio_auth_token,
aws_ses_secret_access_key, and resend_api_key were previously writable by
any "settings"-module admin (only *reading* them required super_admin).
Same privilege gap and same fix shape as the pre-existing LMS/Meta/SOS-paging
gate — see test_admin_settings_lms_gate.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routes.admin import settings as admin_settings

_EXISTING_ROW = {
    "id": "app_settings",
    "stripe_secret_key": "sk_test_real",
    "twilio_auth_token": "real-twilio-token",
    "resend_api_key": "real-resend-key",
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("stripe_secret_key", "sk_test_attacker"),
        ("stripe_webhook_secret", "whsec_attacker"),
        ("stripe_connect_webhook_secret", "whsec_attacker_connect"),
        ("twilio_auth_token", "attacker-known-token"),
        ("aws_ses_secret_access_key", "attacker-known-secret"),
        ("resend_api_key", "attacker-known-key"),
    ],
)
async def test_non_super_admin_cannot_change_payment_credential(field, value):
    req = admin_settings.SettingsUpdateRequest(**{field: value})
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc:
            await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert exc.value.status_code == 403


async def test_non_super_admin_unchanged_credential_fields_still_save():
    """The frontend ships the full object back; identical values must pass."""
    req = admin_settings.SettingsUpdateRequest(
        stripe_secret_key="sk_test_real",
        company_name="Spinr",
    )
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("admin"))
    assert "audit_log_id" in result
    update_one.assert_awaited_once()


async def test_super_admin_can_change_payment_credentials():
    req = admin_settings.SettingsUpdateRequest(
        stripe_secret_key="sk_test_rotated",
        twilio_auth_token="rotated-twilio-token",
    )
    p1, p2, p3 = _patched_db(_EXISTING_ROW)
    with p1, p2 as update_one, p3:
        result = await admin_settings.admin_update_settings(req, admin=_admin("super_admin"))
    assert "audit_log_id" in result
    payload = update_one.await_args.args[2]
    assert payload["stripe_secret_key"] == "sk_test_rotated"
    assert payload["twilio_auth_token"] == "rotated-twilio-token"


@pytest.mark.unit
def test_stripe_secret_key_rejects_live_prefix_outside_production():
    # pytest.ini sets ENV=test — non-production, so a live-prefixed key must
    # be rejected. Bare prefix, no suffix — avoids tripping the repo's
    # secret-scanning pre-commit hook on a realistic-looking fake live key
    # (same reasoning as test_admin_settings_lms_gate.py's fake routing key).
    live_prefix = "sk_live_"
    with pytest.raises(ValidationError):
        admin_settings.SettingsUpdateRequest(stripe_secret_key=live_prefix)


@pytest.mark.unit
def test_stripe_secret_key_accepts_test_prefix_outside_production():
    req = admin_settings.SettingsUpdateRequest(stripe_secret_key="sk_test_ok")
    assert req.stripe_secret_key == "sk_test_ok"


@pytest.mark.unit
def test_stripe_secret_key_rejects_garbage_value():
    with pytest.raises(ValidationError):
        admin_settings.SettingsUpdateRequest(stripe_secret_key="not-a-stripe-key")


@pytest.mark.unit
def test_stripe_secret_key_empty_string_is_allowed():
    """Empty/unset must not be blocked — that's how a field is left untouched
    or intentionally cleared, not a malformed key."""
    req = admin_settings.SettingsUpdateRequest(stripe_secret_key="")
    assert req.stripe_secret_key == ""


@pytest.mark.unit
def test_stripe_secret_key_masked_preview_roundtrip_passes_validation():
    """The mask-roundtrip guard in admin_update_settings drops masked
    previews from the persisted payload, but Pydantic validation itself
    runs first — the mask keeps the real key's own prefix (v[:8] + '*****'),
    so a preview must not be rejected by the format check before the
    endpoint even gets a chance to recognize and drop it."""
    req = admin_settings.SettingsUpdateRequest(stripe_secret_key="sk_test_*****")
    assert req.stripe_secret_key == "sk_test_*****"


def test_credential_reveal_allows_payment_credentials_for_super_admin():
    for field in (
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_connect_webhook_secret",
        "twilio_auth_token",
        "aws_ses_secret_access_key",
        "resend_api_key",
    ):
        assert field in admin_settings._CREDENTIAL_FIELDS
        assert field in admin_settings._SUPER_ADMIN_ONLY_FIELDS
