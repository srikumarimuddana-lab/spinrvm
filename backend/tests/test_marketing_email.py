"""Unit tests for utils.marketing_email.send_marketing_email.

Covers:
- not eligible (no consent / suppressed) → no send, returns False
- eligible → send_transactional_email called with email_type='marketing',
  the marketing sender, a List-Unsubscribe header, and an unsubscribe link +
  CASL footer appended to the body
- a DB NULL settings value never renders as the literal 'None' in the footer

Patching: marketing_email gates via services.marketing_consent.is_eligible,
loads settings via settings_loader.get_app_settings, and delegates delivery to
utils.email_provider.send_transactional_email. We patch all three at the names
marketing_email resolves them to.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from utils import marketing_email as me
except ImportError:  # pragma: no cover
    from backend.utils import marketing_email as me  # type: ignore

pytestmark = pytest.mark.anyio

_USER = "33333333-3333-3333-3333-333333333333"
_SETTINGS = {
    "company_name": "Spinr",
    "company_address": "123 Main St",
    "company_city": "Saskatoon",
    "company_province": "SK",
    "company_postal_code": "S7K 0A1",
    "marketing_from_email": "news@spinr.ca",
}


async def test_skips_when_not_eligible():
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=False)),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        ok = await me.send_marketing_email(
            to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", log_id="rider"
        )
    assert ok is False
    send.assert_not_called()


async def test_sends_with_footer_header_and_marketing_sender():
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=dict(_SETTINGS))),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        ok = await me.send_marketing_email(
            to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", text="Hi", log_id="rider"
        )

    assert ok is True
    send.assert_awaited_once()
    kw = send.call_args.kwargs
    assert kw["email_type"] == "marketing"
    assert kw["recipient_user_id"] == _USER
    assert kw["from_email"] == "news@spinr.ca"
    # One-click unsubscribe headers present (RFC 8058).
    assert "List-Unsubscribe" in kw["extra_headers"]
    assert kw["extra_headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # CASL footer: physical address + unsubscribe link in both bodies.
    assert "123 Main St" in kw["html"] and "Saskatoon" in kw["html"]
    assert "/api/v1/marketing/unsubscribe?token=" in kw["html"]
    assert "Unsubscribe" in kw["text"]


async def test_null_settings_never_render_none():
    # Loader can surface a DB NULL as Python None; the footer must coalesce.
    null_settings = {k: None for k in _SETTINGS}
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=null_settings)),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        await me.send_marketing_email(to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", log_id="r")
    html = send.call_args.kwargs["html"]
    assert "None" not in html
    # Falls back to the default sender name "Spinr".
    assert "Spinr" in html


async def test_throttling_flag_off_ignores_should_throttle():
    """notification_throttling_enabled False (default) — should_throttle is
    never even consulted; matches the ships-dark contract."""
    settings = dict(_SETTINGS, notification_throttling_enabled=False)
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch("utils.notification_throttle.should_throttle", AsyncMock(return_value=True)),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        ok = await me.send_marketing_email(to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", log_id="r")
    assert ok is True
    send.assert_awaited_once()


async def test_throttling_flag_on_and_throttled_suppresses():
    settings = dict(_SETTINGS, notification_throttling_enabled=True)
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch("utils.notification_throttle.should_throttle", AsyncMock(return_value=True)),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        ok = await me.send_marketing_email(to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", log_id="r")
    assert ok is False
    send.assert_not_called()


async def test_throttling_flag_on_and_not_throttled_sends():
    settings = dict(_SETTINGS, notification_throttling_enabled=True)
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch("utils.notification_throttle.should_throttle", AsyncMock(return_value=False)),
        patch("utils.marketing_email.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        ok = await me.send_marketing_email(to="a@b.com", user_id=_USER, subject="Deal", html="<p>Hi</p>", log_id="r")
    assert ok is True
    send.assert_awaited_once()
