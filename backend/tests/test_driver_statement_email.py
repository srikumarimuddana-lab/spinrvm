"""Tests for the driver self-serve statement email endpoint
(routes/drivers/tax_exports.email_driver_statement)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

DRIVER_USER_ID = "user_stmt_email"


def _user(email="drv@example.com"):
    return {"id": DRIVER_USER_ID, "email": email, "first_name": "Har", "last_name": "S"}


def _statement():
    return {
        "period_type": "weekly",
        "period_start": "2026-07-20",
        "period_label": "Jul 20 - 26, 2026",
        "trips": 3,
        "earnings": {"total": "100.00"},
        "payouts_total": "50.00",
    }


@pytest.mark.asyncio
class TestEmailDriverStatement:
    async def test_schedules_email_and_returns_message(self):
        from backend.routes.drivers.tax_exports import email_driver_statement

        bg = BackgroundTasks()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "drv-1", "user_id": DRIVER_USER_ID}]),
            ),
            patch(
                "backend.utils.driver_statement.build_statement",
                AsyncMock(return_value=_statement()),
            ) as build,
        ):
            result = await email_driver_statement(
                background_tasks=bg,
                period_type="weekly",
                period_start="2026-07-20",
                current_user=_user(),
            )

        assert "Jul 20 - 26, 2026" in result["message"]
        assert len(bg.tasks) == 1
        assert build.call_args.kwargs["driver_name"] == "Har S"

    async def test_invalid_period_type_is_422(self):
        from backend.routes.drivers.tax_exports import email_driver_statement

        with pytest.raises(HTTPException) as exc:
            await email_driver_statement(
                background_tasks=BackgroundTasks(),
                period_type="daily",
                period_start="2026-07-20",
                current_user=_user(),
            )
        assert exc.value.status_code == 422

    async def test_misaligned_weekly_anchor_is_422(self):
        from backend.routes.drivers.tax_exports import email_driver_statement

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(return_value=[{"id": "drv-1", "user_id": DRIVER_USER_ID}]),
        ):
            with pytest.raises(HTTPException) as exc:
                await email_driver_statement(
                    background_tasks=BackgroundTasks(),
                    period_type="weekly",
                    period_start="2026-07-21",  # Tuesday
                    current_user=_user(),
                )
        assert exc.value.status_code == 422

    async def test_no_email_on_file_is_400(self):
        from backend.routes.drivers.tax_exports import email_driver_statement

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(return_value=[{"id": "drv-1", "user_id": DRIVER_USER_ID}]),
        ):
            with pytest.raises(HTTPException) as exc:
                await email_driver_statement(
                    background_tasks=BackgroundTasks(),
                    period_type="weekly",
                    period_start="2026-07-20",
                    current_user=_user(email=None),
                )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
class TestEmailStatementBackgroundTask:
    async def test_renders_pdf_and_emails_attachment(self):
        from backend.routes.drivers import tax_exports

        with (
            patch("backend.utils.driver_statement_pdf.generate_driver_statement_pdf", return_value=b"%PDF-x") as gen,
            patch.object(tax_exports, "_email_driver_document", AsyncMock()) as sender,
        ):
            await tax_exports._email_statement_document(DRIVER_USER_ID, "drv@example.com", _statement())

        gen.assert_called_once()
        kwargs = sender.call_args.kwargs
        assert kwargs["filename"] == "spinr-statement-weekly-2026-07-20.pdf"
        assert kwargs["content"] == b"%PDF-x"
        assert kwargs["log_id"] == "stmt"

    async def test_render_failure_skips_email_and_logs(self):
        from backend.routes.drivers import tax_exports

        with (
            patch(
                "backend.utils.driver_statement_pdf.generate_driver_statement_pdf",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(tax_exports, "_email_driver_document", AsyncMock()) as sender,
        ):
            await tax_exports._email_statement_document(DRIVER_USER_ID, "drv@example.com", _statement())

        sender.assert_not_awaited()
