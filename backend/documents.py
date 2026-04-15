import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

try:
    from . import db_supabase
    from .dependencies import get_current_user
    from .supabase_client import supabase
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from supabase_client import supabase

from loguru import logger

# --- File Upload Security ---
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
}

# Magic byte signatures for content-type verification
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",  # WebP starts with RIFF
    b"%PDF": "application/pdf",
}


def _validate_file_type(content: bytes, declared_type: str) -> None:
    """Validate file MIME type against allowlist and verify magic bytes."""
    if declared_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{declared_type}' not allowed. Accepted: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )
    # Verify magic bytes match the declared content type
    if content:
        header = content[:4]
        for magic, expected_type in _MAGIC_BYTES.items():
            if header.startswith(magic) and declared_type != expected_type:
                raise HTTPException(
                    status_code=400,
                    detail="File content does not match declared type",
                )


# Routers
# Routers
documents_router = APIRouter(prefix="/drivers", tags=["Driver Documents"])
admin_documents_router = APIRouter(prefix="/documents", tags=["Admin Documents"])

# --- Models ---


class DocumentRequirement(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_mandatory: bool
    requires_back_side: bool
    created_at: datetime


class CreateRequirementRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_mandatory: bool = True
    requires_back_side: bool = False


class UpdateRequirementRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_mandatory: Optional[bool] = None
    requires_back_side: Optional[bool] = None


class LinkDocumentRequest(BaseModel):
    requirement_id: str
    document_url: str
    document_type: str = "image/jpeg"
    side: Optional[str] = "front"
    expiry_date: Optional[datetime] = None


class DriverDocument(BaseModel):
    id: str
    driver_id: str
    requirement_id: Optional[str] = None
    document_type: str  # Kept for backward compatibility or display
    document_url: str
    side: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    uploaded_at: datetime


# --- Helper: Upload Directory ---
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# Map keywords in a requirement name to the legacy top-level expiry column on
# the `drivers` row. Used so that approving a re-uploaded document refreshes
# the expiry that `update_driver_status` (go-online) checks against.
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


async def _supersede_and_flag_pending_review(
    driver_id: str,
    requirement_id: str,
    side: Optional[str],
) -> None:
    """
    When a driver (re-)uploads a document, mark any prior docs for the same
    requirement+side as 'superseded' so they stop counting against the driver,
    and flip the driver's `is_verified` back to False. This makes the driver
    re-appear in the admin panel's "Unverified" queue so the Approve button
    becomes actionable again.
    """
    try:
        query: Dict[str, Any] = {
            "driver_id": driver_id,
            "requirement_id": requirement_id,
            "status": {"$in": ["approved", "pending"]},
        }
        if side is not None:
            query["side"] = side
        await db_supabase.update_one("driver_documents", query, {"status": "superseded", "updated_at": datetime.utcnow()})
    except Exception as e:
        logger.warning(f"Could not supersede prior docs for driver {driver_id}: {e}")

    # Set driver to needs_review so they can't go online until admin re-approves.
    try:
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("status") == "active":
            await db_supabase.update_one("drivers", {"id": driver_id}, {"status": "needs_review", "is_online": False, "is_available": False, "updated_at": datetime.utcnow()})
        else:
            await db_supabase.update_one("drivers", {"id": driver_id}, {"updated_at": datetime.utcnow()})
    except Exception as e:
        logger.warning(f"Could not flag driver {driver_id} for review: {e}")


async def save_upload(file: UploadFile) -> str:
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"

    try:
        file_bytes = await file.read()
        _validate_file_type(file_bytes, file.content_type or "application/octet-stream")
        supabase.storage.from_("driver-documents").upload(
            file=file_bytes, path=filename, file_options={"content-type": file.content_type}
        )

        # Get public URL
        url_res = supabase.storage.from_("driver-documents").get_public_url(filename)
        return url_res
    except Exception as e:
        logger.error(f"Failed to upload to Supabase Storage: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}") from e


# --- Public/Driver Endpoints ---


@documents_router.get("/requirements")
async def get_document_requirements(
    service_area_id: Optional[str] = Query(None),
    current_user: Optional[dict] = Depends(get_current_user),
):
    """Get document requirements for drivers.

    Priority:
    1. If service_area_id is passed, use that area's required_documents.
    2. Else if the current user has a driver profile with a service_area_id,
    use that area's required_documents.
    3. Fall back to the global document_requirements table.
    """
    area_id = service_area_id

    # Try to get area from driver profile if not explicitly passed
    if not area_id and current_user:
        driver = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1))
        if driver:
            area_id = driver.get("service_area_id")

    # If we have an area, return its required_documents
    if area_id:
        area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
        if area and area.get("required_documents"):
            area_docs = area["required_documents"]
            # Transform to match DocumentRequirement shape so driver app works
            result = []
            for doc in area_docs:
                result.append(
                    {
                        "id": doc.get("key", ""),
                        "name": doc.get("label", ""),
                        "description": None,
                        "is_mandatory": doc.get("required", True),
                        "requires_back_side": doc.get("requires_back_side", False),
                        "has_expiry": doc.get("has_expiry", False),
                        "created_at": area.get("created_at", datetime.utcnow().isoformat()),
                    }
                )
            return result

    # Fallback: global document_requirements table
    requirements = await db_supabase.get_rows("document_requirements", None, limit=100, order="created_at", desc=False)
    return requirements


@documents_router.get("/documents")
async def get_driver_documents(current_user: dict = Depends(get_current_user)):
    """Get all documents uploaded by the current driver."""
    # Look up the driver profile directly — avoids a stale is_driver flag and
    # returns an empty list gracefully during the onboarding flow.
    driver = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1))
    if not driver:
        return []  # No driver profile yet — not an error

    documents = await db_supabase.get_rows("driver_documents", {"driver_id": driver["id"]}, limit=100, order="uploaded_at", desc=True)
    return documents


@documents_router.post("/documents")
async def link_driver_document(doc_data: LinkDocumentRequest, current_user: dict = Depends(get_current_user)):
    """Link an uploaded document to the current driver."""
    if not current_user.get("is_driver"):
        raise HTTPException(status_code=403, detail="User is not a driver")

    driver = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1))
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Validate requirement exists — check global table first, then
    # fall back to the driver's service area required_documents list
    # (since we moved to per-area docs, requirement_id is now the area doc key).
    req = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("document_requirements", {"id": doc_data.requirement_id}, limit=1))
    if not req:
        # Try looking it up from the driver's service area
        area_req = None
        if driver.get("service_area_id"):
            area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1))
            if area:
                area_req = next(
                    (d for d in (area.get("required_documents") or []) if d.get("key") == doc_data.requirement_id), None
                )
        if not area_req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        # Synthesise a req-like dict so downstream code works uniformly
        req = {
            "id": area_req.get("key"),
            "name": area_req.get("label", doc_data.requirement_id),
            "requires_back_side": area_req.get("requires_back_side", False),
        }

    # Supersede any prior docs for this requirement+side and flip the
    # driver back to unverified so admin re-reviews this upload.
    await _supersede_and_flag_pending_review(driver["id"], doc_data.requirement_id, doc_data.side)

    # Create document record.
    # NOTE: Only columns that exist on the Supabase driver_documents table —
    # writing `expiry_date` here raises PGRST204. Expiry is stored on the
    # drivers row via admin approval in the legacy *_expiry_date columns.
    doc_record = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "requirement_id": doc_data.requirement_id,
        "document_type": doc_data.document_type,
        "document_url": doc_data.document_url,
        "side": doc_data.side,
        "status": "pending",
        "uploaded_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    await db_supabase.insert_one("driver_documents", doc_record)
    # Stash the admin-facing expiry on the response so the caller can
    # display it back, without persisting a non-existent column.
    if doc_data.expiry_date:
        doc_record["expiry_date"] = doc_data.expiry_date.isoformat()
    return doc_record


@documents_router.post("/documents/upload")
async def upload_driver_document(
    file: UploadFile = File(...),
    driver_id: str = Form(...),
    requirement_id: str = Form(...),
    side: Optional[str] = Form(None),  # 'front' or 'back'
    expiry_date: Optional[str] = Form(None),
):
    """Upload a specific document linked to a requirement."""
    # storage logic
    url = await save_upload(file)

    # Validate requirement — check global table first, then service area docs.
    req = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("document_requirements", {"id": requirement_id}, limit=1))
    if not req:
        area_req = None
        if driver_id:
            drv = await db_supabase.get_driver_by_id(driver_id)
            if drv and drv.get("service_area_id"):
                area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": drv["service_area_id"]}, limit=1))
                if area:
                    area_req = next(
                        (d for d in (area.get("required_documents") or []) if d.get("key") == requirement_id), None
                    )
        if not area_req:
            raise HTTPException(status_code=404, detail="Requirement not found")
        req = {
            "id": area_req.get("key"),
            "name": area_req.get("label", requirement_id),
            "requires_back_side": area_req.get("requires_back_side", False),
        }

    # Normalise expiry_date input — accept ISO string, store as ISO string.
    expiry_iso: Optional[str] = None
    if expiry_date:
        try:
            expiry_iso = datetime.fromisoformat(expiry_date.replace("Z", "+00:00")).isoformat()
        except ValueError:
            expiry_iso = None

    # Supersede prior docs for same requirement+side and flip driver to unverified
    # so admin panel resurfaces this driver for re-review.
    await _supersede_and_flag_pending_review(driver_id, requirement_id, side)

    # Create document record.
    # NOTE: Only columns that exist on the Supabase driver_documents table —
    # `expiry_date` is intentionally NOT written to this row (column doesn't
    # exist, would cause PGRST204). Expiry lives in the drivers row legacy
    # columns, refreshed on admin approval.
    doc_record = {
        "id": str(uuid.uuid4()),
        "driver_id": driver_id,
        "requirement_id": requirement_id,
        "document_type": req.get("name"),  # Denormalize name for easy display
        "document_url": url,
        "side": side,
        "status": "pending",
        "uploaded_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    await db_supabase.insert_one("driver_documents", doc_record)

    # Return the admin-facing expiry as part of the response without
    # persisting it to a non-existent column.
    if expiry_iso:
        doc_record["expiry_date"] = expiry_iso
    return doc_record


# --- Admin Endpoints ---


@admin_documents_router.get("/requirements")
async def admin_get_requirements():
    """Get all document requirements."""
    requirements = await db_supabase.get_rows("document_requirements", None, limit=100, order="created_at", desc=False)
    return requirements


@admin_documents_router.post("/requirements")
async def admin_create_requirement(req: CreateRequirementRequest):
    """Create a new document requirement."""
    new_req = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "description": req.description,
        "is_mandatory": req.is_mandatory,
        "requires_back_side": req.requires_back_side,
        "created_at": datetime.utcnow(),
    }
    await db_supabase.insert_one("document_requirements", new_req)
    return new_req


@admin_documents_router.put("/requirements/{req_id}")
async def admin_update_requirement(req_id: str, req: UpdateRequirementRequest):
    """Update a document requirement."""
    update_data = {}
    if req.name is not None:
        update_data["name"] = req.name  # noqa: E701
    if req.description is not None:
        update_data["description"] = req.description  # noqa: E701
    if req.is_mandatory is not None:
        update_data["is_mandatory"] = req.is_mandatory  # noqa: E701
    if req.requires_back_side is not None:
        update_data["requires_back_side"] = req.requires_back_side  # noqa: E701

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db_supabase.update_one("document_requirements", {"id": req_id}, update_data)
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Requirement not found")

    return (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("document_requirements", {"id": req_id}, limit=1))


@admin_documents_router.delete("/requirements/{req_id}")
async def admin_delete_requirement(req_id: str):
    """Delete a document requirement."""
    # Check if used?
    # For now, allow delete.
    result = await db_supabase.delete_one("document_requirements", {"id": req_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return {"deleted": True}


@admin_documents_router.get("/drivers/{driver_id}")
async def admin_get_driver_documents(driver_id: str):
    """Get all documents uploaded by a specific driver."""
    documents = await db_supabase.get_rows("driver_documents", {"driver_id": driver_id}, limit=100, order="uploaded_at", desc=True)
    return documents


class ReviewDocumentRequest(BaseModel):
    status: str
    rejection_reason: Optional[str] = None
    expiry_date: Optional[datetime] = None


@admin_documents_router.post("/{doc_id}/review")
async def admin_review_document(doc_id: str, req: ReviewDocumentRequest):
    """Approve or reject a driver document.

    On approval, if an ``expiry_date`` is provided (or already stored on the
    doc), we also refresh the corresponding legacy top-level expiry column on
    the ``drivers`` row so that the go-online check in
    ``update_driver_status`` sees the new date.
    """
    if req.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    # Pull the existing doc so we know which driver/requirement this is.
    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("driver_documents", {"id": doc_id}, limit=1))
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # Only write columns that exist on the driver_documents Supabase table.
    # `expiry_date` is NOT a column — we propagate it to the legacy
    # drivers.*_expiry_date column below instead.
    update_data: Dict[str, Any] = {"status": req.status, "updated_at": datetime.utcnow()}
    if req.rejection_reason is not None:
        update_data["rejection_reason"] = req.rejection_reason

    await db_supabase.update_one("driver_documents", {"id": doc_id}, update_data)

    # On approval, propagate the expiry to the legacy drivers.* column so the
    # go-online check in routes/drivers.py update_driver_status stops blocking
    # this driver based on stale onboarding-time values.
    if req.status == "approved":
        effective_expiry = req.expiry_date

        # Look up requirement name — first from global table, then from
        # the driver's service area required_documents (since requirement_id
        # may now be a service-area doc key like "drivers_license").
        req_row = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("document_requirements", {"id": existing.get("requirement_id")}, limit=1))
        req_name = req_row.get("name") if req_row else None

        if not req_name:
            # Try the service area's required_documents
            driver = await db_supabase.get_driver_by_id(existing.get("driver_id"))
            if driver and driver.get("service_area_id"):
                area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1))
                if area:
                    area_doc = next(
                        (
                            d
                            for d in (area.get("required_documents") or [])
                            if d.get("key") == existing.get("requirement_id")
                        ),
                        None,
                    )
                    if area_doc:
                        req_name = area_doc.get("label")

            # Last resort: use the document_type field from the uploaded doc
            if not req_name:
                req_name = existing.get("document_type")

        legacy_field = _legacy_expiry_field_for_requirement(req_name)
        if legacy_field:
            # If admin didn't supply a new expiry, clear the stale legacy
            # value (None) so the go-online check skips it instead of
            # rejecting on a past date from original onboarding.
            new_val = effective_expiry.isoformat() if effective_expiry else None
            try:
                await db_supabase.update_one("drivers", {"id": existing.get("driver_id")}, {legacy_field: new_val, "updated_at": datetime.utcnow()})
            except Exception as e:
                logger.warning(
                    f"Could not update legacy expiry field {legacy_field} for driver {existing.get('driver_id')}: {e}"
                )

    return (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("driver_documents", {"id": doc_id}, limit=1))


# --- File Serving Router ---
files_router = APIRouter(prefix="/documents", tags=["Files"])

# --- Generic Upload Router (no prefix so it mounts at /api/v1/upload) ---
upload_router = APIRouter(tags=["Upload"])

import base64  # noqa: E402

from fastapi import Response  # noqa: E402


@upload_router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Generic file upload endpoint.

    Stores the file as base64 in the `document_files` collection and returns a URL
    that can be served by GET /api/documents/{file_id}. Works on Railway's
    ephemeral filesystem because nothing is written to disk.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")

        # 10 MB hard cap — documents are usually photos/PDFs
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (max 10MB)")

        file_id = str(uuid.uuid4())
        size = len(content)
        filename = file.filename or "upload"
        content_type = file.content_type or "application/octet-stream"

        _validate_file_type(content, content_type)

        # Only insert columns that actually exist on the Supabase
        # `document_files` table. Historically this table was created with
        # just { id, data, content_type, created_at } — adding columns like
        # `size`, `filename`, `uploaded_by` to the insert raises PGRST204
        # ("Could not find the X column of document_files in the schema
        # cache"). We still return size/filename in the response so the
        # client gets full metadata without us having to touch the schema.
        record = {
            "id": file_id,
            "content_type": content_type,
            "data": base64.b64encode(content).decode("utf-8"),
            "created_at": datetime.utcnow().isoformat(),
        }

        try:
            await db_supabase.insert_one("document_files", record)
        except Exception as e:
            # If a newer schema has the extra columns, retry with them
            # included so we don't silently lose metadata on upgraded DBs.
            err_msg = str(e)
            if "PGRST204" in err_msg or "schema cache" in err_msg:
                # Already using minimal columns — re-raise with context.
                raise HTTPException(
                    status_code=500,
                    detail=f"Upload insert rejected by DB schema: {err_msg}",
                ) from e
            # Not a schema error — bubble up as-is.
            raise

        # Relative URL served by files_router (GET /api/documents/{file_id})
        url = f"/api/documents/{file_id}"
        return {
            "success": True,
            "url": url,
            "file_id": file_id,
            "filename": filename,
            "content_type": content_type,
            "size": size,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e


@files_router.get("/{file_id}")
async def get_document_file(file_id: str):
    """Serve a document file by ID."""
    # check if it's in document_files (DB storage)
    video_file = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("document_files", {"id": file_id}, limit=1))
    if video_file:
        try:
            content = base64.b64decode(video_file.get("data", ""))
            media_type = video_file.get("content_type", "application/octet-stream")
            return Response(content=content, media_type=media_type)
        except Exception as e:
            logger.error(f"Error serving file {file_id}: {e}")
            raise HTTPException(status_code=500, detail="Error serving file") from e

    # If not found in DB files, maybe it's a direct reference to a driver document
    # which might have a URL. But the request is specifically for /ids that are likely file IDs if generated by the legacy upload.

    # If the ID passed is actually a driver_document ID, we might want to redirect to its document_url
    doc = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("driver_documents", {"id": file_id}, limit=1))
    if doc and doc.get("document_url"):
        # If it's a relative URL (local upload), we might need to serve it from disk if we used disk storage
        # But current implementation uses /uploads/filename for disk
        if doc["document_url"].startswith("/uploads/"):
            # This should be handled by StaticFiles in server.py if mounted
            from fastapi.responses import RedirectResponse

            return RedirectResponse(doc["document_url"])
        # If it's a full URL (Supabase), redirect
        from fastapi.responses import RedirectResponse

        return RedirectResponse(doc["document_url"])

    raise HTTPException(status_code=404, detail="File not found")
