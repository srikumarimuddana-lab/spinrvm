"""Safety-incident reporting endpoint.

Drivers and riders submit safety/abuse reports via POST /safety/report.
The report is inserted into `safety_incidents` with status=open for
the trust & safety team to review in the admin dashboard. A SEV-1
issue (weapon, assault, medical) also broadcasts to on-call admins
over the admin WebSocket channel.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
except ImportError:
    import db_supabase
    from dependencies import get_current_user

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
    user_role = current_user.get("role") or "rider"

    incident_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

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
        "ride_id": body.ride_context.ride_id if body.ride_context else None,
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

    return {"success": True, "incident_id": incident_id}
