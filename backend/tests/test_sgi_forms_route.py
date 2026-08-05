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


# ── regulator removal queue + filing audit ──────────────────────────────────
#
# Spinr stops dispatching the moment a driver deletes their account, but SGI
# goes on listing them as an active passenger-for-hire driver until the D00032
# removal row is filed (and their vehicle until D00033). Nothing used to
# trigger, surface, or record that filing.

_DELETED_DRIVER_ROW = {
    **_DRIVER_ROW,
    "deleted_at": "2026-07-20T12:00:00Z",
    "regulator_removal_required": True,
    "regulator_removal_effective_date": "2026-07-20",
}


def _generate(admin_client, action, form_type="driver_details", driver_row=None, update_side=None):
    updates = []

    async def _update_one(table, filters, payload):
        updates.append((table, filters, payload))
        if update_side:
            update_side()

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[driver_row or _DELETED_DRIVER_ROW])),
        patch("backend.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
        patch("backend.services.data_transfer.sgi_form_filler.fill_vehicle_details_form", return_value=b"%PDF-1.4"),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": form_type, "driver_ids": ["user-1"], "action": action},
        )
    return resp, updates


def test_remove_stamps_the_driver_form_column(admin_client):
    resp, updates = _generate(admin_client, "remove", "driver_details")
    assert resp.status_code == 200
    driver_writes = [u for u in updates if u[0] == "drivers"]
    assert len(driver_writes) == 1
    _, filters, payload = driver_writes[0]
    assert filters == {"id": "driver-record-1"}
    assert payload["regulator_removal_driver_form_at"]
    assert payload["regulator_removal_reported_by"] == "admin-1"
    # The vehicle form is filed separately and is still outstanding.
    assert "regulator_removal_vehicle_form_at" not in payload


def test_remove_stamps_the_vehicle_form_column_separately(admin_client):
    """A driver needs BOTH forms. Stamping one shared 'reported' column off
    whichever was generated first would mark the job done with the vehicle
    still on SGI's books."""
    resp, updates = _generate(admin_client, "remove", "vehicle_details")
    assert resp.status_code == 200
    payload = [u for u in updates if u[0] == "drivers"][0][2]
    assert payload["regulator_removal_vehicle_form_at"]
    assert "regulator_removal_driver_form_at" not in payload


def test_add_action_does_not_stamp_a_removal(admin_client):
    resp, updates = _generate(admin_client, "add")
    assert resp.status_code == 200
    assert [u for u in updates if u[0] == "drivers"] == []


def test_stamp_failure_still_returns_the_pdf(admin_client):
    """The PDF is already built. Failing the request would make the admin
    re-generate — and re-file — the form that just succeeded."""

    def _boom():
        raise RuntimeError("db down")

    resp, _ = _generate(admin_client, "remove", update_side=_boom)
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4"


def test_removal_queue_lists_outstanding_forms(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DELETED_DRIVER_ROW])):
        resp = admin_client.get("/api/admin/data-transfer/sgi-forms/removal-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    entry = body["drivers"][0]
    # users.id — the id the generate endpoint and the rest of Data Transfer take.
    assert entry["entity_id"] == "user-1"
    assert entry["effective_date"] == "2026-07-20"
    assert entry["driver_form_outstanding"] is True
    assert entry["vehicle_form_outstanding"] is True


def test_removal_queue_hides_fully_filed_drivers_by_default(admin_client):
    filed = {
        **_DELETED_DRIVER_ROW,
        "regulator_removal_driver_form_at": "2026-07-25T00:00:00Z",
        "regulator_removal_vehicle_form_at": "2026-07-25T00:00:00Z",
    }
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[filed])):
        resp = admin_client.get("/api/admin/data-transfer/sgi-forms/removal-queue")
    assert resp.json()["count"] == 0

    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[filed])):
        resp = admin_client.get("/api/admin/data-transfer/sgi-forms/removal-queue?include_filed=true")
    assert resp.json()["count"] == 1


def test_removal_queue_keeps_partially_filed_drivers(admin_client):
    half = {**_DELETED_DRIVER_ROW, "regulator_removal_driver_form_at": "2026-07-25T00:00:00Z"}
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[half])):
        resp = admin_client.get("/api/admin/data-transfer/sgi-forms/removal-queue")
    entry = resp.json()["drivers"][0]
    assert entry["driver_form_outstanding"] is False
    assert entry["vehicle_form_outstanding"] is True


def test_removal_queue_flags_drivers_with_no_linked_user(admin_client):
    """Without a users.id the driver cannot be selected in the Data Transfer
    flow, so the queue entry would never clear — surface it, don't drop it."""
    orphan = {**_DELETED_DRIVER_ROW, "user_id": None}
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[orphan])):
        resp = admin_client.get("/api/admin/data-transfer/sgi-forms/removal-queue")
    assert resp.json()["unresolvable"] == 1


# ---------------------------------------------------------------------------
# POST /sgi-forms/documents — supporting-document bundle
# ---------------------------------------------------------------------------

_DOC_REASON = "Q3 SGI driver eligibility submission"
_DOCS_PATH = "/api/admin/data-transfer/sgi-forms/documents"


def _bundle(status="included", content=b"SCAN"):
    return {
        "entity_type": "driver",
        "entity_id": "user-1",
        "user": {"id": "user-1"},
        "driver_profile": {"id": "driver-record-1"},
        "notification_preferences": [],
        "rides": [],
        "documents": [
            {
                "id": "doc-bg",
                "document_type": "background_check",
                "_storage_key": "bg.jpg",
                "_content": content,
                "_content_status": status,
            }
        ],
        "driver_insurance_periods": [],
    }


def test_documents_endpoint_returns_a_zip_containing_the_scan(admin_client):
    """The reported gap: the SGI tab could only produce filled PDFs, never the
    drivers' actual documents. This endpoint is what closes that."""
    import zipfile
    from io import BytesIO

    with (
        patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[dict(_DRIVER_ROW)])),
        patch(
            "backend.routes.admin.sgi_forms.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=[_bundle()]),
        ),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock()),
    ):
        resp = admin_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": _DOC_REASON})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(BytesIO(resp.content))
    doc_files = [n for n in zf.namelist() if "/documents/" in n]
    assert len(doc_files) == 1
    assert zf.read(doc_files[0]) == b"SCAN"
    assert resp.headers["X-Documents-Included"] == "1"
    assert resp.headers["X-Documents-Listed"] == "1"


def test_documents_endpoint_requests_files_not_just_metadata(admin_client):
    """Regression guard for the original bug: asking for the documents but not
    their bytes yields a metadata-only ZIP, which is what sent this whole
    investigation sideways. doc_file_types must be populated."""
    gather = AsyncMock(return_value=[_bundle()])
    with (
        patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[dict(_DRIVER_ROW)])),
        patch("backend.routes.admin.sgi_forms.entity_export_service.gather_entity_bundles", gather),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock()),
    ):
        admin_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": _DOC_REASON})

    kwargs = gather.await_args.kwargs
    assert kwargs["doc_file_types"], "files must be requested, not just metadata rows"
    assert "background_check" in kwargs["doc_file_types"]
    # An SGI eligibility package has no business carrying trip coordinates.
    assert kwargs["include_ride_gps"] is False


def test_documents_endpoint_reports_a_missing_scan_in_the_headers(admin_client):
    """A document that couldn't be retrieved must be counted, not silently
    absent — the admin is filing this with a regulator."""
    with (
        patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[dict(_DRIVER_ROW)])),
        patch(
            "backend.routes.admin.sgi_forms.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=[_bundle(status="unavailable_fetch_failed", content=None)]),
        ),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock()),
    ):
        resp = admin_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": _DOC_REASON})

    assert resp.status_code == 200
    assert resp.headers["X-Documents-Listed"] == "1"
    assert resp.headers["X-Documents-Included"] == "0"


def test_documents_endpoint_requires_a_reason(admin_client):
    resp = admin_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": "short"})
    assert resp.status_code == 422


def test_documents_endpoint_rejects_an_empty_selection(admin_client):
    resp = admin_client.post(_DOCS_PATH, json={"driver_ids": [], "reason": _DOC_REASON})
    assert resp.status_code == 400


def test_documents_endpoint_caps_the_batch_size(admin_client):
    from backend.routes.admin.sgi_forms import MAX_DOCUMENT_BUNDLE_DRIVERS

    too_many = [f"user-{n}" for n in range(MAX_DOCUMENT_BUNDLE_DRIVERS + 1)]
    resp = admin_client.post(_DOCS_PATH, json={"driver_ids": too_many, "reason": _DOC_REASON})
    assert resp.status_code == 422
    assert str(MAX_DOCUMENT_BUNDLE_DRIVERS) in resp.json()["detail"]


def test_documents_endpoint_blocks_non_sgi_drivers(admin_client):
    """Same hard block as form generation — a non-SGI driver's documents must
    not be assembled into an SGI submission package either."""
    alberta = {**_DRIVER_ROW, "regulatory_authority": "AB"}
    with patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[alberta])):
        resp = admin_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": _DOC_REASON})

    assert resp.status_code == 422
    assert "not SGI" in resp.json()["detail"]


def test_documents_endpoint_404s_when_no_driver_resolves(admin_client):
    with patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[])):
        resp = admin_client.post(_DOCS_PATH, json={"driver_ids": ["nope"], "reason": _DOC_REASON})
    assert resp.status_code == 404


def test_documents_endpoint_requires_admin_auth(test_client):
    resp = test_client.post(_DOCS_PATH, json={"driver_ids": ["user-1"], "reason": _DOC_REASON})
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /sgi-forms/package — the actual SGI submission
# ---------------------------------------------------------------------------

_PKG_PATH = "/api/admin/data-transfer/sgi-forms/package"
_PKG_REASON = "Q3 SGI driver eligibility filing"


def _check_bundle(entity_id="user-1", content=b"CRC-PDF", key="crc.pdf"):
    return {
        "entity_type": "driver",
        "entity_id": entity_id,
        "user": {"id": entity_id},
        "driver_profile": {"id": "driver-record-1"},
        "notification_preferences": [],
        "rides": [],
        "documents": (
            [
                {
                    "id": "doc-bg",
                    "document_type": "Background Check",
                    "_storage_key": key,
                    "_content": content,
                    "_content_status": "included" if content else "unavailable_fetch_failed",
                }
            ]
            if content is not None
            else []
        ),
        "driver_insurance_periods": [],
    }


def _patch_package(bundles, driver_rows=None):
    rows = driver_rows if driver_rows is not None else [dict(_DRIVER_ROW)]
    return (
        patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=rows)),
        patch("backend.routes.admin.sgi_forms._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.routes.admin.sgi_forms.sgi_form_filler.fill_driver_details_form", lambda rows: b"%PDF-D00032"),
        patch("backend.routes.admin.sgi_forms.sgi_form_filler.fill_vehicle_details_form", lambda rows: b"%PDF-D00033"),
        patch(
            "backend.routes.admin.sgi_forms.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=bundles),
        ),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock()),
    )


def test_package_contains_both_forms_and_the_criminal_record_check(admin_client):
    """What actually gets emailed to SGI: D00032, D00033, and the driver's
    criminal record check — and nothing else."""
    import zipfile
    from io import BytesIO

    p = _patch_package([_check_bundle()])
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        resp = admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})

    assert resp.status_code == 200
    names = zipfile.ZipFile(BytesIO(resp.content)).namelist()
    assert "SGI_D00032_Driver_Details.pdf" in names
    assert "SGI_D00033_Vehicle_Details.pdf" in names
    assert "criminal_record_checks/Jane_Driver_background_check.pdf" in names
    # No data-transfer scaffolding — this is a filing, not a bundle.
    assert not [n for n in names if n.endswith(".csv")]
    assert not [n for n in names if n.endswith("raw_data.json")]
    assert "README.txt" not in names


def test_package_keeps_a_jpeg_check_as_a_jpeg(admin_client):
    """The file is not re-encoded — a regulator-facing document should be
    exactly what the driver submitted."""
    import zipfile
    from io import BytesIO

    p = _patch_package([_check_bundle(content=b"\xff\xd8\xff JPEG", key="crc.jpg")])
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        resp = admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})

    zf = zipfile.ZipFile(BytesIO(resp.content))
    assert "criminal_record_checks/Jane_Driver_background_check.jpg" in zf.namelist()
    assert zf.read("criminal_record_checks/Jane_Driver_background_check.jpg") == b"\xff\xd8\xff JPEG"


def test_package_names_drivers_missing_a_check_inside_the_zip(admin_client):
    """A driver on the forms with no clearance on file must not be discovered
    by SGI — it is called out in the ZIP and in the headers."""
    import zipfile
    from io import BytesIO

    p = _patch_package([_check_bundle(content=None)])
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        resp = admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})

    assert resp.headers["X-Checks-Missing"] == "1"
    assert resp.headers["X-Checks-Included"] == "0"
    zf = zipfile.ZipFile(BytesIO(resp.content))
    assert "MISSING_CRIMINAL_RECORD_CHECKS.txt" in zf.namelist()
    assert "Jane_Driver" in zf.read("MISSING_CRIMINAL_RECORD_CHECKS.txt").decode()
    # The forms are still produced — the filing is not blocked, just flagged.
    assert "SGI_D00032_Driver_Details.pdf" in zf.namelist()


def test_package_requests_only_the_background_check(admin_client):
    """Data minimization: a submission needs clearance proof, not the driver's
    whole document file."""
    gather = AsyncMock(return_value=[_check_bundle()])
    p = _patch_package([])
    with (
        p[0],
        p[1],
        p[2],
        p[3],
        patch("backend.routes.admin.sgi_forms.entity_export_service.gather_entity_bundles", gather),
        p[5],
    ):
        admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})

    kwargs = gather.await_args.kwargs
    assert kwargs["doc_types"] == ["background_check"]
    assert kwargs["doc_file_types"] == ["background_check"]
    assert kwargs["include_ride_gps"] is False


def test_package_blocks_non_sgi_drivers(admin_client):
    alberta = {**_DRIVER_ROW, "regulatory_authority": "AB"}
    with patch("backend.routes.admin.sgi_forms.db_supabase.get_rows", AsyncMock(return_value=[alberta])):
        resp = admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})
    assert resp.status_code == 422
    assert "not SGI" in resp.json()["detail"]


def test_package_requires_a_reason(admin_client):
    resp = admin_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": "short"})
    assert resp.status_code == 422


def test_package_rejects_an_empty_selection(admin_client):
    resp = admin_client.post(_PKG_PATH, json={"driver_ids": [], "reason": _PKG_REASON})
    assert resp.status_code == 400


def test_package_requires_admin_auth(test_client):
    resp = test_client.post(_PKG_PATH, json={"driver_ids": ["user-1"], "reason": _PKG_REASON})
    assert resp.status_code in (401, 403)


def test_package_splits_forms_past_their_row_limits(admin_client):
    """D00032 holds 10 driver rows and D00033 holds 16 — a larger selection
    becomes consecutive documents rather than being refused."""
    import zipfile
    from io import BytesIO

    rows = [{**_DRIVER_ROW, "id": f"drv-{n}", "user_id": f"user-{n}", "name": f"D {n}"} for n in range(12)]
    p = _patch_package([], driver_rows=rows)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        resp = admin_client.post(
            _PKG_PATH, json={"driver_ids": [f"user-{n}" for n in range(12)], "reason": _PKG_REASON}
        )

    names = zipfile.ZipFile(BytesIO(resp.content)).namelist()
    assert "SGI_D00032_Driver_Details_1.pdf" in names  # 12 drivers > 10 rows
    assert "SGI_D00032_Driver_Details_2.pdf" in names
    assert "SGI_D00033_Vehicle_Details.pdf" in names  # 12 <= 16, single doc
