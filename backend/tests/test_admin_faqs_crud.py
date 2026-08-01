"""Coverage for admin FAQ CRUD + notification endpoints (routes/admin/faqs.py).

Lower risk than promotions.py (content management, not fare-affecting), but
still prioritizes write endpoints and error/branch paths over the trivial
GET /faqs list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app", "modules": ["support"]}


@pytest.fixture
def test_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _faq_payload(**overrides):
    payload = {
        "question": "How do I book a ride?",
        "answer": "Tap Book Now.",
        "category": "general",
        "audience": "rider",
    }
    payload.update(overrides)
    return payload


# ---------- create ----------


def test_create_faq_success(test_client):
    with patch("backend.db_supabase.insert_one", new=AsyncMock(return_value={"id": "faq-1"})) as mock_insert:
        resp = test_client.post("/api/admin/faqs", json=_faq_payload())

    assert resp.status_code == 200
    assert resp.json() == {"faq_id": "faq-1"}
    doc = mock_insert.call_args[0][1]
    assert doc["question"] == "How do I book a ride?"
    assert doc["audience"] == "rider"


def test_create_faq_requires_valid_audience(test_client):
    resp = test_client.post("/api/admin/faqs", json=_faq_payload(audience="everyone"))
    assert resp.status_code == 422


def test_create_faq_falls_back_to_generated_id_when_row_missing_id(test_client):
    with patch("backend.db_supabase.insert_one", new=AsyncMock(return_value=None)):
        resp = test_client.post("/api/admin/faqs", json=_faq_payload())
    assert resp.status_code == 200
    assert resp.json()["faq_id"]  # a uuid string was generated


# ---------- update ----------


def test_update_faq_partial_fields_only(test_client):
    with patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update:
        resp = test_client.put("/api/admin/faqs/faq-1", json={"question": "New question?"})

    assert resp.status_code == 200
    updates = mock_update.call_args[0][2]
    assert updates["question"] == "New question?"
    assert "answer" not in updates
    # editing the question invalidates the stored embedding
    assert updates["embedding"] is None
    assert updates["embedding_model"] is None


def test_update_faq_service_area_ids_explicit_null_clears_scope(test_client):
    with patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update:
        resp = test_client.put("/api/admin/faqs/faq-1", json={"service_area_ids": None})

    assert resp.status_code == 200
    updates = mock_update.call_args[0][2]
    assert "service_area_ids" in updates
    assert updates["service_area_ids"] is None


def test_update_faq_omitted_service_area_ids_not_touched(test_client):
    with patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update:
        resp = test_client.put("/api/admin/faqs/faq-1", json={"category": "billing"})

    assert resp.status_code == 200
    updates = mock_update.call_args[0][2]
    assert "service_area_ids" not in updates


def test_update_faq_no_changes_skips_write(test_client):
    with patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update:
        resp = test_client.put("/api/admin/faqs/faq-1", json={})

    assert resp.status_code == 200
    mock_update.assert_not_awaited()


# ---------- delete ----------


def test_delete_faq(test_client):
    with patch("backend.db_supabase.delete_many", new=AsyncMock()) as mock_delete:
        resp = test_client.delete("/api/admin/faqs/faq-1")

    assert resp.status_code == 200
    assert resp.json() == {"message": "FAQ deleted"}
    mock_delete.assert_awaited_once_with("faqs", {"id": "faq-1"})


# ---------- list ----------


def test_list_faqs_excludes_embedding_column(test_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows:
        resp = test_client.get("/api/admin/faqs")

    assert resp.status_code == 200
    assert "embedding" not in mock_get_rows.call_args.kwargs["columns"]


# ---------- notifications: send ----------


def test_send_notification_to_specific_user(test_client):
    with patch("backend.db_supabase.insert_one", new=AsyncMock(return_value={"id": "n-1"})) as mock_insert:
        resp = test_client.post(
            "/api/admin/notifications/send",
            json={"user_id": "user-1", "title": "Hi", "body": "Hello there"},
        )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_insert.assert_awaited_once()
    assert mock_insert.call_args[0][1]["sent_count"] == 1


def test_send_notification_broadcast_all(test_client):
    send_mock = AsyncMock()
    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "u1"}, {"id": "u2"}])),
        patch("backend.features.send_push_notification", new=send_mock),
    ):
        resp = test_client.post(
            "/api/admin/notifications/send",
            json={"title": "Promo", "body": "New feature!", "audience": "all"},
        )

    assert resp.status_code == 200
    assert send_mock.await_count == 2


def test_send_notification_broadcast_riders(test_client):
    send_mock = AsyncMock()
    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "u1"}])) as mock_get_rows,
        patch("backend.features.send_push_notification", new=send_mock),
    ):
        resp = test_client.post(
            "/api/admin/notifications/send",
            json={"title": "Rider update", "body": "Body", "audience": "riders"},
        )

    assert resp.status_code == 200
    assert mock_get_rows.call_args[0][1] == {"is_rider": True}
    send_mock.assert_awaited_once_with("u1", "Rider update", "Body")


def test_send_notification_broadcast_drivers(test_client):
    send_mock = AsyncMock()
    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "d1"}])) as mock_get_rows,
        patch("backend.features.send_push_notification", new=send_mock),
    ):
        resp = test_client.post(
            "/api/admin/notifications/send",
            json={"title": "Driver update", "body": "Body", "audience": "drivers"},
        )

    assert resp.status_code == 200
    assert mock_get_rows.call_args[0][1] == {"is_driver": True}
    send_mock.assert_awaited_once_with("d1", "Driver update", "Body")


# ---------- notifications: list ----------


def test_get_notifications_with_filters(test_client):
    async def fake_get_rows(table, filters=None, **kwargs):
        if kwargs.get("limit") == 1:
            return [{"created_at": "2026-07-01T00:00:00Z"}]
        return [{"id": "n-1", "status": "sent", "type": "general"}]

    with patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)):
        resp = test_client.get("/api/admin/notifications", params={"status": "sent", "notification_type": "general"})

    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "n-1"


def test_get_notifications_falls_back_to_sent_at_ordering_when_no_created_at(test_client):
    async def fake_get_rows(table, filters=None, **kwargs):
        if kwargs.get("limit") == 1:
            return []  # no rows -> no created_at key -> falls back to sent_at
        assert kwargs.get("order") == "sent_at"
        return []

    with patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)):
        resp = test_client.get("/api/admin/notifications")

    assert resp.status_code == 200
    assert resp.json() == []
