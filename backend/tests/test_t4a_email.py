"""
Driver tax-document email delivery (no in-app download).

Tax documents and earnings exports are delivered by email only — the driver
app no longer downloads them. These tests pin:

  POST /drivers/t4a/{year}/email          — schedules the T4A PDF email
  POST /drivers/earnings/export/email     — schedules the earnings CSV email
  _email_t4a_document  — renders the PDF and attaches it to send_email
  _email_earnings_csv  — attaches the CSV (utf-8 bytes) to send_email

Both endpoints return immediately and require an email address on file (400
otherwise — a phone-number fallback would leak PII to the email provider).

Run:
    pytest backend/tests/test_t4a_email.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

# Both endpoints are @tax_doc_email_limit (6/hour) in production. The conftest
# autouse `reset_rate_limiters` fixture sets default_limiter.enabled = False, so
# these direct handler calls pass request=None and skip the limiter (firing
# behaviour is not unit-testable under that harness — same as every other
# @limiter.limit endpoint in the suite).

DRIVER_USER_ID = "driver_user_t4a_email"
DRIVER_ID = "driver_row_t4a_email"
DRIVER_EMAIL = "driver@example.com"


def _driver_row(**extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "gst_registered": False,
        "gst_bn": "",
        **extra,
    }


def _ride_row(earnings: float = 20.00) -> dict:
    return {
        "id": "ride-001",
        "driver_id": DRIVER_ID,
        "status": "completed",
        "driver_earnings": earnings,
        "tip_amount": 0,
    }


def _user(email: str | None = DRIVER_EMAIL) -> dict:
    return {"id": DRIVER_USER_ID, "email": email, "first_name": "Pat", "last_name": "Driver"}


# ─────────────────────────────────────────────────────────────────────────────
# POST /drivers/t4a/{year}/email
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestEmailT4ASummary:
    async def test_schedules_email_and_returns_message(self):
        from backend.routes.drivers import email_t4a_summary

        bg = BackgroundTasks()
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rides_for_driver",
                AsyncMock(return_value=[_ride_row()]),
            ),
        ):
            result = await email_t4a_summary(year=2025, background_tasks=bg, current_user=_user())

        assert "email" in result["message"].lower()
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func.__name__ == "_email_t4a_document"
        # (user_id, email, year, summary)
        assert task.args[1] == DRIVER_EMAIL
        assert task.args[2] == 2025

    async def test_no_email_on_file_raises_400(self):
        from fastapi import HTTPException

        from backend.routes.drivers import email_t4a_summary

        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as exc_info:
            await email_t4a_summary(year=2025, background_tasks=bg, current_user=_user(email=None))

        assert exc_info.value.status_code == 400
        assert bg.tasks == []


# ─────────────────────────────────────────────────────────────────────────────
# POST /drivers/earnings/export/email
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestEmailEarningsExport:
    async def test_schedules_csv_email(self):
        from backend.routes.drivers import email_earnings_export

        bg = BackgroundTasks()
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rides_for_driver",
                AsyncMock(return_value=[_ride_row()]),
            ),
        ):
            result = await email_earnings_export(year=2025, background_tasks=bg, current_user=_user())

        assert "email" in result["message"].lower()
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func.__name__ == "_email_earnings_csv"
        assert task.args[1] == DRIVER_EMAIL
        assert task.args[2] == 2025
        # The CSV payload is the export's "data" string.
        assert "Total Earnings" in task.args[3]

    async def test_no_email_on_file_raises_400(self):
        from fastapi import HTTPException

        from backend.routes.drivers import email_earnings_export

        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as exc_info:
            await email_earnings_export(year=2025, background_tasks=bg, current_user=_user(email=None))

        assert exc_info.value.status_code == 400
        assert bg.tasks == []


# ─────────────────────────────────────────────────────────────────────────────
# Background tasks — attachment shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestEmailBackgroundTasks:
    async def test_t4a_document_emails_pdf_attachment(self):
        from backend.routes.drivers import _email_t4a_document

        summary = {"year": 2025, "total_earnings": "70.00", "total_trips": 3}
        with (
            patch("backend.routes.drivers._deps.generate_t4a_pdf", return_value=b"%PDF-1.4 fake"),
            patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
        ):
            await _email_t4a_document(DRIVER_USER_ID, DRIVER_EMAIL, 2025, summary)

        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        assert kwargs["to"] == DRIVER_EMAIL
        att = kwargs["attachments"][0]
        assert att["filename"].endswith(".pdf")
        assert att["mime"] == "application/pdf"
        assert att["content"] == b"%PDF-1.4 fake"

    async def test_csv_emails_utf8_bytes_attachment(self):
        from backend.routes.drivers import _email_earnings_csv

        csv_data = "Year,Total Earnings\n2025,70.00"
        with patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send:
            await _email_earnings_csv(DRIVER_USER_ID, DRIVER_EMAIL, 2025, csv_data)

        send.assert_awaited_once()
        att = send.await_args.kwargs["attachments"][0]
        assert att["filename"].endswith(".csv")
        assert att["mime"] == "text/csv"
        assert att["content"] == csv_data.encode("utf-8")

    async def test_t4a_pdf_render_failure_skips_email(self):
        """A PDF render failure logs and returns — no email is attempted."""
        from backend.routes.drivers import _email_t4a_document

        with (
            patch("backend.routes.drivers._deps.generate_t4a_pdf", side_effect=RuntimeError("bad font")),
            patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
        ):
            # Must not raise.
            await _email_t4a_document(DRIVER_USER_ID, DRIVER_EMAIL, 2025, {"year": 2025})

        send.assert_not_awaited()

    async def test_send_failure_is_logged_not_raised(self):
        """A provider failure must not escape the background task (already off-request)."""
        from backend.routes.drivers import _email_earnings_csv

        with patch("backend.routes.drivers._deps.send_email", AsyncMock(side_effect=RuntimeError("ses down"))):
            # Must not raise.
            await _email_earnings_csv(DRIVER_USER_ID, DRIVER_EMAIL, 2025, "Year\n2025")
