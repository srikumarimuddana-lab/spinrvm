"""ACTION_ITEMS.md B14: approving a driver's licence document must require
license_number/license_class to already be on the driver row.

Before this gate, an admin could approve a driver's uploaded licence photo
while those two structured columns stayed NULL forever — nothing forced them
in, and nothing surfaced the gap. That is exactly how the original 22-driver
backfill gap (see B14) accumulated. This is a *going-forward* gate only: it
must never reject a driver who already has both fields on file (the common
case), and must never block approval of any other document type.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import routes.admin.documents as admin_docs

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_LICENSE_DOC = {
    "id": "doc-license-1",
    "driver_id": "drv-1",
    "document_type": "Driver's License",
    "status": "pending",
}
_INSURANCE_DOC = {
    "id": "doc-insurance-1",
    "driver_id": "drv-1",
    "document_type": "Insurance",
    "status": "pending",
}
_ADMIN = {"id": "adm-1", "email": "admin@spinr.ca", "role": "super_admin"}

_DRIVER_MISSING_LICENSE = {
    "id": "drv-1",
    "user_id": "usr-1",
    "status": "active",
    "license_number": None,
    "license_class": None,
}
_DRIVER_WITH_LICENSE = {
    "id": "drv-1",
    "user_id": "usr-1",
    "status": "active",
    "license_number": "vault:ciphertext...",
    "license_class": "5",
}


def _patches(doc: dict, driver: dict):
    async def _get_rows(table, filters=None, **kwargs):
        if table == "driver_documents":
            if (filters or {}).get("status") == "pending":
                return []
            return [doc]
        return []

    update_one = AsyncMock()
    return (
        patch.object(admin_docs.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(admin_docs.db_supabase, "update_one", update_one),
        patch.object(admin_docs.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_docs, "notify_driver_status_change", AsyncMock()),
        patch.object(admin_docs, "_log_driver_activity", AsyncMock()),
        patch.object(admin_docs, "log_admin_action", AsyncMock()),
        patch.object(admin_docs, "supabase", None),
    ), update_one


async def test_approving_license_doc_blocked_when_license_data_missing():
    """Newly-blocked path: approve attempted with missing license_number/class."""
    patches, update_one = _patches(_LICENSE_DOC, _DRIVER_MISSING_LICENSE)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with pytest.raises(HTTPException) as exc_info:
            await admin_docs.admin_review_driver_document(
                "doc-license-1",
                admin_docs.DocumentReviewRequest(status="approved"),
                admin=_ADMIN,
            )
    assert exc_info.value.status_code == 422
    assert "licence number/class" in exc_info.value.detail
    # No driver_documents write happened -- the doc must stay pending, not
    # half-approved.
    update_one.assert_not_awaited()


async def test_approving_license_doc_blocked_when_only_class_missing():
    driver = {**_DRIVER_WITH_LICENSE, "license_class": None}
    patches, update_one = _patches(_LICENSE_DOC, driver)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with pytest.raises(HTTPException) as exc_info:
            await admin_docs.admin_review_driver_document(
                "doc-license-1",
                admin_docs.DocumentReviewRequest(status="approved"),
                admin=_ADMIN,
            )
    assert exc_info.value.status_code == 422
    update_one.assert_not_awaited()


async def test_approving_license_doc_succeeds_when_license_data_present():
    """Allowed path (the common case): both fields already on file."""
    patches, update_one = _patches(_LICENSE_DOC, _DRIVER_WITH_LICENSE)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = await admin_docs.admin_review_driver_document(
            "doc-license-1",
            admin_docs.DocumentReviewRequest(status="approved"),
            admin=_ADMIN,
        )
    assert result == {"message": "Document approved"}
    update_one.assert_awaited()


async def test_approving_non_license_doc_unaffected_by_missing_license_data():
    """Gate must not widen to documents other than the licence one."""
    patches, update_one = _patches(_INSURANCE_DOC, _DRIVER_MISSING_LICENSE)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = await admin_docs.admin_review_driver_document(
            "doc-insurance-1",
            admin_docs.DocumentReviewRequest(status="approved"),
            admin=_ADMIN,
        )
    assert result == {"message": "Document approved"}
    update_one.assert_awaited()


async def test_rejecting_license_doc_unaffected_by_missing_license_data():
    """Gate only applies to approvals, never rejections."""
    patches, update_one = _patches(_LICENSE_DOC, _DRIVER_MISSING_LICENSE)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = await admin_docs.admin_review_driver_document(
            "doc-license-1",
            admin_docs.DocumentReviewRequest(status="rejected", rejection_reason="blurry"),
            admin=_ADMIN,
        )
    assert result == {"message": "Document rejected"}
    update_one.assert_awaited()
