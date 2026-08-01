"""Coverage for admin promotion CRUD/management endpoints (routes/admin/promotions.py).

Money-adjacent: promo codes feed directly into fare discounting, so the
create-validation branches (negative/oversized discount_value) and the
update/delete audit-logging paths are prioritized over the read-only
list/usage/stats endpoints (list is covered lightly here; stats already has
dedicated coverage in test_admin_promo_stats.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app", "modules": ["promotions"]}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _base_payload(**overrides):
    payload = {
        "code": "save10",
        "description": "10 off",
        "promo_type": "discount",
        "discount_type": "flat",
        "discount_value": 10,
    }
    payload.update(overrides)
    return payload


# ---------- create: validation guards (money-adjacent) ----------


def test_create_rejects_negative_discount_value(admin_client):
    resp = admin_client.post("/api/admin/promotions", json=_base_payload(discount_value=-5))
    assert resp.status_code == 400
    assert "negative" in resp.json()["detail"]


def test_create_rejects_percentage_over_100(admin_client):
    resp = admin_client.post(
        "/api/admin/promotions",
        json=_base_payload(discount_type="percentage", discount_value=150),
    )
    assert resp.status_code == 400
    assert "100" in resp.json()["detail"]


def test_create_rejects_flat_over_500(admin_client):
    resp = admin_client.post(
        "/api/admin/promotions",
        json=_base_payload(discount_type="flat", discount_value=501),
    )
    assert resp.status_code == 400
    assert "500" in resp.json()["detail"]


@pytest.mark.parametrize("field", ["max_discount", "total_budget", "referrer_reward", "min_ride_fare"])
def test_create_rejects_negative_optional_money_fields(admin_client, field):
    resp = admin_client.post("/api/admin/promotions", json=_base_payload(**{field: -1}))
    assert resp.status_code == 400
    assert field in resp.json()["detail"]


# ---------- create: happy path ----------


def test_create_promotion_success_uppercases_code_and_audits(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value={"id": "promo-1"})) as mock_insert,
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.post("/api/admin/promotions", json=_base_payload(code="save10"))

    assert resp.status_code == 200
    assert resp.json() == {"promotion_id": "promo-1"}
    doc = mock_insert.call_args[0][1]
    assert doc["code"] == "SAVE10"
    mock_audit.assert_awaited_once()
    audit_args = mock_audit.call_args[0]
    assert audit_args[1] == "promotion_created"
    assert audit_args[3] == "promo-1"


def test_create_promotion_falls_back_when_optional_fields_rejected(admin_client):
    """If inserting with optional (possibly-missing-column) fields fails, retry
    without them rather than surfacing a 500 to the admin."""

    calls = []

    async def fake_insert(table, doc):
        calls.append(doc)
        if len(calls) == 1:
            raise Exception("column assigned_user_ids does not exist")
        return {"id": "promo-2"}

    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(side_effect=fake_insert)),
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()),
    ):
        resp = admin_client.post("/api/admin/promotions", json=_base_payload(code="fallback"))

    assert resp.status_code == 200
    assert resp.json() == {"promotion_id": "promo-2"}
    assert len(calls) == 2
    # Second (fallback) attempt must not include the optional fields.
    assert "assigned_user_ids" not in calls[1]


# ---------- update ----------


def test_update_promotion_with_changes_audits_updated_fields(admin_client):
    with (
        patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update,
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.put("/api/admin/promotions/promo-1", json={"discount_value": 20})

    assert resp.status_code == 200
    assert resp.json() == {"message": "Promotion updated"}
    mock_update.assert_awaited_once()
    assert mock_update.call_args[0][1] == {"id": "promo-1"}
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args[0][1] == "promotion_updated"


def test_update_promotion_no_fields_skips_write_and_audit(admin_client):
    with (
        patch("backend.db_supabase.update_one", new=AsyncMock()) as mock_update,
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.put("/api/admin/promotions/promo-1", json={})

    assert resp.status_code == 200
    mock_update.assert_not_awaited()
    mock_audit.assert_not_awaited()


def test_update_promotion_retries_without_optional_fields_on_failure(admin_client):
    calls = []

    async def fake_update(table, match, updates):
        calls.append(dict(updates))
        if len(calls) == 1:
            raise Exception("column min_total_rides does not exist")
        return None

    with (
        patch("backend.db_supabase.update_one", new=AsyncMock(side_effect=fake_update)),
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()),
    ):
        resp = admin_client.put(
            "/api/admin/promotions/promo-1",
            json={"discount_value": 15, "min_total_rides": 3},
        )

    assert resp.status_code == 200
    assert len(calls) == 2
    assert "min_total_rides" not in calls[1]
    assert calls[1]["discount_value"] == 15


# ---------- delete ----------


def test_delete_promotion_audits(admin_client):
    with (
        patch("backend.db_supabase.delete_many", new=AsyncMock()) as mock_delete,
        patch("backend.routes.admin.promotions.log_admin_action", new=AsyncMock()) as mock_audit,
    ):
        resp = admin_client.delete("/api/admin/promotions/promo-1")

    assert resp.status_code == 200
    assert resp.json() == {"message": "Promotion deleted"}
    mock_delete.assert_awaited_once_with("promotions", {"id": "promo-1"})
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args[0][1] == "promotion_deleted"


# ---------- list (filters) ----------


def test_list_promotions_applies_status_and_search_filters(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows:
        resp = admin_client.get(
            "/api/admin/promotions",
            params={"promo_type": "public", "status": "active", "search": "SAVE"},
        )

    assert resp.status_code == 200
    filters = mock_get_rows.call_args[0][1]
    assert filters["promo_type"] == {"$ne": "private"}
    assert filters["is_active"] is True
    assert "$and" in filters  # status $or combined with search $or via $and


def test_list_promotions_expired_filter(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[])) as mock_get_rows:
        resp = admin_client.get("/api/admin/promotions", params={"status": "expired"})

    assert resp.status_code == 200
    filters = mock_get_rows.call_args[0][1]
    assert "$lt" in filters["expiry_date"]


# ---------- usage ----------


def test_promo_usage_enriches_with_user_name_and_ride_id(admin_client):
    applications = [
        {"id": "app-1", "promo_id": "promo-1", "user_id": "user-1", "created_at": "2026-07-01T00:00:00Z"},
    ]
    users_result = type(
        "R", (), {"data": [{"id": "user-1", "first_name": "Ann", "last_name": "Lee", "phone": "306"}]}
    )()
    rides_result = type("R", (), {"data": [{"id": "ride-1", "promo_application_id": "app-1"}]})()

    async def fake_get_rows(table, *a, **kw):
        if table == "promo_applications":
            return list(applications)
        return []

    async def fake_run_sync(fn):
        # Called twice: once for users, once for rides. Disambiguate by return type.
        return fn()

    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)),
        patch("backend.db_supabase.run_sync", new=AsyncMock(side_effect=[users_result, rides_result])),
    ):
        resp = admin_client.get("/api/admin/promotions/usage")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["user_name"] == "Ann Lee"
    assert body[0]["ride_id"] == "ride-1"


def test_promo_usage_empty_returns_empty_list_without_enrichment(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(return_value=[])):
        resp = admin_client.get("/api/admin/promotions/usage")
    assert resp.status_code == 200
    assert resp.json() == []


def test_promo_usage_missing_table_returns_empty_list(admin_client):
    with patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=Exception("relation does not exist"))):
        resp = admin_client.get("/api/admin/promotions/usage")
    assert resp.status_code == 200
    assert resp.json() == []


def test_promo_usage_date_range_filtering(admin_client):
    applications = [
        {"id": "app-1", "promo_id": "p", "user_id": "u", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "app-2", "promo_id": "p", "user_id": "u", "created_at": "2026-07-01T00:00:00Z"},
    ]

    async def fake_get_rows(table, *a, **kw):
        if table == "promo_applications":
            return list(applications)
        return []

    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)),
        patch("backend.db_supabase.run_sync", new=AsyncMock(return_value=type("R", (), {"data": []})())),
    ):
        resp = admin_client.get(
            "/api/admin/promotions/usage", params={"date_from": "2026-06-01", "date_to": "2026-07-31"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "app-2"
