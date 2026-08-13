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


@pytest.fixture(autouse=True)
def _stub_email_db():
    """Stub the suppression lookup + send-log write for every test.

    email_provider does `import db_supabase` (fallback path) and calls
    db_supabase.find_one / insert_one, so we patch them there. Defaults:
    not suppressed, log write is a no-op. Individual tests re-patch to assert.
    """
    with (
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
    ):
        yield


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
async def test_ses_attachment_in_mixed_mime():
    factory, ses_client = _boto3_mock()
    att = [{"filename": "receipt.pdf", "content": b"%PDF-1.4 fake", "mime": "application/pdf"}]
    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),
        patch("boto3.client", factory),
    ):
        ok = await send_transactional_email(
            to="rider@example.com", subject="Receipt", html="<p>hi</p>", attachments=att
        )
    assert ok is True
    _, send_kwargs = ses_client.send_raw_email.call_args
    import email as _email

    parsed = _email.message_from_string(send_kwargs["RawMessage"]["Data"])
    assert parsed.get_content_type() == "multipart/mixed"
    pdf_parts = [p for p in parsed.walk() if p.get_content_type() == "application/pdf"]
    assert len(pdf_parts) == 1
    assert pdf_parts[0].get_filename() == "receipt.pdf"
    assert pdf_parts[0].get_payload(decode=True) == b"%PDF-1.4 fake"


@pytest.mark.anyio
async def test_resend_attachment_base64():
    import base64

    resend_client = _async_client_mock()
    att = [{"filename": "receipt.pdf", "content": b"%PDF-1.4 fake", "mime": "application/pdf"}]
    with (
        patch("settings_loader.get_app_settings", _settings(**_RESEND_SETTINGS)),
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="rider@example.com", subject="Receipt", text="hi", attachments=att)
    assert ok is True
    _, post_kwargs = resend_client.post.call_args
    sent = post_kwargs["json"]["attachments"]
    assert sent[0]["filename"] == "receipt.pdf"
    assert base64.b64decode(sent[0]["content"]) == b"%PDF-1.4 fake"


@pytest.mark.anyio
async def test_ses_failure_log_does_not_leak_recipient(caplog):
    """PIPEDA: SES MessageRejected echoes the recipient — it must not hit logs."""
    addr = "mkkreddy52@gmail.com"
    err = RuntimeError(f"MessageRejected: identities failed the check: {addr}")
    factory, _ = _boto3_mock(send_side_effect=err)

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),
        patch("boto3.client", factory),
        caplog.at_level("ERROR"),
    ):
        ok = await send_transactional_email(to=addr, subject="Receipt", text="hi")

    assert ok is False  # SES failed, no Resend configured
    assert addr not in caplog.text  # raw address scrubbed
    assert "MessageRejected" in caplog.text  # but the diagnostic survives


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


# ---------------------------------------------------------------------------
# Suppression list + send log
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_suppressed_recipient_skips_send_and_logs():
    resend_client = _async_client_mock()
    inserts: list = []

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS, **_RESEND_SETTINGS)) as load,
        patch("db_supabase.find_one", AsyncMock(return_value={"email": "rider@example.com"})),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
        patch("boto3.client", MagicMock()) as boto_factory,
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(
            to="rider@example.com", subject="x", text="hi", email_type="receipt", recipient_user_id="u1"
        )

    assert ok is False
    # Neither provider is touched, and settings aren't even loaded.
    load.assert_not_awaited()
    boto_factory.assert_not_called()
    resend_client.post.assert_not_awaited()
    # Logged as suppressed.
    assert inserts and inserts[0][0] == "email_send_log"
    assert inserts[0][1]["status"] == "suppressed"
    assert inserts[0][1]["email_type"] == "receipt"
    assert inserts[0][1]["recipient_user_id"] == "u1"


@pytest.mark.anyio
async def test_send_log_written_on_ses_success():
    factory, _ses = _boto3_mock()
    inserts: list = []

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),
        patch("boto3.client", factory),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        ok = await send_transactional_email(
            to="r@example.com", subject="x", html="<p>h</p>", email_type="receipt", recipient_user_id="u1"
        )

    assert ok is True
    assert inserts and inserts[0][0] == "email_send_log"
    row = inserts[0][1]
    assert row["provider"] == "ses"
    assert row["status"] == "sent"
    assert row["message_id"] == "msg-123"
    assert row["email_type"] == "receipt"
    assert row["recipient_user_id"] == "u1"


@pytest.mark.anyio
async def test_send_log_records_resend_provider_on_fallback():
    factory, _ses = _boto3_mock(send_side_effect=RuntimeError("SES down"))
    resend_client = _async_client_mock()
    resend_client.post = AsyncMock(return_value=_mock_resp(202))
    resend_client.post.return_value.json = lambda: {"id": "resend-abc"}
    inserts: list = []

    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS, **_RESEND_SETTINGS)),
        patch("boto3.client", factory),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
        patch("httpx.AsyncClient", return_value=resend_client),
    ):
        ok = await send_transactional_email(to="r@example.com", subject="x", text="hi")

    assert ok is True
    row = inserts[0][1]
    assert row["provider"] == "resend"
    assert row["status"] == "sent"
    assert row["message_id"] == "resend-abc"


@pytest.mark.anyio
async def test_failed_send_logged_as_failed():
    inserts: list = []
    with (
        patch("settings_loader.get_app_settings", _settings()),  # nothing configured
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        ok = await send_transactional_email(to="r@example.com", subject="x", text="hi")

    assert ok is False
    row = inserts[0][1]
    assert row["provider"] == "none"
    assert row["status"] == "failed"
    assert row["error_detail"] is None  # unconfigured, not a provider error


@pytest.mark.anyio
async def test_failed_send_persists_ses_error_detail():
    """A configured-but-failing SES send (with no Resend fallback configured)
    must persist the actual provider error to email_send_log, not just
    provider='none'/status='failed' with no diagnosable detail.

    This is the real production shape found while investigating the
    corporate-portal OTP send: every attempt since it shipped logged
    provider='none'/status='failed' with nothing else to go on, requiring
    live app-log/Sentry access neither this session nor on-call always has
    to find the actual SES rejection reason.
    """
    factory, _ = _boto3_mock(send_side_effect=RuntimeError("MessageRejected: identity not verified"))
    inserts: list = []
    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),  # SES only, no Resend
        patch("boto3.client", factory),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        ok = await send_transactional_email(to="r@example.com", subject="x", text="hi")

    assert ok is False
    row = inserts[0][1]
    assert row["provider"] == "none"
    assert row["status"] == "failed"
    assert row["error_detail"] is not None
    assert "MessageRejected" in row["error_detail"]


@pytest.mark.anyio
async def test_suppression_lookup_error_fails_open():
    factory, ses_client = _boto3_mock()
    with (
        patch("settings_loader.get_app_settings", _settings(**_SES_SETTINGS)),
        patch("boto3.client", factory),
        patch("db_supabase.find_one", AsyncMock(side_effect=RuntimeError("db down"))),
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
    ):
        ok = await send_transactional_email(to="r@example.com", subject="x", text="hi")

    # Fail-open: a suppression-lookup error must not block the send.
    assert ok is True
    ses_client.send_raw_email.assert_called_once()


@pytest.mark.anyio
async def test_settings_load_error_fails_open_not_raises():
    """An app_settings read failure (DB hiccup) must degrade to "no provider
    configured" like every other failure branch here, not propagate.

    Regression: send_transactional_email is documented to return bool and
    never raise — every one of its 12 call sites (including the corporate
    portal's send_company_email_otp) calls it unwrapped on that assumption.
    Before this fix, a transient get_app_settings() failure on the *second*
    read inside this function (the first, already-guarded read in the OTP
    endpoint can succeed off the 60s cache while this one misses it) bubbled
    up as a raw exception — surfacing to the corporate-portal caller as a 500
    Internal Server Error instead of the existing, already-handled "could not
    send verification code" 502.
    """
    inserts: list = []
    with (
        patch("settings_loader.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        ok = await send_transactional_email(to="r@example.com", subject="x", text="hi")

    assert ok is False
    row = inserts[0][1]
    assert row["provider"] == "none"
    assert row["status"] == "failed"
