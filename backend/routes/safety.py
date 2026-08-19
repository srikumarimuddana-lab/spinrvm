"""Safety-incident reporting endpoint.

Drivers and riders submit safety/abuse reports via POST /safety/report.
The report is inserted into `safety_incidents` with status=open for
the trust & safety team to review in the admin dashboard. A SEV-1
issue (weapon, assault, medical) also broadcasts to on-call admins
over the admin WebSocket channel.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..documents import _resolve_upload_type, read_upload_capped
    from ..features import notify_safety_team
    from ..services.zoho_desk_integration import create_ticket_for_safety
    from ..supabase_client import supabase
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from documents import _resolve_upload_type, read_upload_capped  # type: ignore
    from features import notify_safety_team
    from services.zoho_desk_integration import create_ticket_for_safety
    from supabase_client import supabase  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/safety", tags=["Safety"])


class SafetyLocation(BaseModel):
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    timestamp: Optional[str] = None


class SafetyRideContext(BaseModel):
    ride_id: str
    pickup_location: Optional[str] = None
    dropoff_location: Optional[str] = None
    rider_id: Optional[str] = None


class SafetyReportRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=4000)
    location: Optional[SafetyLocation] = None
    ride_context: Optional[SafetyRideContext] = None
    reported_at: Optional[str] = None


@api_router.post("/report")
async def submit_safety_report(
    body: SafetyReportRequest,
    current_user: dict = Depends(get_current_user),
):
    """Record a safety incident submitted by a driver or rider."""
    user_id = current_user.get("id")
    user_role = "driver" if current_user.get("is_driver") else "rider"

    incident_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # WS-18: if the report references a ride, verify the caller is a party.
    verified_ride_id = None
    if body.ride_context and body.ride_context.ride_id:
        _ride = await db_supabase.get_ride(body.ride_context.ride_id)
        if _ride:
            _is_rider = _ride.get("rider_id") == user_id
            _driver_row = None
            if not _is_rider:
                _driver_rows = await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1)
                _driver_row = _driver_rows[0] if _driver_rows else None
            _is_driver = bool(_driver_row) and _ride.get("driver_id") == _driver_row["id"]
            if _is_rider or _is_driver:
                verified_ride_id = body.ride_context.ride_id

    incident = {
        "id": incident_id,
        "reported_by_user_id": user_id,
        "role": user_role,
        "category": body.category,
        "description": body.description,
        "status": "open",
        "latitude": body.location.latitude if body.location else None,
        "longitude": body.location.longitude if body.location else None,
        "location_accuracy": body.location.accuracy if body.location else None,
        "ride_id": verified_ride_id,
        "reported_at": body.reported_at or now,
        "created_at": now,
    }

    try:
        await db_supabase.insert_one("safety_incidents", incident)
    except Exception as exc:
        logger.error(
            f"[SAFETY] Failed to persist incident user_id={user_id} category={body.category}: {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Unable to record report. Please try again.") from exc

    logger.info(
        f"[SAFETY] Incident {incident_id} reported by {user_role} {user_id} "
        f"category={body.category} ride_id={incident['ride_id']}"
    )

    # Raise a Zoho Desk ticket (urgent) for the safety team — fire-and-forget,
    # no-op when the integration is disabled; never blocks the report.
    asyncio.create_task(create_ticket_for_safety(incident))

    # Notify the safety team — WS broadcast to admin dashboard + email
    # to the configured distribution list + CRITICAL log line for
    # on-call paging. Best-effort: failures are logged but don't 5xx
    # the user (their report is already persisted, that's the
    # important part).
    try:
        notify_result = await notify_safety_team(incident)
        logger.info(f"[SAFETY] Incident {incident_id} notify_safety_team result={notify_result}")
    except Exception:
        logger.error(
            f"[SAFETY] notify_safety_team unexpected failure for incident {incident_id}",
            exc_info=True,
        )

    return {"success": True, "incident_id": incident_id}


# ---------------------------------------------------------------------------
# Evidence photos
# ---------------------------------------------------------------------------

# Matches the driver app's own picker limit (report-safety.tsx caps selection
# at 4). Enforced server-side too so a replayed request can't grow unbounded.
MAX_INCIDENT_PHOTOS = 4

_SAFETY_BUCKET = "safety-evidence"


@api_router.post("/report/{incident_id}/photo")
async def upload_safety_report_photo(
    incident_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Attach an evidence photo to a safety incident.

    The driver app has been calling this route since it shipped; it was never
    implemented, and the client swallowed the 404 as "non-fatal", so evidence
    photos were silently dropped. Errors here are returned to the caller rather
    than swallowed — losing safety evidence quietly is the failure mode this is
    fixing.
    """
    user_id = current_user.get("id")

    rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = rows[0]

    # Only the person who filed the report may attach to it. Evidence can
    # depict a third party, so this must not be open to any authenticated user
    # who guesses an incident id. Admins attach through the admin surface.
    if incident.get("reported_by_user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your report")

    existing = await db_supabase.get_rows(
        "safety_incident_photos", {"incident_id": incident_id}, limit=MAX_INCIDENT_PHOTOS + 1
    )
    if len(existing) >= MAX_INCIDENT_PHOTOS:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_INCIDENT_PHOTOS} photos per report"
        )

    content = await read_upload_capped(file)
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Bytes are authoritative — expo-image-picker labels everything image/jpeg.
    content_type, ext = _resolve_upload_type(content, file.content_type or "application/octet-stream")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image attachments are supported")

    if not supabase:
        raise HTTPException(status_code=503, detail="Storage unavailable")

    storage_key = f"{incident_id}/{uuid.uuid4()}{ext}"
    try:
        await db_supabase.run_sync(
            lambda: supabase.storage.from_(_SAFETY_BUCKET).upload(
                file=content,
                path=storage_key,
                file_options={"content-type": content_type},
            )
        )
    except Exception as exc:
        logger.error(
            f"[SAFETY] evidence upload failed incident={incident_id} key={storage_key}: {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="Could not store the photo. Please try again.") from exc

    photo = await db_supabase.insert_one(
        "safety_incident_photos",
        {
            "id": str(uuid.uuid4()),
            "incident_id": incident_id,
            "storage_key": storage_key,
            "content_type": content_type,
            "size_bytes": len(content),
            "uploaded_by_user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    logger.info(f"[SAFETY] evidence photo attached incident={incident_id} bytes={len(content)}")
    return {"success": True, "photo_id": (photo or {}).get("id")}
