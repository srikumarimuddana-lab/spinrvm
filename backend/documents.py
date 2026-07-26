import os
import re as _re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel, field_validator

try:
    from . import db_supabase
    from .dependencies import get_admin_user, get_current_user
    from .supabase_client import supabase
except ImportError:
    import db_supabase
    from dependencies import get_admin_user, get_current_user
    from supabase_client import supabase

db = db_supabase  # legacy alias


def _is_valid_uuid(value: str) -> bool:
    """Return True if *value* is a well-formed UUID string."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


# --- File Upload Security ---
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",  # alias — some devices/pickers send this
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
}

# Magic byte signatures for content-type verification (non-RIFF types only)
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"GIF8": "image/gif",
    b"%PDF": "application/pdf",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}


def _is_valid_webp(data: bytes) -> bool:
    """Return True only if data has both the RIFF container and WEBP marker."""
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _validate_file_type(content: bytes, declared_type: str) -> None:
    """Validate file MIME type against allowlist and verify magic bytes."""
    # Normalise image/jpg → image/jpeg before allowlist check
    normalised = "image/jpeg" if declared_type == "image/jpg" else declared_type
    if normalised not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{declared_type}' not allowed. Accepted: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )
    # Verify magic bytes: detect the actual type from the file header.
    # Only reject if we can positively identify a DIFFERENT type from the bytes —
    # unknown headers (e.g. HEIC, camera raw) pass through.
    if content:
        # WebP requires both the RIFF container header AND the WEBP marker at
        # bytes 8-11; plain RIFF (WAV, AVI, etc.) must not be accepted as WebP.
        if normalised == "image/webp":
            if len(content) >= 12 and not _is_valid_webp(content):
                raise HTTPException(
                    status_code=400,
                    detail="File content does not match declared type",
                )
            return

        header = content[:4]
        detected_type: str | None = None
        for magic, magic_mime in _MAGIC_BYTES.items():
            if header.startswith(magic):
                detected_type = magic_mime
                break
        if detected_type and detected_type != normalised:
            raise HTTPException(
                status_code=400,
                detail="File content does not match declared type",
            )


# Routers
# Routers
documents_router = APIRouter(prefix="/drivers", tags=["Driver Documents"])
admin_documents_router = APIRouter(
    prefix="/documents",
    tags=["Admin Documents"],
    dependencies=[Depends(get_admin_user)],
)

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


def _validate_storage_document_url(url: str) -> str:
    """Reject any document_url that is not a Supabase storage URL for the
    driver-documents bucket.

    LinkDocumentRequest.document_url was a bare str written verbatim to
    driver_documents.document_url and later rendered as an <a href> in the admin
    dashboard. Any authenticated user can reach POST /drivers/documents (it
    auto-creates a driver row), so a value like
    javascript:fetch('…'+localStorage.token) executed on the admin origin with
    the admin session when a reviewer clicked "view" — stored XSS → admin token
    theft. Constrain the value to https on the configured Supabase host with the
    expected storage object path so only a genuine uploaded file can be linked.
    """
    from urllib.parse import urlparse

    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https":
        raise ValueError("document_url must be an https Supabase storage URL")
    # Path must be a storage object in the driver-documents bucket.
    if not _re.match(r"^/storage/v1/object/(?:sign|public)/driver-documents/", parsed.path):
        raise ValueError("document_url must point at the driver-documents storage bucket")
    # Host must match the configured Supabase project when known (always set in
    # production; startup fails without it). Falls back to the path check alone
    # in dev where SUPABASE_URL may be unset.
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    if supabase_url:
        expected_host = urlparse(supabase_url).netloc
        if expected_host and parsed.netloc != expected_host:
            raise ValueError("document_url host is not the configured Supabase project")
    return url


class LinkDocumentRequest(BaseModel):
    requirement_id: str
    document_url: str
    document_type: str = "image/jpeg"
    side: Optional[str] = "front"
    expiry_date: Optional[datetime] = None

    @field_validator("document_url")
    @classmethod
    def _check_document_url(cls, v: str) -> str:
        return _validate_storage_document_url(v)


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
    document_type: Optional[str] = None,
    flag_review: bool = True,
) -> None:
    """
    When a driver (re-)uploads a document, mark any prior docs for the same
    requirement+side as 'superseded' so they stop counting against the driver,
    and flip the driver's `is_verified` back to False. This makes the driver
    re-appear in the admin panel's "Unverified" queue so the Approve button
    becomes actionable again.

    ``flag_review=False`` supersedes the prior docs but does NOT knock the
    driver back to ``needs_review`` / offline. The admin manual-upload flow uses
    this when uploading a document already marked ``approved`` on the driver's
    behalf, so a currently-active driver isn't taken offline by the upload.
    """
    try:
        query: Dict[str, Any] = {
            "driver_id": driver_id,
            "status": {"$in": ["approved", "pending"]},
        }
        # requirement_id is a UUID column in Supabase — only filter by it when
        # the value is actually a UUID. Service-area document keys like
        # "vehicle_registration" are plain strings and would cause a
        # `invalid input syntax for type uuid` error from PostgREST.
        # For non-UUID keys, fall back to matching by document_type (the
        # requirement name) so we only supersede docs for this specific
        # requirement rather than all of the driver's docs.
        if requirement_id and _is_valid_uuid(requirement_id):
            query["requirement_id"] = requirement_id
        elif document_type:
            query["document_type"] = document_type
        if side is not None:
            query["side"] = side
        await db_supabase.update_one(
            "driver_documents", query, {"status": "superseded", "updated_at": datetime.now(timezone.utc)}
        )
    except Exception as e:
        logger.warning(f"Could not supersede prior docs for driver {driver_id}: {e}")

    if not flag_review:
        # Admin uploaded an already-approved doc — supersede prior docs but keep
        # the driver in their current state (don't take an active driver offline).
        return

    # Set driver to needs_review so they can't go online until admin re-approves.
    try:
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("status") == "active":
            await db_supabase.update_one(
                "drivers",
                {"id": driver_id},
                {
                    "status": "needs_review",
                    "is_online": False,
                    "is_available": False,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
        else:
            await db_supabase.update_one("drivers", {"id": driver_id}, {"updated_at": datetime.now(timezone.utc)})
    except Exception as e:
        logger.warning(f"Could not flag driver {driver_id} for review: {e}")


def _extract_signed_url(res: Any) -> str:
    """
    Pull the URL out of supabase-py's create_signed_url() response, tolerating
    both the legacy object-with-`.data.signed_url` shape and the current dict
    shape with a `signedURL` / `signedUrl` / `signed_url` key. Railway was
    500-ing with "'dict' object has no attribute 'data'" after supabase-py
    flipped the return type.
    """
    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        if not url:
            raise RuntimeError(f"create_signed_url returned no URL: {res!r}")
        return url
    data = getattr(res, "data", None)
    if data is None:
        raise RuntimeError(f"create_signed_url returned unexpected shape: {res!r}")
    if isinstance(data, dict):
        url = data.get("signedURL") or data.get("signedUrl") or data.get("signed_url")
    else:
        url = getattr(data, "signed_url", None) or getattr(data, "signedURL", None)
    if not url:
        raise RuntimeError(f"create_signed_url missing URL field: {res!r}")
    return url


_STORAGE_KEY_RE = _re.compile(r"/storage/v1/object/(?:sign|public)/driver-documents/([^?#]+)")


def _extract_storage_key(stored_url: str) -> Optional[str]:
    """Extract the bare storage-object key from a Supabase Storage URL.

    Handles both signed and public URL shapes, regardless of which Supabase
    project host they were originally generated for.
    """
    m = _STORAGE_KEY_RE.search(stored_url or "")
    return m.group(1) if m else None


def regenerate_signed_url(stored_url: str, expires_in: int = 3600) -> str:
    """Return a fresh signed URL for a driver-document object.

    If the stored URL comes from the old Supabase project (or if the
    signature has expired), the current Supabase client generates a new one
    pointing at the currently-configured project. Falls back to the stored
    URL if the storage key cannot be extracted or the Supabase client is
    unavailable.
    """
    if not supabase:
        return stored_url
    storage_key = _extract_storage_key(stored_url)
    if not storage_key:
        return stored_url
    try:
        res = supabase.storage.from_("driver-documents").create_signed_url(storage_key, expires_in)
        return _extract_signed_url(res)
    except Exception as exc:
        logger.warning("regenerate_signed_url failed for key=%s: %s", storage_key, exc)
        return stored_url


async def save_upload(file: UploadFile) -> str:
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"

    try:
        file_bytes = await file.read()
        _validate_file_type(file_bytes, file.content_type or "application/octet-stream")
        supabase.storage.from_("driver-documents").upload(
            file=file_bytes, path=filename, file_options={"content-type": file.content_type}
        )

        url_res = supabase.storage.from_("driver-documents").create_signed_url(filename, 3600)
        return _extract_signed_url(url_res)
    except Exception as e:
        logger.error(f"Failed to upload to Supabase Storage: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}") from e


# --- Helpers ---


async def _insert_driver_document(record: dict) -> dict:
    """
    Insert a row into driver_documents, falling back to a minimal set of
    columns if ``requirement_id`` or ``side`` don't exist yet in the live
    Supabase table (i.e. the ALTER TABLE migration hasn't been run yet).
    """
    try:
        result = await db.insert_one("driver_documents", record)
        return result if result else record
    except Exception as e:
        err = str(e)
        # PGRST204 = column not found in schema cache; 42703 = undefined column
        if "PGRST204" in err or "42703" in err or "schema cache" in err:
            logger.warning(
                "driver_documents missing requirement_id/side columns — "
                "falling back to minimal insert. Run the ALTER TABLE migration!"
            )
            minimal = {
                "id": record["id"],
                "driver_id": record["driver_id"],
                "document_type": record.get("document_type"),
                "document_url": record["document_url"],
                "status": record.get("status", "pending"),
                "uploaded_at": record.get("uploaded_at"),
                "updated_at": record.get("updated_at"),
            }
            result = await db.insert_one("driver_documents", minimal)
            return result if result else minimal
        raise


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
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
        )
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
                        "created_at": area.get("created_at", datetime.now(timezone.utc).isoformat()),
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
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        return []  # No driver profile yet — not an error

    documents = await db_supabase.get_rows(
        "driver_documents", {"driver_id": driver["id"]}, limit=100, order="uploaded_at", desc=True
    )
    return documents


@documents_router.post("/documents")
async def link_driver_document(doc_data: LinkDocumentRequest, current_user: dict = Depends(get_current_user)):
    """Link an uploaded document to the current driver."""
    # Look up the driver profile directly rather than trusting the is_driver
    # flag. During onboarding the drivers row may not exist yet, and even right
    # after registration a stale "no driver" cache sentinel can leave is_driver
    # false for the rest of the cache TTL. Guarding on is_driver here made the
    # auto-create path below unreachable — the exact condition it handles (no
    # drivers row) is precisely when is_driver is false — so a driver mid-
    # onboarding always got a 403 "User is not a driver". Mirror the GET
    # sibling and resolve the row from the source of truth instead.
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        # Auto-create a minimal driver row so documents can be uploaded
        # before the driver has completed the vehicle-info step.
        first = current_user.get("first_name", "")
        last = current_user.get("last_name", "")
        driver = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "name": f"{first} {last}".strip() or current_user.get("phone", ""),
            "phone": current_user.get("phone", ""),
            "status": "pending",
            "is_verified": False,
            "is_online": False,
            "is_available": False,
            "rating": 5.0,
            "total_rides": 0,
            "lat": 0,
            "lng": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.insert_one("drivers", driver)
        await db.update_one(
            "users",
            {"id": current_user["id"]},
            {"$set": {"role": "driver", "is_driver": True}},
        )
        logger.info(f"Auto-created driver row for user_id={current_user['id']} during document upload")

    # Validate requirement exists — check global table first (only if the id
    # looks like a UUID; the driver app now sends area-doc slugs like
    # "drivers_license" which would otherwise trip the UUID cast in Postgres
    # and raise 22P02 before we reach the service-area fallback below).
    req = None
    if _is_valid_uuid(doc_data.requirement_id):
        rows = await db_supabase.get_rows("document_requirements", {"id": doc_data.requirement_id}, limit=1)
        req = rows[0] if rows else None
    if not req:
        # Try looking it up from the driver's service area
        area_req = None
        if driver.get("service_area_id"):
            area = (lambda _r: _r[0] if _r else None)(
                await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
            )
            if area:
                required_docs = area.get("required_documents") or []
                logger.info(f"Required documents: {required_docs}")
                area_req = next((d for d in required_docs if d.get("key") == doc_data.requirement_id), None)
                logger.info(f"Area requirement found: {area_req is not None}")

        if area_req:
            # Service area explicitly configured this requirement — synthesise a
            # req-like dict so downstream code works uniformly.
            req = {
                "id": area_req.get("key"),
                "name": area_req.get("label", doc_data.requirement_id),
                "requires_back_side": area_req.get("requires_back_side", False),
            }
        else:
            # Fallback: allow common document types even if not configured in the
            # service area. This handles areas that haven't been set up yet.
            # NOTE: this branch must NOT fall through to an area_req.get(...) —
            # area_req is None here, and doing so raised AttributeError (500) on
            # every common-requirement upload (e.g. a driver's licence uploaded
            # before the service area configured its required documents).
            common_requirements = {
                "drivers_license": {"name": "Driver's License", "requires_back_side": False},
                "vehicle_insurance": {"name": "Vehicle Insurance", "requires_back_side": False},
                "vehicle_registration": {"name": "Vehicle Registration", "requires_back_side": False},
                "background_check": {"name": "Background Check", "requires_back_side": False},
                "vehicle_inspection": {"name": "Vehicle Inspection", "requires_back_side": False},
            }

            if doc_data.requirement_id in common_requirements:
                logger.warning(f"Using fallback for common requirement: {doc_data.requirement_id}")
                req = {
                    "id": doc_data.requirement_id,
                    "name": common_requirements[doc_data.requirement_id]["name"],
                    "requires_back_side": common_requirements[doc_data.requirement_id]["requires_back_side"],
                }
            else:
                logger.error(
                    f"Requirement '{doc_data.requirement_id}' not found in global table, service area, or common types"
                )
                raise HTTPException(status_code=404, detail=f"Requirement '{doc_data.requirement_id}' not found")

    # Supersede any prior docs for this requirement+side and flip the
    # driver back to unverified so admin re-reviews this upload.
    await _supersede_and_flag_pending_review(
        driver["id"],
        doc_data.requirement_id,
        doc_data.side,
        document_type=doc_data.document_type,
    )

    # Create document record.
    # NOTE: Only columns that exist on the Supabase driver_documents table —
    # writing `expiry_date` here raises PGRST204. Expiry is stored on the
    # drivers row via admin approval in the legacy *_expiry_date columns.
    # NOTE: requirement_id is a UUID column in Supabase. Service-area doc keys
    # (e.g. "vehicle_registration") are plain strings — store None to avoid a
    # `invalid input syntax for type uuid` error from PostgREST.
    req_id_for_db = doc_data.requirement_id if _is_valid_uuid(doc_data.requirement_id) else None
    # Always persist the raw requirement key (e.g. "vehicle_registration") so the
    # admin panel can match documents to service-area requirements even when the
    # requirement_id column holds NULL (non-UUID service-area keys).
    requirement_key = doc_data.requirement_id if not _is_valid_uuid(doc_data.requirement_id) else None
    doc_record = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "requirement_id": req_id_for_db,
        "requirement_key": requirement_key,
        "document_type": doc_data.document_type,
        "document_url": doc_data.document_url,
        "side": doc_data.side,
        "status": "pending",
        "uploaded_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    try:
        await db_supabase.insert_one("driver_documents", doc_record)
    except Exception as e:
        err = str(e)
        # requirement_key column may not exist yet (migration 28 pending).
        # Fall back to inserting without it so uploads aren't broken.
        if "requirement_key" in err or "PGRST204" in err or "42703" in err:
            logger.warning("requirement_key column missing — inserting without it. Run migration 28.")
            doc_record_fallback = {k: v for k, v in doc_record.items() if k != "requirement_key"}
            await db_supabase.insert_one("driver_documents", doc_record_fallback)
        else:
            raise
    # Stash the admin-facing expiry on the response so the caller can
    # display it back, without persisting a non-existent column.
    if doc_data.expiry_date:
        doc_record["expiry_date"] = doc_data.expiry_date.isoformat()
    return doc_record


@documents_router.post("/documents/upload")
async def upload_driver_document(
    file: UploadFile = File(...),
    requirement_id: str = Form(...),
    side: Optional[str] = Form(None),  # 'front' or 'back'
    expiry_date: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Upload a specific document linked to a requirement.

    driver_id is derived from the authenticated user — clients must not
    supply it. Accepting a caller-supplied driver_id would allow any
    authenticated user to attach documents to arbitrary driver accounts.
    """
    drv_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    drv_profile = drv_rows[0] if drv_rows else None
    if not drv_profile:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    driver_id = drv_profile["id"]

    # storage logic
    url = await save_upload(file)

    # Validate requirement — check global table first, then service area docs.
    req = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("document_requirements", {"id": requirement_id}, limit=1)
    )
    if not req:
        area_req = None
        if driver_id:
            drv = drv_profile
            if drv and drv.get("service_area_id"):
                area = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("service_areas", {"id": drv["service_area_id"]}, limit=1)
                )
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
    await _supersede_and_flag_pending_review(
        driver_id,
        requirement_id,
        side,
        document_type=req.get("name"),
    )

    # Create document record.
    # NOTE: Only columns that exist on the Supabase driver_documents table —
    # `expiry_date` is intentionally NOT written to this row (column doesn't
    # exist, would cause PGRST204). Expiry lives in the drivers row legacy
    # columns, refreshed on admin approval.
    # NOTE: requirement_id is a UUID column in Supabase. Service-area doc keys
    # (e.g. "vehicle_registration") are plain strings — store None to avoid a
    # `invalid input syntax for type uuid` error from PostgREST.
    req_id_for_db = requirement_id if _is_valid_uuid(requirement_id) else None
    doc_record = {
        "id": str(uuid.uuid4()),
        "driver_id": driver_id,
        "requirement_id": req_id_for_db,
        "document_type": req.get("name"),  # Denormalize name for easy display
        "document_url": url,
        "side": side,
        "status": "pending",
        "uploaded_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    await db_supabase.insert_one("driver_documents", doc_record)

    # Return the admin-facing expiry as part of the response without
    # persisting it to a non-existent column.
    if expiry_iso:
        doc_record["expiry_date"] = expiry_iso
    return doc_record


@documents_router.delete("/documents/{document_id}")
async def delete_driver_document(
    document_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Remove one of the authenticated driver's uploaded documents.

    Drivers can delete pending documents (e.g. wrong side uploaded,
    replaced with a clearer photo). Once the document is approved by
    an admin the delete is blocked — approved records must be kept
    for audit and are rotated only by uploading a replacement that
    supersedes them via the requirement linkage.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    doc = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("driver_documents", {"id": document_id}, limit=1)
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this document")

    if doc.get("status") == "approved":
        raise HTTPException(
            status_code=409,
            detail="Approved documents cannot be deleted. Upload a replacement to supersede it.",
        )

    await db_supabase.delete_many("driver_documents", {"id": document_id})
    return {"success": True}


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
        "created_at": datetime.now(timezone.utc),
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

    return (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("document_requirements", {"id": req_id}, limit=1)
    )


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
    documents = await db_supabase.get_rows(
        "driver_documents", {"driver_id": driver_id}, limit=100, order="uploaded_at", desc=True
    )
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
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("driver_documents", {"id": doc_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # Only write columns that exist on the driver_documents Supabase table.
    # `expiry_date` is NOT a column — we propagate it to the legacy
    # drivers.*_expiry_date column below instead.
    update_data: Dict[str, Any] = {"status": req.status, "updated_at": datetime.now(timezone.utc)}
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
        req_row = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("document_requirements", {"id": existing.get("requirement_id")}, limit=1)
        )
        req_name = req_row.get("name") if req_row else None

        if not req_name:
            # Try the service area's required_documents
            driver = await db_supabase.get_driver_by_id(existing.get("driver_id"))
            if driver and driver.get("service_area_id"):
                area = (lambda _r: _r[0] if _r else None)(
                    await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
                )
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
                await db_supabase.update_one(
                    "drivers",
                    {"id": existing.get("driver_id")},
                    {legacy_field: new_val, "updated_at": datetime.now(timezone.utc)},
                )
            except Exception as e:
                logger.warning(
                    f"Could not update legacy expiry field {legacy_field} for driver {existing.get('driver_id')}: {e}"
                )

    return (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("driver_documents", {"id": doc_id}, limit=1))


# --- File Serving Router ---
files_router = APIRouter(prefix="/documents", tags=["Files"])

# --- Generic Upload Router (no prefix so it mounts at /api/v1/upload) ---
upload_router = APIRouter(tags=["Upload"])


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class FileTooLargeError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="File too large (max 10MB)")


@upload_router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Generic file upload endpoint.

    Uploads directly to Supabase Storage (driver-documents bucket) and
    returns the public URL. The previous base64-in-DB approach caused
    2+ minute timeouts for large images.
    """
    logger.info(
        "[upload] hit /api/v1/upload user=%s filename=%s content_type=%s",
        (current_user or {}).get("id"),
        getattr(file, "filename", None),
        getattr(file, "content_type", None),
    )
    try:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            raise FileTooLargeError()

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")

        # 10 MB hard cap -- documents are usually photos/PDFs
        if len(content) > MAX_FILE_SIZE:
            raise FileTooLargeError()

        size = len(content)
        original_filename = file.filename or "upload"
        content_type = file.content_type or "application/octet-stream"

        _validate_file_type(content, content_type)

        # Validate file extension against allowlist (12-11)
        ext = Path(original_filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")

        # Preserve extension so Supabase serves the object with a sensible
        # content-type when the browser fetches the public URL.
        storage_key = f"{uuid.uuid4()}{ext}"
        # Generate a safe display name from doc type and timestamp — never
        # echo the original filename back to the caller (12-8).
        upload_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_name = f"document_{upload_ts}{ext}"

        try:
            logger.info("[upload] storage.upload begin key=%s size=%s", storage_key, size)
            upload_res = supabase.storage.from_("driver-documents").upload(
                file=content,
                path=storage_key,
                file_options={"content-type": content_type},
            )
            logger.info("[upload] storage.upload done type=%s", type(upload_res).__name__)
            signed_res = supabase.storage.from_("driver-documents").create_signed_url(storage_key, 3600)
            logger.info(
                "[upload] create_signed_url returned type=%s repr=%r",
                type(signed_res).__name__,
                signed_res,
            )
            public_url = _extract_signed_url(signed_res)
        except Exception as e:
            logger.exception("Supabase Storage upload failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Storage upload failed: {e}") from e

        return {
            "success": True,
            "url": public_url,
            "file_id": storage_key,
            "filename": safe_name,
            "content_type": content_type,
            "size": size,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e


@files_router.get("/{file_id}")
async def get_document_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Serve a driver document file by ID.

    Requires authentication.  Admins may access any document; drivers may
    only access their own.
    """
    doc = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("driver_documents", {"id": file_id}, limit=1))
    if not doc or not doc.get("document_url"):
        raise HTTPException(status_code=404, detail="File not found")

    # Admin-ness is the _admin_verified marker set only by _verify_admin_payload
    # after the full admin pipeline — never the users.role column, which an
    # ordinary rider/driver token also carries (see get_admin_user).
    if not current_user.get("_admin_verified"):
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if not driver:
            raise HTTPException(status_code=404, detail="Driver profile not found")
        if doc.get("driver_id") != driver["id"]:
            raise HTTPException(status_code=403, detail="Not authorized to access this document")

    from fastapi.responses import RedirectResponse

    return RedirectResponse(doc["document_url"])
