"""Admin safety queue.

Lists + updates ``safety_incidents`` rows so an admin can triage SOS
reports, driver safety reports, and auto-escalations from the
check-in loop in one place.

Authentication: gated by the parent admin_router via
``Depends(get_admin_user)`` + ``require_module("support")`` (see
routes/admin/__init__.py wiring at module mount time).
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
    from .drivers import _batch_fetch_drivers_and_users, _user_display_name
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # type: ignore
    from routes.admin.drivers import (  # type: ignore
        _batch_fetch_drivers_and_users,
        _user_display_name,
    )
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/safety", tags=["Admin · Safety"])


# ---------- List ----------

# Status values that surface as "needs attention" on the queue —
# default scope when no explicit status filter is passed.
_OPEN_STATUSES = ("open", "in_progress")

# Columns the queue table actually renders. Project these instead of SELECT *
# so the list query only pulls what the page needs; the single-incident detail
# endpoint returns the full row for the triage drawer.
_LIST_COLUMNS = "id,reported_by_user_id,role,category,status,severity,ride_id,assigned_to_admin_id,reported_at"


@router.get("/incidents")
async def admin_list_safety_incidents(
    status: Optional[str] = Query(
        None,
        description="open | in_progress | resolved | closed | duplicate. Omit for default 'open + in_progress' queue.",
    ),
    severity: Optional[Literal["sev1", "sev2", "sev3"]] = None,
    role: Optional[Literal["rider", "driver", "system"]] = None,
    category: Optional[str] = None,
    ride_id: Optional[str] = None,
    search: Optional[str] = Query(None, description="Matches description (case-insensitive substring)."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated list of safety incidents, newest-reported first.

    Enriches each row with reporter_name (from drivers + users tables in
    two batched queries) so the admin queue doesn't have to N+1.
    """
    # Build the filter. None of these are sensitive; if a category is
    # supplied we pass it through as-is rather than restricting to a
    # closed set — new abuse taxonomies should not need a backend deploy.
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    else:
        filters["status"] = {"$in": list(_OPEN_STATUSES)}
    if severity:
        filters["severity"] = severity
    if role:
        filters["role"] = role
    if category:
        filters["category"] = category
    if ride_id:
        filters["ride_id"] = ride_id
    if search and search.strip():
        # Push search into the DB filter (case-insensitive substring on
        # description) so it matches across the whole result set, not just the
        # rows that happened to land on the current page.
        filters["description"] = {"$regex": search.strip(), "$options": "i"}

    try:
        # Fetch exactly one page via the DB's native offset — project only the
        # columns the queue renders. (Previously this fetched limit+offset rows
        # and sliced in Python, so deeper pages read ever-larger result sets.)
        page = await db_supabase.get_rows(
            "safety_incidents",
            filters,
            columns=_LIST_COLUMNS,
            order="reported_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.error("safety_incidents list query failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Could not load safety queue.") from None

    # Batch-fetch reporter names so the queue table doesn't have to N+1.
    reporter_ids = list({r.get("reported_by_user_id") for r in page if r.get("reported_by_user_id")})
    _drivers_map, users_map = await _batch_fetch_drivers_and_users([], [])
    if reporter_ids:
        # Only the reporter's display name is used — project the columns
        # _user_display_name reads so base64 profile_image stays out of the read.
        users_list = await db_supabase.get_rows(
            "users",
            {"id": {"$in": reporter_ids}},
            columns="id,first_name,last_name,email,phone",
            limit=len(reporter_ids),
        )
        users_map = {u["id"]: u for u in users_list if u.get("id")}

    enriched: List[Dict[str, Any]] = []
    for r in page:
        reporter = users_map.get(r.get("reported_by_user_id")) if r.get("reported_by_user_id") else None
        enriched.append(
            {
                **r,
                "reporter_name": _user_display_name(reporter) if reporter else None,
            }
        )

    # Total rows matching the current filters — drives the pagination control.
    # Exact COUNT, never a row fetch.
    try:
        total = await db_supabase.count_documents("safety_incidents", filters)
    except Exception:
        logger.error("safety_incidents count query failed", exc_info=True)
        total = offset + len(page)

    # Open count for the sidebar badge / headline stat — a COUNT over the open
    # status set, independent of the current filters (previously this fetched up
    # to 1000 full rows just to len() them).
    try:
        open_count = await db_supabase.count_documents("safety_incidents", {"status": {"$in": list(_OPEN_STATUSES)}})
    except Exception:
        open_count = None

    return {
        "items": enriched,
        "total": total,
        "offset": offset,
        "limit": limit,
        "open_count": open_count,
    }


# ---------- Single ----------


@router.get("/incidents/{incident_id}")
async def admin_get_safety_incident(incident_id: str):
    """Detail view: incident row + reporter snapshot + ride summary."""
    rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = rows[0]

    reporter = None
    if incident.get("reported_by_user_id"):
        try:
            user_rows = await db_supabase.get_rows("users", {"id": incident["reported_by_user_id"]}, limit=1)
            reporter = user_rows[0] if user_rows else None
        except Exception:
            logger.error("safety_incidents reporter lookup failed", exc_info=True)

    ride_summary = None
    if incident.get("ride_id"):
        try:
            ride_rows = await db_supabase.get_rows("rides", {"id": incident["ride_id"]}, limit=1)
            ride = ride_rows[0] if ride_rows else None
            if ride:
                ride_summary = {
                    "id": ride.get("id"),
                    "ride_code": ride.get("ride_code"),
                    "status": ride.get("status"),
                    "rider_id": ride.get("rider_id"),
                    "driver_id": ride.get("driver_id"),
                    "pickup_address": ride.get("pickup_address"),
                    "dropoff_address": ride.get("dropoff_address"),
                    "total_fare": ride.get("total_fare"),
                    "started_at": ride.get("ride_started_at"),
                    "completed_at": ride.get("ride_completed_at"),
                }
        except Exception:
            logger.error("safety_incidents ride snapshot lookup failed", exc_info=True)

    return {
        "incident": incident,
        "reporter": {
            "id": reporter.get("id") if reporter else None,
            "name": _user_display_name(reporter) if reporter else None,
            "email": reporter.get("email") if reporter else None,
            "phone": reporter.get("phone") if reporter else None,
            "role": reporter.get("role") if reporter else None,
        }
        if reporter
        else None,
        "ride": ride_summary,
    }


# ---------- Create ----------


class SafetyIncidentCreate(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=4000)
    role: Literal["rider", "driver", "system"] = "rider"
    reported_by_user_id: Optional[str] = None
    ride_id: Optional[str] = None
    severity: Optional[Literal["sev1", "sev2", "sev3"]] = None
    reported_at: Optional[str] = None


@router.post("/incidents")
async def admin_create_safety_incident(
    body: SafetyIncidentCreate,
    admin: dict = Depends(get_admin_user),
):
    """Manually log a safety incident from the admin side (e.g. a phone
    call or in-person report that never went through the app's own
    POST /safety/report). Corporate + admin portal review, round 2:
    "safety incidents can't be created ... from the admin side."

    Same insert shape as routes/safety.py::submit_safety_report — this is
    the admin-initiated twin of that endpoint, not a separate table or
    workflow. RLS on safety_incidents already restricts INSERT to the
    service role (94_safety_incidents.sql: "admins escalate via the
    backend API, not by writing directly to the table"), which this
    endpoint already does like every other backend write.
    """

    incident_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    incident: Dict[str, Any] = {
        "id": incident_id,
        "reported_by_user_id": body.reported_by_user_id,
        "role": body.role,
        "category": body.category,
        "description": body.description,
        "status": "open",
        "severity": body.severity,
        "ride_id": body.ride_id,
        "reported_at": body.reported_at or now,
        "created_at": now,
    }
    try:
        await db_supabase.insert_one("safety_incidents", incident)
    except Exception:
        logger.error("admin safety_incident create failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Could not create incident.") from None

    try:
        await log_admin_action(
            admin,
            "safety_incident_create",
            "safety_incidents",
            incident_id,
            {"category": body.category, "role": body.role},
        )
    except Exception:
        logger.error("safety_incident create audit log write failed", exc_info=True)

    return {"incident": incident}


# ---------- Update ----------


class SafetyIncidentUpdate(BaseModel):
    status: Optional[Literal["open", "in_progress", "resolved", "closed", "duplicate"]] = None
    severity: Optional[Literal["sev1", "sev2", "sev3"]] = None
    assigned_to_admin_id: Optional[str] = None
    resolution_notes: Optional[str] = Field(default=None, max_length=4000)


@router.patch("/incidents/{incident_id}")
async def admin_update_safety_incident(
    incident_id: str,
    body: SafetyIncidentUpdate,
    admin: dict = Depends(get_admin_user),
):
    """Update the triage state of an incident.

    Status transitions are not enforced server-side — any → any is
    allowed. The CHECK constraint on the column guards against
    nonsense values; semantically the admin UI should restrict
    backward transitions, but operators occasionally need to re-open
    a closed incident.
    """
    existing_rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    if not existing_rows:
        raise HTTPException(status_code=404, detail="Incident not found")
    existing = existing_rows[0]

    updates: Dict[str, Any] = {}
    if body.status is not None:
        updates["status"] = body.status
        # When transitioning to a terminal state, stamp resolved_at +
        # resolved_by from the acting admin. resolution_notes is set
        # separately below if the caller provides one.
        if body.status in ("resolved", "closed") and not existing.get("resolved_at"):
            updates["resolved_at"] = datetime.now(timezone.utc).isoformat()
            updates["resolved_by"] = admin.get("id")
    if body.severity is not None:
        updates["severity"] = body.severity
    if body.assigned_to_admin_id is not None:
        # Caller can pass an empty string to clear the assignment.
        updates["assigned_to_admin_id"] = body.assigned_to_admin_id or None
    if body.resolution_notes is not None:
        updates["resolution_notes"] = body.resolution_notes

    if not updates:
        return {"updated": False, "incident": existing}

    try:
        await db_supabase.update_one("safety_incidents", {"id": incident_id}, updates)
    except Exception:
        logger.error("safety_incident update failed", exc_info=True, extra={"incident_id": incident_id})
        raise HTTPException(status_code=503, detail="Could not update incident.") from None

    # Audit log — never persist the description here (PII), only the
    # field-level diff so we can answer "who set this to closed".
    try:
        await log_admin_action(
            admin,
            "safety_incident_update",
            "safety_incidents",
            incident_id,
            {"updates": {k: v for k, v in updates.items() if k != "resolution_notes"}},
        )
    except Exception:
        logger.error("safety_incident audit log write failed", exc_info=True)

    refreshed_rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    return {"updated": True, "incident": refreshed_rows[0] if refreshed_rows else existing}


# ---------- Merge ----------


class SafetyIncidentMerge(BaseModel):
    canonical_incident_id: str = Field(..., min_length=1)


@router.post("/incidents/{incident_id}/merge")
async def admin_merge_safety_incident(
    incident_id: str,
    body: SafetyIncidentMerge,
    admin: dict = Depends(get_admin_user),
):
    """Mark `incident_id` as a duplicate of `canonical_incident_id`.

    Corporate + admin portal review, round 2: "safety incidents can't be
    ... merged from the admin side" — two reports of the same event
    (rider + driver both SOS the same ride) had no way to be linked.

    Never deletes a row -- safety_incidents is an append-only regulated
    audit record under the SK Transportation Act (94_safety_incidents.sql's
    own table comment: "do not purge"). A merge sets status='duplicate' and
    records merged_into_incident_id (migration 279); the duplicate row
    stays fully intact and queryable.
    """
    if incident_id == body.canonical_incident_id:
        raise HTTPException(status_code=422, detail="Cannot merge an incident into itself.")

    source_rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    if not source_rows:
        raise HTTPException(status_code=404, detail="Incident not found")

    canonical_rows = await db_supabase.get_rows("safety_incidents", {"id": body.canonical_incident_id}, limit=1)
    if not canonical_rows:
        raise HTTPException(status_code=404, detail="Canonical incident not found")
    canonical = canonical_rows[0]

    # Merging into an incident that is itself already merged elsewhere
    # would build a chain instead of a flat star -- point at the chain's
    # actual root so "find every duplicate of X" stays a single-hop query.
    target_id = canonical.get("merged_into_incident_id") or body.canonical_incident_id

    updates = {
        "status": "duplicate",
        "merged_into_incident_id": target_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": admin.get("id"),
    }
    try:
        await db_supabase.update_one("safety_incidents", {"id": incident_id}, updates)
    except Exception:
        logger.error("safety_incident merge failed", exc_info=True, extra={"incident_id": incident_id})
        raise HTTPException(status_code=503, detail="Could not merge incident.") from None

    try:
        await log_admin_action(
            admin,
            "safety_incident_merge",
            "safety_incidents",
            incident_id,
            {"merged_into_incident_id": target_id},
        )
    except Exception:
        logger.error("safety_incident merge audit log write failed", exc_info=True)

    refreshed_rows = await db_supabase.get_rows("safety_incidents", {"id": incident_id}, limit=1)
    return {"merged": True, "incident": refreshed_rows[0] if refreshed_rows else None}
