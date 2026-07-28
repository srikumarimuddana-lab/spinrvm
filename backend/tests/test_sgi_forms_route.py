"""HTTP-level (TestClient) tests for routes/admin/sgi_forms.py — no test
file existed for this route before. Covers the entity_id regression fixed
alongside the Data Transfer search 500 bug: `driver_ids` must resolve
against `drivers.user_id`, not `drivers.id`, since every other consumer in
the module (search's default/mixed mode, entity_export_service) treats
entity_id as `users.id`. Selecting a driver via the driver-scoped search
mode previously made SGI form generation work by accident (it also
returned drivers.id) while breaking export; fixing search to return
users.id everywhere required this route to change what it looks up by.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_DRIVER_ROW = {
    "id": "driver-record-1",
    "user_id": "user-1",
    "name": "Jane Driver",
    "license_number": "vault:abc123",
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def test_sgi_forms_requires_admin_auth(test_client):
    resp = test_client.post(
        "/api/admin/data-transfer/sgi-forms/generate",
        json={"form_type": "driver_details", "driver_ids": ["user-1"]},
    )
    assert resp.status_code in (401, 403)


def test_no_drivers_selected_returns_400(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/sgi-forms/generate",
        json={"form_type": "driver_details", "driver_ids": []},
    )
    assert resp.status_code == 400


def test_over_row_limit_returns_422(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/sgi-forms/generate",
        json={"form_type": "driver_details", "driver_ids": [f"u{i}" for i in range(11)]},
    )
    assert resp.status_code == 422


def test_driver_lookup_resolves_by_user_id_not_driver_id(admin_client):
    """The regression itself: passing a users.id in driver_ids must resolve
    via drivers.user_id — confirms the query filter key, not just that the
    endpoint returns 200 for some arbitrary mock shape."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])) as get_rows,
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
            return_value={"field": "value"},
        ),
        patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4"
    called_table, called_filters = get_rows.call_args.args[0], get_rows.call_args.args[1]
    assert called_table == "drivers"
    assert called_filters == {"user_id": {"$in": ["user-1"]}}


def test_no_matching_drivers_returns_404(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 404


def test_fill_failure_returns_502(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
            side_effect=RuntimeError("bad field map"),
        ),
        patch("backend.services.data_transfer.observability.capture_failure") as capture,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 502
    capture.assert_called_once()


class TestRegulatorySegregationGuard:
    """Alberta-expansion safety guard: an SGI (Saskatchewan) form must never
    be generated for a driver regulated by a different authority. See
    ACTION_ITEMS.md B13 for the NULL-passes grandfather reasoning."""

    def test_out_of_province_driver_rejected(self, admin_client):
        ab_driver = {**_DRIVER_ROW, "regulatory_authority": "AMVIC", "regulatory_region": "AB"}
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[ab_driver])):
            resp = admin_client.post(
                "/api/admin/data-transfer/sgi-forms/generate",
                json={"form_type": "driver_details", "driver_ids": ["user-1"]},
            )
        assert resp.status_code == 422
        assert "AMVIC" in resp.json()["detail"]

    def test_mixed_batch_rejected_even_if_some_are_sgi(self, admin_client):
        sk_driver = {**_DRIVER_ROW, "user_id": "user-1", "regulatory_authority": "SGI"}
        ab_driver = {**_DRIVER_ROW, "user_id": "user-2", "regulatory_authority": "AMVIC"}
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[sk_driver, ab_driver])):
            resp = admin_client.post(
                "/api/admin/data-transfer/sgi-forms/generate",
                json={"form_type": "driver_details", "driver_ids": ["user-1", "user-2"]},
            )
        assert resp.status_code == 422

    def test_sgi_driver_explicitly_tagged_passes(self, admin_client):
        sk_driver = {**_DRIVER_ROW, "regulatory_authority": "SGI", "regulatory_region": "SK"}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[sk_driver])),
            patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch(
                "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
                return_value={"field": "value"},
            ),
            patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
            patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
        ):
            resp = admin_client.post(
                "/api/admin/data-transfer/sgi-forms/generate",
                json={"form_type": "driver_details", "driver_ids": ["user-1"]},
            )
        assert resp.status_code == 200

    def test_null_regulatory_authority_grandfathered_through(self, admin_client):
        """Deliberate allowance, not an oversight — 22 real production
        drivers predate this field's backfill (ACTION_ITEMS.md B13)."""
        legacy_driver = {**_DRIVER_ROW, "regulatory_authority": None, "regulatory_region": None}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[legacy_driver])),
            patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch(
                "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
                return_value={"field": "value"},
            ),
            patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
            patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
        ):
            resp = admin_client.post(
                "/api/admin/data-transfer/sgi-forms/generate",
                json={"form_type": "driver_details", "driver_ids": ["user-1"]},
            )
        assert resp.status_code == 200


def test_vehicle_details_form_type(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_vehicle_details_row",
            return_value={"field": "value"},
        ),
        patch("backend.services.data_transfer.sgi_form_filler.fill_vehicle_details_form", return_value=b"%PDF-1.4"),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "vehicle_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 200
    assert "D00033" in resp.headers.get("content-disposition", "")
