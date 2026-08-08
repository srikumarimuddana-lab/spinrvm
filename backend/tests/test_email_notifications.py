"""Tests for the email-channel policy layer (utils/email_notifications.py).

The matrix that matters here is which guard suppresses a send and which does
not — getting TRANSACTIONAL vs OPTIONAL backwards would either spam people who
opted out or silently mute a suspension notice.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.email_notifications as en
from utils.email_layout import render_email

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_USER = {"id": "user-1", "email": "driver@example.test", "deleted_at": None}
_RENDERED = render_email(heading="Account suspended", paragraphs=["Contact support."])


def _send_mock(result=True):
    return AsyncMock(return_value=result)


async def _run(*, user=_USER, prefs=None, settings=None, send=None, **kwargs):
    """Drive send_lifecycle_email with every external dependency stubbed."""
    send = send or _send_mock()
    with (
        patch.object(en, "send_transactional_email", send),
        patch.object(en, "get_app_settings", AsyncMock(return_value=settings or {})),
        patch.object(en.db_supabase, "get_user_by_id", AsyncMock(return_value=user)),
        patch.object(en.db_supabase, "get_rows", AsyncMock(return_value=prefs or [])),
    ):
        result = await en.send_lifecycle_email(
            user_id="user-1",
            subject=kwargs.pop("subject", "Your Spinr account"),
            rendered=kwargs.pop("rendered", _RENDERED),
            email_type=kwargs.pop("email_type", "driver_suspended"),
            **kwargs,
        )
    return result, send


# --- The class distinction -------------------------------------------------


async def test_transactional_ignores_the_email_enabled_preference():
    # A driver must not be able to opt out of being told they were suspended.
    result, send = await _run(
        prefs=[{"email_enabled": False}],
        email_class=en.EmailClass.TRANSACTIONAL,
    )
    assert result is True
    send.assert_awaited_once()


async def test_optional_honours_the_email_enabled_preference():
    result, send = await _run(
        prefs=[{"email_enabled": False}],
        email_class=en.EmailClass.OPTIONAL,
    )
    assert result is False
    send.assert_not_awaited()


async def test_optional_sends_when_opted_in():
    result, send = await _run(prefs=[{"email_enabled": True}], email_class=en.EmailClass.OPTIONAL)
    assert result is True
    send.assert_awaited_once()


async def test_optional_sends_when_no_preference_row_exists():
    # No row means "not yet configured", not "opted out" — matches the defaults
    # routes/notifications.py returns to the app.
    result, send = await _run(prefs=[], email_class=en.EmailClass.OPTIONAL)
    assert result is True
    send.assert_awaited_once()


async def test_defaults_to_transactional():
    result, send = await _run(prefs=[{"email_enabled": False}])
    assert result is True
    send.assert_awaited_once()


# --- Recipient guard -------------------------------------------------------


async def test_skips_user_with_no_email_on_file():
    # users.email is nullable; anyone who abandoned profile setup has none.
    result, send = await _run(user={"id": "user-1", "email": None})
    assert result is False
    send.assert_not_awaited()


async def test_skips_blank_email():
    result, send = await _run(user={"id": "user-1", "email": "   "})
    assert result is False
    send.assert_not_awaited()


async def test_skips_soft_deleted_account():
    result, send = await _run(user={"id": "u", "email": "x@y.test", "deleted_at": "2026-01-01T00:00:00Z"})
    assert result is False
    send.assert_not_awaited()


async def test_skips_when_user_cannot_be_loaded():
    result, send = await _run(user=None)
    assert result is False
    send.assert_not_awaited()


async def test_preloaded_user_skips_the_lookup():
    send = _send_mock()
    lookup = AsyncMock(return_value=_USER)
    with (
        patch.object(en, "send_transactional_email", send),
        patch.object(en, "get_app_settings", AsyncMock(return_value={})),
        patch.object(en.db_supabase, "get_user_by_id", lookup),
    ):
        result = await en.send_lifecycle_email(
            user_id="user-1",
            subject="s",
            rendered=_RENDERED,
            email_type="t",
            user=_USER,
        )
    assert result is True
    lookup.assert_not_awaited()


# --- Kill switch -----------------------------------------------------------


async def test_kill_switch_suppresses_every_lifecycle_email():
    result, send = await _run(settings={"lifecycle_emails_enabled": False})
    assert result is False
    send.assert_not_awaited()


async def test_kill_switch_defaults_to_enabled_when_absent():
    result, send = await _run(settings={})
    assert result is True
    send.assert_awaited_once()


async def test_settings_failure_fails_open():
    # A Supabase hiccup must not silently mute suspension notices.
    send = _send_mock()
    with (
        patch.object(en, "send_transactional_email", send),
        patch.object(en, "get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(en.db_supabase, "get_user_by_id", AsyncMock(return_value=_USER)),
        patch.object(en.db_supabase, "get_rows", AsyncMock(return_value=[])),
    ):
        result = await en.send_lifecycle_email(user_id="user-1", subject="s", rendered=_RENDERED, email_type="t")
    assert result is True


async def test_preference_load_failure_fails_closed_for_optional():
    # Inverse of the kill switch: unconfirmed consent means don't send.
    send = _send_mock()
    with (
        patch.object(en, "send_transactional_email", send),
        patch.object(en, "get_app_settings", AsyncMock(return_value={})),
        patch.object(en.db_supabase, "get_user_by_id", AsyncMock(return_value=_USER)),
        patch.object(en.db_supabase, "get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        result = await en.send_lifecycle_email(
            user_id="user-1",
            subject="s",
            rendered=_RENDERED,
            email_type="t",
            email_class=en.EmailClass.OPTIONAL,
        )
    assert result is False
    send.assert_not_awaited()


# --- Best-effort contract --------------------------------------------------


async def test_provider_failure_returns_false_without_raising():
    result, _ = await _run(send=_send_mock(result=False))
    assert result is False


async def test_provider_exception_is_swallowed():
    # The caller's state change is already committed; this must never undo it.
    result, _ = await _run(send=AsyncMock(side_effect=RuntimeError("SES exploded")))
    assert result is False


# --- Payload ---------------------------------------------------------------


async def test_sends_both_html_and_text_parts():
    # Without both, send_transactional_email cannot build multipart/alternative.
    _, send = await _run()
    kwargs = send.await_args.kwargs
    assert kwargs["html"] == _RENDERED.html
    assert kwargs["text"] == _RENDERED.text


async def test_log_id_is_the_user_id_not_the_address():
    # PIPEDA: the recipient address must never reach logs.
    _, send = await _run()
    kwargs = send.await_args.kwargs
    assert kwargs["log_id"] == "user-1"
    assert kwargs["recipient_user_id"] == "user-1"
    assert "@" not in kwargs["log_id"]


async def test_email_type_is_passed_through_for_the_send_log():
    _, send = await _run(email_type="document_expiry_warning")
    assert send.await_args.kwargs["email_type"] == "document_expiry_warning"


async def test_recipient_address_is_trimmed():
    _, send = await _run(user={"id": "u", "email": "  driver@example.test  "})
    assert send.await_args.kwargs["to"] == "driver@example.test"
