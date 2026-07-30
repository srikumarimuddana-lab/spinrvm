"""Endpoint tests for the admin driver-statements routes (list / pdf / email)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

DRIVER = {"id": "drv-1", "user_id": "u1", "name": "Row Name"}
USER = {"id": "u1", "first_name": "Har", "last_name": "S", "email": "drv@example.com"}


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _statement(period_type="weekly"):
    return {
        "period_type": period_type,
        "period_start": "2026-07-20",
        "period_end": "2026-07-26",
        "period_label": "Jul 20 - 26, 2026",
        "trips": 3,
        "earnings": {"total": "100.00"},
        "payouts_total": "50.00",
    }


def _db(rows_by_table):
    async def _get_rows(table, filters=None, **kw):
        return rows_by_table.get(table, [])

    return _get_rows


def _patches(rows_by_table, statement=None, custom=None):
    return (
        patch("routes.admin.driver_statements.db_supabase.get_rows", AsyncMock(side_effect=_db(rows_by_table))),
        patch("routes.admin.driver_statements.db_supabase.get_user_by_id", AsyncMock(return_value=USER)),
        patch(
            "routes.admin.driver_statements.build_statement",
            AsyncMock(return_value=statement or _statement()),
        ),
        patch(
            "routes.admin.driver_statements.build_custom_statement",
            AsyncMock(return_value=custom or _statement("custom")),
        ),
        patch("routes.admin.driver_statements.generate_driver_statement_pdf", return_value=b"%PDF-admin"),
        patch("routes.admin.driver_statements.log_admin_action", AsyncMock(return_value="audit-1")),
        patch("routes.admin.driver_statements.send_email", AsyncMock(return_value=True)),
    )


def test_list_statements(test_client, super_admin_override):
    ledger = [
        {
            "id": "stmt-weekly-2026-07-20-drv-1",
            "period_type": "weekly",
            "period_start": "2026-07-20",
            "period_end": "2026-07-26",
            "status": "sent",
            "totals": {"payouts_total": "50.00"},
            "email_sent_at": "2026-07-27T12:00:00+00:00",
            "created_at": "2026-07-27T12:00:00+00:00",
        }
    ]
    ps = _patches({"drivers": [DRIVER], "driver_statements": ledger})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = test_client.get("/api/admin/drivers/drv-1/statements")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["statements"][0]["status"] == "sent"
    assert body["statements"][0]["period_start"] == "2026-07-20"


def test_download_pdf_anchored_period(test_client, super_admin_override):
    ps = _patches({"drivers": [DRIVER]})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5] as audit, ps[6]:
        resp = test_client.get(
            "/api/admin/drivers/drv-1/statements/pdf",
            params={"period_type": "weekly", "period_start": "2026-07-20"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-admin"
    assert resp.headers["content-type"] == "application/pdf"
    assert "spinr-statement-weekly-2026-07-20" in resp.headers["content-disposition"]
    audit.assert_awaited_once()


def test_download_pdf_custom_date_range(test_client, super_admin_override):
    ps = _patches({"drivers": [DRIVER]})
    with ps[0], ps[1], ps[2], ps[3] as custom, ps[4], ps[5], ps[6]:
        resp = test_client.get(
            "/api/admin/drivers/drv-1/statements/pdf",
            params={"start": "2026-07-01", "end": "2026-07-15"},
        )
    assert resp.status_code == 200, resp.text
    from datetime import date

    assert custom.call_args.args[1] == date(2026, 7, 1)
    assert custom.call_args.args[2] == date(2026, 7, 15)


def test_download_pdf_requires_a_selection(test_client, super_admin_override):
    ps = _patches({"drivers": [DRIVER]})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = test_client.get("/api/admin/drivers/drv-1/statements/pdf")
    assert resp.status_code == 422


def test_download_pdf_driver_not_found(test_client, super_admin_override):
    ps = _patches({"drivers": []})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = test_client.get(
            "/api/admin/drivers/ghost/statements/pdf",
            params={"period_type": "weekly", "period_start": "2026-07-20"},
        )
    assert resp.status_code == 404


def test_email_statement_to_driver(test_client, super_admin_override):
    ps = _patches({"drivers": [DRIVER]})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5] as audit, ps[6] as send:
        resp = test_client.post(
            "/api/admin/drivers/drv-1/statements/email",
            params={"period_type": "weekly", "period_start": "2026-07-20"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"sent": True, "period_label": "Jul 20 - 26, 2026"}
    kwargs = send.call_args.kwargs
    assert kwargs["to"] == "drv@example.com"
    assert kwargs["attachments"][0]["content"] == b"%PDF-admin"
    audit.assert_awaited_once()


def test_email_provider_rejection_is_502(test_client, super_admin_override):
    ps = _patches({"drivers": [DRIVER]})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], patch(
        "routes.admin.driver_statements.send_email", AsyncMock(return_value=False)
    ):
        resp = test_client.post(
            "/api/admin/drivers/drv-1/statements/email",
            params={"period_type": "weekly", "period_start": "2026-07-20"},
        )
    assert resp.status_code == 502
