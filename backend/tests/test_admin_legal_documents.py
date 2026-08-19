"""Unit tests for admin legal-document (ToS / Privacy Policy) CRUD.

PIPEDA-relevant: material changes to consent-governing documents (ToS,
Privacy Policy) bump the per-(audience, doc_type) version counter that the
rider/driver apps use to detect "material change → re-consent required".
A bug here (e.g. failing to bump version, or upserting the wrong row) is a
consent-tracking correctness issue, not just a content bug.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "admin"}

try:
    from routes.admin.legal_documents import (
        admin_list_legal_documents,
        admin_upsert_legal_document,
    )
except ImportError:
    from backend.routes.admin.legal_documents import (  # type: ignore[no-redef]
        admin_list_legal_documents,
        admin_upsert_legal_document,
    )


# ---------------------------------------------------------------------------
# GET /legal-documents
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_legal_documents_returns_rows():
    rows = [
        {"id": "1", "audience": "rider", "doc_type": "tos", "content": "v1", "version": 1},
        {"id": "2", "audience": "driver", "doc_type": "privacy", "content": "v1", "version": 1},
    ]
    with patch(
        "routes.admin.legal_documents.db_supabase.get_rows",
        AsyncMock(return_value=rows),
    ):
        out = await admin_list_legal_documents()
    assert out == rows


@pytest.mark.anyio
async def test_list_legal_documents_empty_returns_list_not_none():
    with patch(
        "routes.admin.legal_documents.db_supabase.get_rows",
        AsyncMock(return_value=None),
    ):
        out = await admin_list_legal_documents()
    assert out == []


# ---------------------------------------------------------------------------
# PUT /legal-documents — validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upsert_rejects_bad_audience():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await admin_upsert_legal_document({"audience": "bogus", "type": "tos", "content": "x"})
    assert ei.value.status_code == 400
    assert "audience" in ei.value.detail


@pytest.mark.anyio
async def test_upsert_rejects_bad_type():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await admin_upsert_legal_document({"audience": "rider", "type": "eula", "content": "x"})
    assert ei.value.status_code == 400
    assert "type" in ei.value.detail


@pytest.mark.anyio
async def test_upsert_rejects_missing_audience():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await admin_upsert_legal_document({"type": "tos", "content": "x"})


# ---------------------------------------------------------------------------
# PUT /legal-documents — version bump semantics (PIPEDA consent tracking)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upsert_creates_new_row_at_version_1():
    """No existing (audience, doc_type) row → insert at version 1."""
    find_one_mock = AsyncMock(return_value=None)
    insert_mock = AsyncMock(return_value={"id": "new-id"})
    audit_mock = AsyncMock(return_value="audit-1")

    with (
        patch("routes.admin.legal_documents.db_supabase.find_one", find_one_mock),
        patch("routes.admin.legal_documents.db_supabase.insert_one", insert_mock),
        patch("routes.admin.legal_documents.log_admin_action", audit_mock),
    ):
        out = await admin_upsert_legal_document(
            {"audience": "rider", "type": "tos", "content": "Terms v1"}, admin=_ADMIN
        )

    assert out == {"audience": "rider", "type": "tos", "version": 1, "id": "new-id"}
    find_one_mock.assert_awaited_once_with("legal_documents", {"audience": "rider", "doc_type": "tos"})
    insert_args = insert_mock.await_args.args
    assert insert_args[0] == "legal_documents"
    inserted_row = insert_args[1]
    assert inserted_row["audience"] == "rider"
    assert inserted_row["doc_type"] == "tos"
    assert inserted_row["content"] == "Terms v1"
    assert inserted_row["version"] == 1
    audit_mock.assert_awaited_once_with(
        _ADMIN,
        "legal_document_created",
        "legal_documents",
        "new-id",
        {"audience": "rider", "doc_type": "tos", "version": 1},
    )


@pytest.mark.anyio
async def test_upsert_bumps_version_on_existing_row():
    """Existing row at version 3 → update to version 4, same row id."""
    existing = {"id": "row-1", "audience": "driver", "doc_type": "privacy", "version": 3}
    update_mock = AsyncMock(return_value=None)
    audit_mock = AsyncMock(return_value="audit-2")

    with (
        patch("routes.admin.legal_documents.db_supabase.find_one", AsyncMock(return_value=existing)),
        patch("routes.admin.legal_documents.db_supabase.update_one", update_mock),
        patch("routes.admin.legal_documents.log_admin_action", audit_mock),
    ):
        out = await admin_upsert_legal_document(
            {"audience": "driver", "type": "privacy", "content": "Privacy v4"}, admin=_ADMIN
        )

    assert out == {"audience": "driver", "type": "privacy", "version": 4}
    update_args = update_mock.await_args.args
    assert update_args[0] == "legal_documents"
    assert update_args[1] == {"id": "row-1"}
    updated_fields = update_args[2]
    assert updated_fields["content"] == "Privacy v4"
    assert updated_fields["version"] == 4
    assert "updated_at" in updated_fields
    audit_mock.assert_awaited_once_with(
        _ADMIN,
        "legal_document_updated",
        "legal_documents",
        "row-1",
        {"audience": "driver", "doc_type": "privacy", "version": 4},
    )


@pytest.mark.anyio
async def test_upsert_treats_missing_version_as_1_then_bumps_to_2():
    """A legacy row with no `version` column populated must not error — the
    upsert coerces it to 1 before incrementing, so it lands on 2."""
    existing = {"id": "row-legacy", "audience": "rider", "doc_type": "tos"}  # no version key
    update_mock = AsyncMock(return_value=None)

    with (
        patch("routes.admin.legal_documents.db_supabase.find_one", AsyncMock(return_value=existing)),
        patch("routes.admin.legal_documents.db_supabase.update_one", update_mock),
        patch("routes.admin.legal_documents.log_admin_action", AsyncMock(return_value="audit-3")),
    ):
        out = await admin_upsert_legal_document(
            {"audience": "rider", "type": "tos", "content": "Terms v2"}, admin=_ADMIN
        )

    assert out["version"] == 2
    assert update_mock.await_args.args[2]["version"] == 2


@pytest.mark.anyio
async def test_upsert_accepts_doc_type_alias_key():
    """Body may use `doc_type` instead of `type` — both are accepted."""
    with (
        patch("routes.admin.legal_documents.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("routes.admin.legal_documents.db_supabase.insert_one", AsyncMock(return_value={"id": "x"})),
        patch("routes.admin.legal_documents.log_admin_action", AsyncMock(return_value="audit-4")),
    ):
        out = await admin_upsert_legal_document(
            {"audience": "driver", "doc_type": "tos", "content": "Driver ToS"}, admin=_ADMIN
        )
    assert out["type"] == "tos"
    assert out["audience"] == "driver"


@pytest.mark.anyio
async def test_upsert_defaults_missing_content_to_empty_string():
    insert_mock = AsyncMock(return_value={"id": "x"})
    with (
        patch("routes.admin.legal_documents.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("routes.admin.legal_documents.db_supabase.insert_one", insert_mock),
        patch("routes.admin.legal_documents.log_admin_action", AsyncMock(return_value="audit-5")),
    ):
        await admin_upsert_legal_document({"audience": "rider", "type": "privacy"}, admin=_ADMIN)
    assert insert_mock.await_args.args[1]["content"] == ""
