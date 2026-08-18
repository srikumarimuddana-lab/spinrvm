"""C23 Action item 3: GET /api/admin/disputes/chargebacks (routes/admin/support.py's
admin_get_chargebacks). Mirrors test_admin_support_routes.py's fixture pattern for
the sibling `/disputes` endpoints; kept in its own file since this is a distinct
data source (`stripe_disputes`, not `disputes`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import routes.admin.support as m

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": []}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def _set_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def client(test_client):
    return test_client


def test_authz_denied_without_admin(client):
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code in (401, 403)


def test_registered_before_dispute_id_path_param(client, _set_admin, monkeypatch):
    """Regression pin: `/disputes/chargebacks` must not be swallowed by the
    `/disputes/{dispute_id}` route. A get_rows call against `stripe_disputes`
    (not `disputes`) proves the chargebacks handler ran, not the details one."""
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 200
    assert get_rows.call_args.args[0] == "stripe_disputes"


def test_filters_status(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/disputes/chargebacks", params={"status": "needs_response"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {"status": "needs_response"}


def test_status_all_is_not_filtered(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/disputes/chargebacks", params={"status": "all"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {}


def test_db_error_surfaces_503_not_swallowed(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=Exception("db down")))
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 503


def test_empty_result_skips_ride_enrichment(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 200
    assert resp.json() == []
    get_rows.assert_awaited_once()  # only the stripe_disputes call, no rides lookup


def test_enriches_ride_code_and_computes_days_remaining(client, _set_admin, monkeypatch):
    now = datetime.now(timezone.utc)
    due_by = (now + timedelta(days=2)).isoformat()
    dispute_row = {
        "id": "sd-1",
        "stripe_dispute_id": "dp_1",
        "ride_id": "ride-1",
        "amount_cents": 5000,
        "reason": "fraudulent",
        "status": "needs_response",
        "evidence_due_by": due_by,
        "evidence_submitted_at": None,
        "created_at": now.isoformat(),
    }
    ride_row = {"id": "ride-1", "ride_code": "SPN-123"}

    async def _get_rows(table, filters, **kwargs):
        if table == "stripe_disputes":
            return [dispute_row]
        if table == "rides":
            return [ride_row]
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ride_code"] == "SPN-123"
    assert body[0]["days_remaining"] in (1, 2)  # tz-rounding tolerance


def test_evidence_already_submitted_has_no_days_remaining(client, _set_admin, monkeypatch):
    now = datetime.now(timezone.utc)
    dispute_row = {
        "id": "sd-2",
        "stripe_dispute_id": "dp_2",
        "ride_id": None,
        "amount_cents": 1000,
        "reason": "duplicate",
        "status": "under_review",
        "evidence_due_by": (now + timedelta(days=1)).isoformat(),
        "evidence_submitted_at": now.isoformat(),
        "created_at": now.isoformat(),
    }
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=[dispute_row]))
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["days_remaining"] is None
    assert body[0]["ride_code"] is None


def test_malformed_due_by_does_not_raise(client, _set_admin, monkeypatch):
    dispute_row = {
        "id": "sd-3",
        "stripe_dispute_id": "dp_3",
        "ride_id": None,
        "amount_cents": 1000,
        "reason": "product_not_received",
        "status": "needs_response",
        "evidence_due_by": "not-a-timestamp",
        "evidence_submitted_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=[dispute_row]))
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 200
    assert resp.json()[0]["days_remaining"] is None


def test_ride_enrichment_db_error_surfaces_503(client, _set_admin, monkeypatch):
    dispute_row = {
        "id": "sd-4",
        "stripe_dispute_id": "dp_4",
        "ride_id": "ride-4",
        "amount_cents": 1000,
        "reason": "fraudulent",
        "status": "needs_response",
        "evidence_due_by": None,
        "evidence_submitted_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }

    async def _get_rows(table, filters, **kwargs):
        if table == "stripe_disputes":
            return [dispute_row]
        raise Exception("rides table unreachable")

    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = client.get("/api/admin/disputes/chargebacks")
    assert resp.status_code == 503
