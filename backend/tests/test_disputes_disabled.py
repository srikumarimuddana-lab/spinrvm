"""In-app disputes are disabled (owner decision 2026-09-25).

POST /api/v1/disputes and PUT /api/admin/disputes/{id}/resolve answer 410
before any DB read/write, Zoho ticket, Stripe refund or push. The read-only
GET endpoints stay so old clients and the historical admin view keep working.
See docs/change-log/2026-09-25-disable-in-app-disputes.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import routes.disputes as d

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": []}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def side_effects(monkeypatch):
    """Every side effect the old handlers had; each must stay uncalled."""
    mocks = {
        "get_rows": AsyncMock(return_value=[]),
        "get_ride": AsyncMock(return_value=None),
        "insert_one": AsyncMock(),
        "update_one": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(d.db_supabase, name, mock)
    mocks["push"] = AsyncMock()
    mocks["zoho"] = AsyncMock()
    mocks["spawn"] = AsyncMock()
    mocks["settings"] = AsyncMock(return_value={})
    monkeypatch.setattr(d, "send_push_notification", mocks["push"])
    monkeypatch.setattr(d, "create_ticket_for_dispute", mocks["zoho"])
    monkeypatch.setattr(d, "_spawn", mocks["spawn"])
    monkeypatch.setattr(d, "get_app_settings", mocks["settings"])
    return mocks


def _assert_no_side_effects(mocks):
    called = sorted(name for name, mock in mocks.items() if mock.called)
    assert called == [], f"disabled endpoint had side effects: {called}"


def test_user_create_dispute_is_410_with_support_email(test_client, side_effects):
    resp = test_client.post(
        "/api/v1/disputes",
        json={"ride_id": "ride-1", "reason": "earnings_issue", "description": "x"},
    )
    assert resp.status_code == 410, resp.text
    detail = resp.json()["detail"]
    # Structured so the 4xx PII scrubber doesn't redact the support address.
    assert detail["code"] == "IN_APP_DISPUTES_DISABLED"
    assert detail["message"] == (
        "In-app disputes are not available. For questions about a charge, "
        "email support@spinr.ca or contact your card issuer."
    )
    _assert_no_side_effects(side_effects)


def test_user_create_dispute_is_410_even_with_old_or_empty_body(test_client, side_effects):
    # Old builds may send any shape; the answer is 410, never a 422.
    resp = test_client.post("/api/v1/disputes", json={})
    assert resp.status_code == 410, resp.text
    _assert_no_side_effects(side_effects)


def test_admin_resolve_is_410_and_moves_no_money(test_client, app_fixture, side_effects):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    resp = test_client.put(
        "/api/admin/disputes/d1/resolve",
        json={"resolution": "approved", "refund_amount": 10, "admin_note": "goodwill"},
    )
    assert resp.status_code == 410, resp.text
    assert "read-only" in resp.text
    _assert_no_side_effects(side_effects)


@pytest.mark.parametrize(("modules", "status"), [(["support"], 403), (["disputes"], 410)])
def test_admin_resolve_keeps_disputes_module_gate(test_client, app_fixture, side_effects, modules, status):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: {"id": "admin-2", "role": "admin", "modules": modules}
    resp = test_client.put("/api/admin/disputes/d1/resolve", json={"resolution": "rejected"})
    assert resp.status_code == status, resp.text
    _assert_no_side_effects(side_effects)


def test_user_read_endpoints_still_served(test_client, app_fixture, side_effects):
    """GET /disputes stays so an old build's history screen doesn't break."""
    from dependencies import get_current_user

    app_fixture.dependency_overrides[get_current_user] = lambda: {"id": "rider-1", "role": "rider"}
    resp = test_client.get("/api/v1/disputes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    resp = test_client.get("/api/v1/disputes/d-missing")
    assert resp.status_code == 404
