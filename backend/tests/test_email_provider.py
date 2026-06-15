"""Unit tests for utils.email_provider.send_transactional_email.

Provider strategy under test: AWS SES primary, Resend guardrail fallback.

Covers:
- SES configured + succeeds → SES used, Resend never called, returns True
- SES configured + raises → falls back to Resend, returns True
- SES unconfigured + Resend configured → Resend used, returns True
- SES succeeds → boto3 receives html/text body + the SES from address
- Resend non-2xx → returns False
- Neither provider configured → returns False, no network calls
- Empty body / no recipient → returns False, no network calls

Patching notes:
  email_provider._load_settings imports get_app_settings inline as
  `from settings_loader import get_app_settings`, so we patch
  `settings_loader.get_app_settings`.

  boto3 is imported inline inside _send_ses_sync as `import boto3`, so we
  patch `boto3.client`. httpx is imported inline in the Resend path as
  `import httpx`, so we patch `httpx.AsyncClient`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from utils.email_provider import send_transactional_email
except ImportError:
    from backend.utils.email_provider import send_transactional_email  # type: ignore[no-redef]


_SES_SETTINGS = {
    "aws_ses_region": "ca-central-1",
    "aws_ses_access_key_id": "AKIATEST",
    "aws_ses_secret_access_key": "secret-shh",
    "aws_ses_from_email": "ses@spinr.ca",
}

_RESEND_SETTINGS = {
    "resend_api_key": "re_test-key",
    "resend_from_email": "resend@spinr.ca",
}


def _settings(**overrides):
    return AsyncMock(return_value=dict(overrides))


def _mock_resp(status_code: int = 202) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    return resp


def _async_client_mock(post_side_effect=None) -> MagicMock:
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock.post = AsyncMock(return_value=_mock_resp(202))
    return mock


def _boto3_mock(send_side_effect=None) -> MagicMock:
    """Return a fake boto3 module-level client() factory and the SES client."""
    ses_client = MagicMock()
    if send_side_effect is not None:
        ses_client.send_raw_email = MagicMock(side_effect=send_side_effect)
    else:
        ses_client.send_raw_email = MagicMock(return_value={"MessageId": "msg-123"})
    factory = MagicMock(return_value=ses_client)
    return factory, ses_client


# ---------------------------------------------------------------------------
# SES primary
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ses_used_when_configured_and_resend_not_called():
    factory, ses_client = _boto3_mock()
    resend_client = _async_client_mock()

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS, **_RESEND_SETTINGS)),
        patch("boto3.client", factory),
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", html="<p>hi</p>")

    assert ok is True
    ses_client.send_raw_email.assert_called_once()
    resend_client.post.assert_not_awaited()  # guardrail must not fire on SES success


@pytest.mark.anyio
async def test_ses_receives_body_and_from_address():
    factory, ses_client = _boto3_mock()

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),
        patch("boto3.client", factory),
    ):
        ok = await send_transactional_email(
            to="rider@example.com",
            subject="Receipt",
            html="<p>hi</p>",
            text="hi",
            default_from="receipts@spinr.ca",
        )

    assert ok is True
    # Region + credentials forwarded to boto3.client
    _, kwargs = factory.call_args
    assert kwargs["region_name"] == "ca-central-1"
    assert kwargs["aws_access_key_id"] == "AKIATEST"
    assert kwargs["aws_secret_access_key"] == "secret-shh"
    # SendRawEmail with the verified SES from address as Source + envelope dest.
    _, send_kwargs = ses_client.send_raw_email.call_args
    assert send_kwargs["Source"] == "Spinr <ses@spinr.ca>"
    assert send_kwargs["Destinations"] == ["rider@example.com"]
    # Raw MIME carries both alternatives + the headers (parse, don't substring:
    # MIMEText base64-encodes the payloads under a utf-8 charset).
    import email as _email

    parsed = _email.message_from_string(send_kwargs["RawMessage"]["Data"])
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed["Subject"] == "Receipt"
    assert parsed["From"] == "Spinr <ses@spinr.ca>"
    assert parsed["To"] == "rider@example.com"
    parts = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8") for p in parsed.get_payload()}
    assert parts["text/plain"] == "hi"
    assert parts["text/html"] == "<p>hi</p>"


# ---------------------------------------------------------------------------
# Guardrail fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ses_failure_falls_back_to_resend():
    factory, ses_client = _boto3_mock(send_side_effect=RuntimeError("SES down"))
    resend_client = _async_client_mock()

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS, **_RESEND_SETTINGS)),
        patch("boto3.client", factory),
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", text="hi")

    assert ok is True
    ses_client.send_raw_email.assert_called_once()
    resend_client.post.assert_awaited_once()  # guardrail caught the SES failure


@pytest.mark.anyio
async def test_resend_used_when_ses_unconfigured():
    resend_client = _async_client_mock()

    with (
        patch("settings_loader.get_app_settings", _settings(**_RESEND_SETTINGS)),
        patch("boto3.client", MagicMock()) as boto_factory,
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", html="<p>hi</p>")

    assert ok is True
    boto_factory.assert_not_called()  # SES skipped without touching boto3
    resend_client.post.assert_awaited_once()
    _, post_kwargs = resend_client.post.call_args
    assert post_kwargs["json"]["from"] == "Spinr <resend@spinr.ca>"
    assert post_kwargs["json"]["html"] == "<p>hi</p>"


@pytest.mark.anyio
async def test_resend_non_2xx_returns_false():
    resend_client = _async_client_mock()
    resend_client.post = AsyncMock(return_value=_mock_resp(500))

    with (
        patch("settings_loader.get_app_settings", _settings(**_RESEND_SETTINGS)),
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", text="hi")

    assert ok is False


# ---------------------------------------------------------------------------
# Nothing configured / bad input
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_provider_configured_returns_false():
    resend_client = _async_client_mock()

    with (
        patch("settings_loader.get_app_settings", _settings()),
        patch("boto3.client", MagicMock()) as boto_factory,
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", text="hi")

    assert ok is False
    boto_factory.assert_not_called()
    resend_client.post.assert_not_awaited()


@pytest.mark.anyio
async def test_no_recipient_returns_false_without_loading_settings():
    with patch("settings_loader.get_app_settings", AsyncMock()) as load:
        ok = await send_transactional_email(to="", subject="x", text="hi")
    assert ok is False
    load.assert_not_awaited()


@pytest.mark.anyio
async def test_empty_body_returns_false_without_loading_settings():
    with patch("settings_loader.get_app_settings", AsyncMock()) as load:
        ok = await send_transactional_email(to="r@example.com", subject="x")
    assert ok is False
    load.assert_not_awaited()
