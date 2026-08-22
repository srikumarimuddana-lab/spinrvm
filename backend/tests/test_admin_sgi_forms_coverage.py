"""Supplementary coverage for routes/admin/sgi_forms.py, layered on top of
the existing test_sgi_forms_route.py (which already exercises the happy
paths for both form types, the remove-stamp block, the fill-failure 502,
and the removal-queue endpoint).

This file targets branches / argument shapes that file leaves unasserted:
  - explicit observability.record_sgi_form_result call args (success + both
    form types' failure path, not just driver_details)
  - the `action` field's pydantic default ("add") when omitted entirely
  - pydantic-level 422s for invalid form_type / action values
  - the remove-stamp loop actually iterating once per driver (multi-driver)
  - audit log call args (resource_id join, details payload) on success
  - `_out_of_scope_drivers` exercised directly as a unit, incl. dedup of the
    `authorities` sorted-set message when two out-of-scope drivers share one
    authority
  - the driver-details filename/content-disposition header
  - GET removal-queue requires admin auth

Regulatory-adjacent, admin-only endpoint (JWT admin trust model — CLAUDE.md:
admin JWTs are fully trusted, not re-derived from a table)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.admin.sgi_forms import _out_of_scope_drivers

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_DRIVER_ROW = {
    "id": "driver-record-1",
    "user_id": "user-1",
    "name": "Jane Driver",
    "license_number": "vault:abc123",
    # ACTION_ITEMS.md B13 round 2: the segregation guard now blocks a NULL
    # regulatory_authority too, so fixtures representing a normal in-scope
    # driver need it set explicitly — a bare-row fixture without it would
    # now trip the out-of-scope 422 on every test that doesn't care about
    # the guard specifically.
    "regulatory_authority": "SGI",
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


# ── _out_of_scope_drivers unit tests (no HTTP layer needed) ────────────────


def test_out_of_scope_drivers_empty_list():
    assert _out_of_scope_drivers([]) == []


def test_out_of_scope_drivers_all_sgi_returns_empty():
    rows = [{"regulatory_authority": "SGI"}, {"regulatory_authority": "SGI"}]
    assert _out_of_scope_drivers(rows) == []


def test_out_of_scope_drivers_null_and_missing_are_now_blocked():
    # ACTION_ITEMS.md B13 (round 2, 2026-08-22): the NULL-passes grandfather
    # allowance is retired now that the backfill is confirmed complete and
    # every driver write path sets regulatory_authority — a NULL row is
    # treated the same as any other non-SGI authority.
    rows = [{"regulatory_authority": None}, {}]
    assert _out_of_scope_drivers(rows) == rows


def test_out_of_scope_drivers_filters_non_sgi_and_null():
    sk = {"regulatory_authority": "SGI"}
    ab = {"regulatory_authority": "AMVIC"}
    null = {"regulatory_authority": None}
    assert _out_of_scope_drivers([sk, ab, null]) == [ab, null]


# ── observability call-arg assertions ───────────────────────────────────────


def test_success_records_completed_metric_for_driver_details(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
            return_value={"field": "value"},
        ),
        patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
        patch("backend.services.data_transfer.observability.record_sgi_form_result") as record,
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 200
    record.assert_called_once_with("driver_details", "completed")


def test_vehicle_details_fill_failure_returns_502(admin_client):
    """The 502 path is already covered for driver_details in
    test_sgi_forms_route.py; the vehicle_details branch (lines 128-131 ->
    exception) is a separate code path and wasn't exercised there."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_vehicle_details_row",
            side_effect=RuntimeError("bad vehicle field map"),
        ),
        patch("backend.services.data_transfer.observability.record_sgi_form_result") as record,
        patch("backend.services.data_transfer.observability.capture_failure") as capture,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "vehicle_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 502
    # B-P2-1 (utils/error_handling.py): 5xx HTTPException details are
    # sanitized to a generic message unless they match the ERR_* sentinel
    # pattern — "Could not generate the SGI form" isn't a sentinel, so the
    # client sees "Internal server error" even though the route raised the
    # more specific message (which still hits the server log).
    assert resp.json()["detail"] == "Internal server error"
    record.assert_called_once_with("vehicle_details", "failed")
    capture.assert_called_once()
    # contexts payload (positional arg 3) carries the admin id, form type,
    # count, and stringified error for the Sentry event.
    contexts = capture.call_args.args[2]
    assert contexts["admin_id"] == "admin-1"
    assert contexts["form_type"] == "vehicle_details"
    assert contexts["driver_count"] == 1
    assert "bad vehicle field map" in contexts["error"]


# ── pydantic request-model validation (form_type / action) ─────────────────


def test_invalid_form_type_rejected_422(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/sgi-forms/generate",
        json={"form_type": "insurance_details", "driver_ids": ["user-1"]},
    )
    assert resp.status_code == 422


def test_invalid_action_rejected_422(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/sgi-forms/generate",
        json={"form_type": "driver_details", "driver_ids": ["user-1"], "action": "delete"},
    )
    assert resp.status_code == 422


def test_action_defaults_to_add_when_omitted(admin_client):
    """SgiFormRequest.action defaults to 'add' — confirm the remove-stamp
    block (guarded on action == 'remove') is skipped when the caller never
    sends `action` at all, not just when it's explicitly 'add'."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])) as get_rows,
        patch("backend.db_supabase.update_one", AsyncMock()) as update_one,
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
            return_value={"field": "value"},
        ),
        patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")) as log_action,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 200
    update_one.assert_not_called()
    # Audit log records the default action.
    assert log_action.call_args.args[4]["action"] == "add"
    get_rows.assert_called_once()


# ── multi-driver remove-stamp loop + audit log args ─────────────────────────


def test_remove_stamps_each_driver_once(admin_client):
    """The stamp loop (lines 166-179) iterates per driver_row — confirm it
    fires once per driver, not once total, for a multi-driver batch."""
    d1 = {**_DRIVER_ROW, "id": "driver-1", "user_id": "user-1", "name": "Amy"}
    d2 = {**_DRIVER_ROW, "id": "driver-2", "user_id": "user-2", "name": "Bo"}
    updates = []

    async def _update_one(table, filters, payload):
        updates.append((table, filters, payload))

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[d1, d2])),
        patch("backend.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
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
            json={"form_type": "driver_details", "driver_ids": ["user-1", "user-2"], "action": "remove"},
        )
    assert resp.status_code == 200
    driver_writes = [u for u in updates if u[0] == "drivers"]
    assert len(driver_writes) == 2
    stamped_ids = {u[1]["id"] for u in driver_writes}
    assert stamped_ids == {"driver-1", "driver-2"}
    for _, _, payload in driver_writes:
        assert payload["regulator_removal_reported_by"] == "admin-1"


def test_success_audit_log_records_requested_ids_and_metadata(admin_client):
    """log_admin_action is called with the *requested* driver_ids (joined),
    not the resolved driver_row ids — confirms the resource_id string and
    details payload shape."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch(
            "backend.services.data_transfer.sgi_field_maps.driver_to_driver_details_row",
            return_value={"field": "value"},
        ),
        patch("backend.services.data_transfer.sgi_form_filler.fill_driver_details_form", return_value=b"%PDF-1.4"),
        patch("backend.routes.admin.sgi_forms.log_admin_action", AsyncMock(return_value="aud-1")) as log_action,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"], "action": "add"},
        )
    assert resp.status_code == 200
    args = log_action.call_args.args
    assert args[0] == _ADMIN
    assert args[1] == "sgi_form_generated"
    assert args[2] == "drivers"
    assert args[3] == "user-1"  # ",".join(body.driver_ids)
    assert args[4] == {"form_type": "driver_details", "driver_count": 1, "action": "add"}


def test_driver_details_content_disposition_header(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_DRIVER_ROW])),
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
    assert resp.headers["content-type"] == "application/pdf"
    disposition = resp.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert "SGI_D00032_Driver_Details.pdf" in disposition


# ── removal-queue auth guard ────────────────────────────────────────────────


def test_removal_queue_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/data-transfer/sgi-forms/removal-queue")
    assert resp.status_code in (401, 403)


# ── found-not-fixed: authorities-message dedup relies on a set, so two
# out-of-scope drivers sharing one non-SGI authority collapse to a single
# mention in the error detail even though the count (`len(out_of_scope)`)
# still reflects both drivers. Not a functional bug (the count is correct
# and the message is still accurate — "2 drivers... AMVIC"), but it means
# the message doesn't enumerate distinct authorities per driver; pinning
# the actual behavior here rather than assuming an off-by-one in the copy. ─


def test_out_of_scope_message_dedups_repeated_authority_but_count_is_per_driver(admin_client):
    ab1 = {**_DRIVER_ROW, "user_id": "user-1", "regulatory_authority": "AMVIC"}
    ab2 = {**_DRIVER_ROW, "user_id": "user-2", "regulatory_authority": "AMVIC"}
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[ab1, ab2])):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1", "user-2"]},
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith("2 of the selected drivers are regulated by AMVIC, not SGI")
    # AMVIC appears exactly once in the authorities list despite 2 drivers.
    assert detail.count("AMVIC") == 1


def test_out_of_scope_message_labels_null_authority_as_unspecified(admin_client):
    # ACTION_ITEMS.md B13 round 2: a NULL regulatory_authority is now blocked
    # too (see test_out_of_scope_drivers_null_and_missing_are_now_blocked) —
    # this pins that the 422 detail renders something readable instead of the
    # literal string "None" when that happens.
    null_row = {**_DRIVER_ROW, "user_id": "user-1", "regulatory_authority": None}
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[null_row])):
        resp = admin_client.post(
            "/api/admin/data-transfer/sgi-forms/generate",
            json={"form_type": "driver_details", "driver_ids": ["user-1"]},
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "unspecified" in detail
    assert "None" not in detail
