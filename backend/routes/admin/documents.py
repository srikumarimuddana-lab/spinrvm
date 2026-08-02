import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...documents import (
        _extract_storage_key,
        _is_valid_uuid,
        _supersede_and_flag_pending_review,
        regenerate_signed_url,
        save_upload,
    )
    from ...features import send_push_notification
    from ...supabase_client import supabase
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from documents import (  # noqa: F401
        _extract_storage_key,
        _is_valid_uuid,
        _supersede_and_flag_pending_review,
        regenerate_signed_url,
        save_upload,
    )
    from features import send_push_notification  # noqa: F401
    from supabase_client import supabase  # noqa: F401
    from utils.audit_logger import log_admin_action  # noqa: F401

from .drivers import _log_driver_activity

# Templated push copy for document rejections. Keys match the dropdown in
# the admin reviewer ui; "other" falls back to the free-text reason.
_REJECT_TEMPLATES: Dict[str, tuple[str, str]] = {
    "blurry_image": (
        "Document needs re-upload",
        "We couldn't read your {doc} clearly. Please re-upload a sharper photo.",
    ),
    "wrong_document_type": (
        "Wrong document type",
        "The file you uploaded for {doc} doesn't match what's required. Please upload the correct document.",
    ),
    "expired": (
        "Expired document",
        "Your {doc} appears expired. Please upload a current copy to continue driving.",
    ),
    "information_unclear": (
        "Document information unclear",
        "Some information on your {doc} is unclear or unreadable. Please re-upload.",
    ),
}

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Document file proxy ----------


@router.get("/documents/{document_id}/view")
async def admin_view_driver_document(
    document_id: str,
    admin: dict = Depends(get_admin_user),
):
    """Stream a driver document through the backend — browser never touches storage directly.

    Uses the service-role key server-side, so no public bucket policy is needed
    and no signed URL is ever exposed to the client.
    """
    rows = await db_supabase.get_rows("driver_documents", {"id": document_id}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Document not found")
    doc = rows[0]

    stored_url = doc.get("document_url") or doc.get("file_url") or ""
    storage_key = _extract_storage_key(stored_url)
    if not storage_key:
        raise HTTPException(status_code=404, detail="Document has no resolvable storage key")

    if not supabase:
        raise HTTPException(status_code=503, detail="Storage client not configured")

    try:
        data: bytes = supabase.storage.from_("driver-documents").download(storage_key)
    except Exception as exc:
        logger.error("Storage download failed key=%s doc=%s: %s", storage_key, document_id, exc)
        raise HTTPException(status_code=502, detail="Could not fetch document from storage") from exc

    content_type, _ = mimetypes.guess_type(storage_key)
    return Response(content=data, media_type=content_type or "application/octet-stream")


# ---------- Document Requirements ----------


class DocumentRequirementCreateRequest(BaseModel):
    name: Optional[str] = None
    description: str = ""
    document_type: Optional[str] = None
    is_required: bool = True
    applicable_to: str = "driver"


class DocumentRequirementUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    document_type: Optional[str] = None
    is_required: Optional[bool] = None
    applicable_to: Optional[str] = None


@router.get("/documents/requirements")
async def admin_get_document_requirements():
    """Get all document requirements."""
    requirements = await db_supabase.get_rows("document_requirements", order="created_at", limit=100)
    return requirements or []


@router.post("/documents/requirements")
async def admin_create_document_requirement(
    requirement: DocumentRequirementCreateRequest,
    admin: dict = Depends(get_admin_user),
):
    """Create a new document requirement."""
    doc = {
        "name": requirement.name,
        "description": requirement.description,
        "document_type": requirement.document_type,
        "is_required": requirement.is_required,
        "applicable_to": requirement.applicable_to,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row = await db_supabase.insert_one("document_requirements", doc)
    requirement_id = str(row.get("id") if row and isinstance(row, dict) else "")
    await log_admin_action(
        admin,
        "document_requirement_created",
        "document_requirements",
        requirement_id,
        {"name": requirement.name, "document_type": requirement.document_type},
    )
    return {"requirement_id": requirement_id}


@router.put("/documents/requirements/{requirement_id}")
async def admin_update_document_requirement(
    requirement_id: str,
    requirement: DocumentRequirementUpdateRequest,
    admin: dict = Depends(get_admin_user),
):
    """Update a document requirement."""
    updates: Dict[str, Any] = {}
    if requirement.name is not None:
        updates["name"] = requirement.name
    if requirement.description is not None:
        updates["description"] = requirement.description
    if requirement.document_type is not None:
        updates["document_type"] = requirement.document_type
    if requirement.is_required is not None:
        updates["is_required"] = requirement.is_required
    if requirement.applicable_to is not None:
        updates["applicable_to"] = requirement.applicable_to

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("document_requirements", {"id": requirement_id}, updates)
        await log_admin_action(
            admin,
            "document_requirement_updated",
            "document_requirements",
            requirement_id,
            {k: v for k, v in updates.items() if k != "updated_at"},
        )
    return {"message": "Document requirement updated"}


@router.delete("/documents/requirements/{requirement_id}")
async def admin_delete_document_requirement(requirement_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a document requirement."""
    await db_supabase.delete_one("document_requirements", {"id": requirement_id})
    await log_admin_action(
        admin,
        "document_requirement_deleted",
        "document_requirements",
        requirement_id,
        {},
    )
    return {"message": "Document requirement deleted"}


# ---------- Pending Documents (cursor-paginated, A-P4-4) ----------


from fastapi import Query  # noqa: E402  (placed here to avoid circular at module top)


@router.get("/documents/pending")
async def admin_get_pending_documents(
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = None,
    status: str = Query("pending"),
    admin: dict = Depends(get_admin_user),
):
    """Paginated list of driver documents awaiting review.

    Cursor is the ``id`` of the last item from the previous page.
    Pass it back as-is on subsequent requests to advance the window.
    Returns at most ``limit`` items (max 100) plus a ``next_cursor``
    field (null when the last page has been reached).
    """
    filters: Dict[str, Any] = {"status": status}
    if cursor:
        filters["id"] = {"$gt": cursor}

    docs = await db_supabase.get_rows(
        "driver_documents",
        filters,
        order="id",
        limit=limit + 1,
    )
    has_more = len(docs) > limit
    items = docs[:limit]
    for doc in items:
        for field in ("document_url", "file_url"):
            if doc.get(field):
                doc[field] = regenerate_signed_url(doc[field])
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if items and has_more else None,
    }


# ---------- Driver Documents ----------


@router.get("/documents/drivers/{driver_id}")
async def admin_get_driver_documents(driver_id: str, admin: dict = Depends(get_admin_user)):
    """Get all documents for a specific driver."""
    documents = await db_supabase.get_rows(
        "driver_documents",
        {"driver_id": driver_id},
        order="uploaded_at",
        desc=True,
        limit=100,
    )
    for doc in documents or []:
        for field in ("document_url", "file_url"):
            if doc.get(field):
                doc[field] = regenerate_signed_url(doc[field])
    return documents or []


# Map keywords in a requirement name to the legacy top-level expiry column
# on the `drivers` row. Used when approving a re-uploaded document so that
# the go-online expiry check in routes/drivers.py update_driver_status stops
# rejecting the driver based on the stale onboarding-time value.
_REQUIREMENT_EXPIRY_FIELD_KEYWORDS = (
    ("license", "license_expiry_date"),
    ("driving", "license_expiry_date"),
    ("permit", "license_expiry_date"),
    ("insurance", "insurance_expiry_date"),
    ("inspection", "vehicle_inspection_expiry_date"),
    ("background", "background_check_expiry_date"),
    ("work", "work_eligibility_expiry_date"),
    ("eligibility", "work_eligibility_expiry_date"),
)


def _legacy_expiry_field_for_requirement(req_name: Optional[str]) -> Optional[str]:
    if not req_name:
        return None
    name = req_name.lower()
    for kw, field in _REQUIREMENT_EXPIRY_FIELD_KEYWORDS:
        if kw in name:
            return field
    return None


class DocumentReviewRequest(BaseModel):
    status: str
    rejection_reason: Optional[str] = None
    expiry_date: Optional[str] = None
    notify: bool = True
    notify_template: Optional[str] = None  # see _REJECT_TEMPLATES


@router.post("/documents/{document_id}/review")
async def admin_review_driver_document(
    document_id: str,
    review_data: DocumentReviewRequest,
    admin: dict = Depends(get_admin_user),
):
    """Review and approve/reject a driver document.

    On approval, if an ``expiry_date`` is provided (or already stored on the
    doc), we also refresh the corresponding legacy top-level expiry column on
    the ``drivers`` row so that the go-online check in
    ``update_driver_status`` sees the new date instead of the stale
    onboarding-time value (which used to leave drivers blocked offline).
    """
    status = review_data.status
    rejection_reason = review_data.rejection_reason
    expiry_raw = review_data.expiry_date

    if status not in ["approved", "rejected", "pending"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    # Load existing doc so we know which driver + requirement this is.
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("driver_documents", {"id": document_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # Parse incoming expiry (accept ISO string or None).
    new_expiry_iso: Optional[str] = None
    if expiry_raw:
        try:
            new_expiry_iso = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00")).isoformat()
        except ValueError:
            new_expiry_iso = None

    # Schema (after migration 91): id, driver_id, document_type,
    # document_url, status, rejection_reason, uploaded_at, updated_at,
    # requirement_id, requirement_key, side, expiry_date.
    # Writing `reviewed_at` would still trigger PGRST204 — keep that out.
    updates: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if rejection_reason:
        updates["rejection_reason"] = rejection_reason
    # Persist the doc-level expiry. Without this, expiries set by the admin
    # were silently dropped for any requirement that didn't map to a legacy
    # drivers.* column (e.g. vehicle_registration), and the slideout's
    # expiry card kept reading "Not set" no matter how many times the admin
    # re-approved the doc.
    if status == "approved":
        updates["expiry_date"] = new_expiry_iso
    elif status == "rejected":
        # Clear any stale expiry on rejection so the next reviewer doesn't
        # see a phantom date carried over from a prior approval.
        updates["expiry_date"] = None

    try:
        await db_supabase.update_one("driver_documents", {"id": document_id}, updates)
    except Exception as e:
        # B-P3-leak-cleanup: full traceback to logs, generic detail
        # to client. Supabase / postgrest errors carry table internals.
        logger.exception(f"Failed to update driver_document {document_id}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update document.",
        ) from e

    # On approval, propagate the expiry to the legacy drivers.* column so the
    # go-online check stops blocking based on stale onboarding-time values.
    if status == "approved":
        effective_expiry_iso = new_expiry_iso

        # Derive a requirement name for keyword-based legacy-field mapping.
        # Service-area uploads store the slug in requirement_key and the human
        # label in document_type; requirement_id is NULL. Fall back through
        # those so the license/insurance/inspection/background keywords in
        # _legacy_expiry_field_for_requirement still match.
        req_name: Optional[str] = None
        existing_req_id = existing.get("requirement_id")
        if existing_req_id:
            try:
                req_row = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("document_requirements", {"id": existing_req_id}, limit=1)
                )
                if req_row:
                    req_name = req_row.get("name")
            except Exception as _req_err:
                logger.error(
                    "document requirement lookup failed — expiry won't propagate to legacy field",
                    extra={"req_id": existing_req_id, "doc_id": document_id},
                    exc_info=True,
                )
                req_name = None
        if not req_name:
            req_name = existing.get("document_type") or existing.get("requirement_key")

        legacy_field = _legacy_expiry_field_for_requirement(req_name)
        if legacy_field:
            # If admin did not supply a new expiry, clear the stale legacy
            # value (None) so the go-online check skips it instead of
            # rejecting on a past date from original onboarding.
            try:
                await db_supabase.update_one(
                    "drivers",
                    {"id": existing.get("driver_id")},
                    {
                        legacy_field: effective_expiry_iso,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                logger.error(
                    "Could not update legacy expiry field %s for driver %s",
                    legacy_field,
                    existing.get("driver_id"),
                    exc_info=True,
                )

    # After approving, check if this driver has no more pending docs → clear needs_review
    if status == "approved":
        driver_id = existing.get("driver_id")
        if driver_id:
            remaining_pending = await db_supabase.get_rows(
                "driver_documents",
                {"driver_id": driver_id, "status": "pending"},
                limit=1,
            )
            if not remaining_pending:
                # All pending docs approved → set driver back to active
                try:
                    drv = await db_supabase.get_driver_by_id(driver_id)
                    if drv and drv.get("status") == "needs_review":
                        await db_supabase.update_one(
                            "drivers",
                            {"id": driver_id},
                            {"status": "active", "is_verified": True},
                        )
                except Exception as _exc:
                    logger.debug(f"Could not reset driver {driver_id} status to active: {_exc}")

    # Log to activity timeline
    doc_type = existing.get("document_type", "Document")
    await _log_driver_activity(
        existing.get("driver_id", ""),
        f"document_{status}",
        f"Document {status}: {doc_type}",
        rejection_reason or "",
        {"document_id": document_id, "document_type": doc_type, "status": status},
    )
    await log_admin_action(
        admin,
        f"document_{status}",
        "driver_documents",
        document_id,
        {
            "driver_id": existing.get("driver_id"),
            "document_type": doc_type,
            "status": status,
            "rejection_reason": rejection_reason,
        },
    )

    # Push the driver a re-upload prompt when the admin rejects a document.
    # Skipped if (a) the caller opted out, (b) status isn't a rejection, or
    # (c) the doc was already in "rejected" state before this call — that
    # case is an edit (e.g. fixing the reason text) and re-firing would
    # spam the driver. Push failures are intentionally swallowed: the
    # DB-level rejection is already committed and the audit trail above
    # records the action even if FCM is unreachable.
    if status == "rejected" and review_data.notify and existing.get("status") != "rejected":
        driver_id = existing.get("driver_id")
        if driver_id:
            try:
                drv = await db_supabase.get_driver_by_id(driver_id)
                user_id = (drv or {}).get("user_id")
                if user_id:
                    template = _REJECT_TEMPLATES.get(review_data.notify_template or "")
                    if template:
                        title, body_tmpl = template
                        body = body_tmpl.format(doc=doc_type)
                    else:
                        title = "Document needs re-upload"
                        body = (
                            f"Your {doc_type} was not approved: {rejection_reason}"
                            if rejection_reason
                            else f"Your {doc_type} was not approved. Please re-upload."
                        )
                    await send_push_notification(
                        user_id,
                        title,
                        body,
                        data={
                            "type": "document_rejected",
                            "driver_id": driver_id,
                            "document_id": document_id,
                            "document_type": doc_type,
                        },
                    )
            except Exception:
                logger.warning(
                    "Document-rejection push failed for driver %s doc %s",
                    driver_id,
                    document_id,
                    exc_info=True,
                )

    return {"message": f"Document {status}"}


# ---------- Manual admin document upload ----------


def _matches_requirement(doc: Dict[str, Any], req: Dict[str, Any]) -> bool:
    req_key = (req.get("key") or "").lower()
    req_label = (req.get("label") or "").lower()
    req_id = req.get("id")
    dkey = (doc.get("requirement_key") or "").lower()
    if dkey and dkey == req_key:
        return True
    drid = doc.get("requirement_id")
    if drid and (drid == req_id or (isinstance(drid, str) and drid.lower() == req_key)):
        return True
    dt = (doc.get("document_type") or "").lower()
    return bool(dt and (dt == req_label or dt == req_key.replace("_", " ")))


def _parse_expiry_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


async def _driver_required_documents_complete(driver_id: str) -> bool:
    """True only when every mandatory service-area document is approved.

    Reactivating a needs_review driver on a single approved upload is unsafe:
    imports can start with zero documents, and some requirements need front +
    back or an unexpired expiry date.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver or not driver.get("service_area_id"):
        return False
    area = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
    )
    if not area:
        return False
    required = [r for r in (area.get("required_documents") or []) if r.get("required", True)]
    if not required:
        return True
    approved_docs = await db_supabase.get_rows(
        "driver_documents", {"driver_id": driver_id, "status": "approved"}, limit=200
    )
    now = datetime.now(timezone.utc)
    for req in required:
        docs = [doc for doc in approved_docs if _matches_requirement(doc, req)]
        if not docs:
            return False
        if req.get("requires_back_side"):
            sides = {doc.get("side") for doc in docs}
            if not {"front", "back"}.issubset(sides):
                return False
        if req.get("has_expiry"):
            latest = sorted(docs, key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)[0]
            expiry = _parse_expiry_datetime(latest.get("expiry_date") or latest.get("expires_at"))
            if not expiry or expiry < now:
                return False
    return True


async def _propagate_approval(driver_id: str, req_name: Optional[str], expiry_iso: Optional[str]) -> None:
    """Approval side-effects shared with the review flow: mirror the doc expiry
    onto the legacy ``drivers.*_expiry_date`` column (so the go-online check
    stops blocking on a stale onboarding date) and, if the driver has no more
    pending docs, flip a ``needs_review`` driver back to ``active``.
    """
    legacy_field = _legacy_expiry_field_for_requirement(req_name)
    if legacy_field:
        try:
            await db_supabase.update_one(
                "drivers",
                {"id": driver_id},
                {legacy_field: expiry_iso, "updated_at": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            logger.error(
                "Could not update legacy expiry field %s for driver %s",
                legacy_field,
                driver_id,
                exc_info=True,
            )

    all_required_docs_complete = await _driver_required_documents_complete(driver_id)
    if all_required_docs_complete:
        try:
            drv = await db_supabase.get_driver_by_id(driver_id)
            if drv and drv.get("status") == "needs_review":
                await db_supabase.update_one(
                    "drivers",
                    {"id": driver_id},
                    {"status": "active", "is_verified": True},
                )
        except Exception as _exc:
            logger.debug(f"Could not reset driver {driver_id} status to active: {_exc}")


@router.post("/documents/upload")
async def admin_upload_driver_document(
    file: UploadFile = File(...),
    driver_id: str = Form(...),
    requirement_key: str = Form(...),
    side: Optional[str] = Form(None),  # 'front' or 'back'
    expiry_date: Optional[str] = Form(None),  # ISO string
    status: str = Form("pending"),  # 'pending' or 'approved'
    admin: dict = Depends(get_admin_user),
):
    """Upload a document on a driver's behalf.

    Mirrors the driver self-upload flow (``documents.upload_driver_document``)
    but is admin-driven: the admin names the ``driver_id`` and may commit the
    document straight to ``approved``. Approving here runs the same legacy-expiry
    mirror + reactivation side-effects as the review endpoint, and skips the
    ``needs_review`` flip so an active driver isn't taken offline by the upload.
    """
    if status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="status must be 'pending' or 'approved'")
    if side is not None and side not in ("front", "back"):
        raise HTTPException(status_code=400, detail="side must be 'front' or 'back'")

    drv = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1))
    if not drv:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Resolve the requirement: global document_requirements by UUID, else the
    # driver's service-area required_documents by slug key. Strict (404) so a
    # typo can't create an orphan document the onboarding check never sees.
    req_name: Optional[str] = None
    req_id_for_db: Optional[str] = None
    req_has_expiry = False
    if _is_valid_uuid(requirement_key):
        req = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("document_requirements", {"id": requirement_key}, limit=1)
        )
        if req:
            req_name = req.get("name")
            req_id_for_db = requirement_key
            req_has_expiry = bool(req.get("has_expiry"))
    if req_name is None and drv.get("service_area_id"):
        area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": drv["service_area_id"]}, limit=1)
        )
        if area:
            area_req = next(
                (d for d in (area.get("required_documents") or []) if d.get("key") == requirement_key), None
            )
            if area_req:
                req_name = area_req.get("label") or requirement_key
                req_has_expiry = bool(area_req.get("has_expiry"))
    if req_name is None:
        raise HTTPException(status_code=404, detail="Requirement not found for this driver's service area")

    expiry_iso: Optional[str] = None
    if expiry_date:
        try:
            expiry_iso = datetime.fromisoformat(str(expiry_date).replace("Z", "+00:00")).isoformat()
        except ValueError as e:
            raise HTTPException(status_code=400, detail="expiry_date must be an ISO date") from e

    if status == "approved" and req_has_expiry and not expiry_iso:
        raise HTTPException(status_code=400, detail="expiry_date is required before approving this document")

    # Uploads (validates size/MIME/magic-bytes, stores in the driver-documents bucket).
    url = await save_upload(file)

    # Supersede prior docs for the same requirement+side. Only flag the driver
    # for review when this upload is left pending; an approved upload keeps the
    # driver's current state.
    await _supersede_and_flag_pending_review(
        driver_id,
        requirement_key,
        side,
        document_type=req_name,
        flag_review=(status == "pending"),
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "id": str(uuid.uuid4()),
        "driver_id": driver_id,
        "requirement_id": req_id_for_db,
        "requirement_key": requirement_key,
        "document_type": req_name,
        "document_url": url,
        "side": side,
        "status": status,
        "expiry_date": expiry_iso,
        "uploaded_at": now_iso,
        "updated_at": now_iso,
    }
    try:
        await db_supabase.insert_one("driver_documents", record)
    except Exception as e:
        logger.exception("admin document upload insert failed for driver %s", driver_id)
        raise HTTPException(status_code=502, detail="Could not save document record") from e

    if status == "approved":
        await _propagate_approval(driver_id, req_name, expiry_iso)

    await log_admin_action(
        admin,
        "document_uploaded",
        "driver_documents",
        record["id"],
        {"driver_id": driver_id, "requirement_key": requirement_key, "status": status},
    )
    return record
