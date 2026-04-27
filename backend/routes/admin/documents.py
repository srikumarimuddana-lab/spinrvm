import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from utils.audit_logger import log_admin_action  # noqa: F401

from .drivers import _log_driver_activity

logger = logging.getLogger(__name__)

router = APIRouter()


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
async def admin_create_document_requirement(requirement: DocumentRequirementCreateRequest):
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
    return {"requirement_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@router.put("/documents/requirements/{requirement_id}")
async def admin_update_document_requirement(requirement_id: str, requirement: DocumentRequirementUpdateRequest):
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
    return {"message": "Document requirement updated"}


@router.delete("/documents/requirements/{requirement_id}")
async def admin_delete_document_requirement(requirement_id: str):
    """Delete a document requirement."""
    await db_supabase.delete_one("document_requirements", {"id": requirement_id})
    return {"message": "Document requirement deleted"}


# ---------- Driver Documents ----------


@router.get("/documents/drivers/{driver_id}")
async def admin_get_driver_documents(driver_id: str):
    """Get all documents for a specific driver."""
    documents = await db_supabase.get_rows(
        "driver_documents",
        {"driver_id": driver_id},
        order="uploaded_at",
        desc=True,
        limit=100,
    )
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

    # NOTE: driver_documents schema only guarantees these columns:
    #   id, driver_id, document_type, document_url, status,
    #   rejection_reason, uploaded_at, updated_at, requirement_id, side
    # Writing `reviewed_at` or `expiry_date` here would cause PGRST204
    # ("Could not find the X column") -> 500 response with no CORS headers,
    # which is why this endpoint has been silently failing in production.
    updates: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if rejection_reason:
        updates["rejection_reason"] = rejection_reason

    try:
        await db_supabase.update_one("driver_documents", {"id": document_id}, updates)
    except Exception as e:
        logger.error(f"Failed to update driver_document {document_id}: {e}")
        raise HTTPException(status_code=500, detail="Document update failed. Please try again.") from e

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
                    f"document_requirements lookup failed for req_id={existing_req_id!r}",
                    extra={"req_id": existing_req_id, "document_id": document_id},
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="doc_req_unavailable") from _req_err
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
            except Exception as e:
                logger.warning(
                    f"Could not update legacy expiry field {legacy_field} for driver {existing.get('driver_id')}: {e}"
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
                            "drivers", {"id": driver_id}, {"status": "active", "is_verified": True}
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

    return {"message": f"Document {status}"}
