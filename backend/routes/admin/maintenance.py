import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ...dependencies import get_admin_user, require_module
except ImportError:
    from dependencies import get_admin_user, require_module  # type: ignore

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)


async def log_audit(
    action: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    details: str = "",
) -> None:
    """Write a single row to the audit_logs table.

    Non-raising: callers wrap this in try/except so a DB hiccup never
    blocks the parent operation. Failures are logged at WARNING level.
    """
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "actor_id": actor,
                "details": details,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.error(
            f"[AUDIT] log_audit({action}, {entity_type}, {entity_id}) failed: {exc}",
            exc_info=True,
            extra={"domain": "admin"},
        )


router = APIRouter()

# ── GPS Location History Cleanup ──


@router.post("/maintenance/cleanup-location-history")
async def admin_cleanup_location_history(days: int = Query(30, ge=7, le=1095)):
    """Delete old driver_location_history rows.

    By default deletes rows older than 30 days. On ride completion the
    aggregated data (phase distances, route polyline) is already stored on
    the ride row, so the raw GPS points are only needed for recent disputes.

    Idle (online_idle) points use the configurable retention from
    settings.idle_breadcrumb_retention_hours (default 2160h = 90 days per the
    owner's 2026-08-18 decision — idle history now feeds per-day Distance
    Logs and map replay; the old hardcoded 24h purge silently destroyed it).
    """
    now = datetime.now(timezone.utc)
    cutoff_historical = (now - timedelta(days=days)).isoformat()
    idle_hours = 2160
    try:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        idle_hours = int(((await get_app_settings()) or {}).get("idle_breadcrumb_retention_hours", 2160))
    except Exception:
        logger.error("idle retention setting read failed; using 2160h default", exc_info=True)
    cutoff_idle = (now - timedelta(hours=max(1, idle_hours))).isoformat()

    deleted_historical = -1
    deleted_idle = -1
    try:
        await db_supabase.delete_many("driver_location_history", {"timestamp": {"$lt": cutoff_historical}})
    except Exception as e:
        logger.error(f"Cleanup historical GPS failed: {e}", exc_info=True)

    try:
        await db_supabase.delete_many(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_idle}, "tracking_phase": "online_idle"},
        )
    except Exception as e:
        logger.error(f"Cleanup idle GPS failed: {e}", exc_info=True)

    logger.info("[CLEANUP] Deleted historical + idle GPS points (counts not tracked — direct delete)")
    return {
        "deleted_historical": deleted_historical,
        "deleted_idle": deleted_idle,
        "historical_cutoff": cutoff_historical,
        "idle_cutoff": cutoff_idle,
    }


@router.post("/maintenance/rollup-driver-daily")
async def admin_rollup_driver_daily(target_date: Optional[str] = None):
    """Roll up driver activity for one Regina business day into driver_daily_stats.

    Delegates to utils/driver_daily_rollup.rollup_driver_day — the same core
    the scheduled 30-min loop runs — so the manual admin trigger and the
    loop share one day definition (America/Regina, day_tz='regina') and one
    field list (v2 per-phase km + seconds). Idempotent upsert per driver-day.
    """
    try:
        from ...utils.driver_activity import REGINA_TZ
        from ...utils.driver_daily_rollup import rollup_driver_day
    except ImportError:
        from utils.driver_activity import REGINA_TZ  # type: ignore
        from utils.driver_daily_rollup import rollup_driver_day  # type: ignore

    regina_today = datetime.now(timezone.utc).astimezone(REGINA_TZ).date()
    if target_date:
        stat_date = datetime.fromisoformat(target_date).date()
        # Completed days only: a partial-day rollup would make MAX(stat_date)
        # claim today is covered, so the leaderboard's freshness top-up
        # (routes/drivers/referrals.py::get_driver_leaderboard, keyed off
        # day-after MAX(stat_date)) would silently drop every ride completed
        # after the rollup ran until the next pass. "Today" is now the REGINA
        # date — between 00:00 and 06:00 UTC the UTC calendar is already a
        # day ahead of Saskatchewan.
        if stat_date >= regina_today:
            raise HTTPException(
                status_code=422,
                detail="target_date must be a completed America/Regina day (yesterday or earlier)",
            )
    else:
        stat_date = regina_today - timedelta(days=1)

    return await rollup_driver_day(stat_date)


# ============================================================
# Audit Logs
# ============================================================


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = Query(50),
    offset: int = Query(0),
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    _admin: dict = Depends(require_module("audit")),
):
    """Get audit log entries with optional filters and pagination."""
    filters: Dict[str, Any] = {}
    if action:
        filters["action"] = action
    if entity_type:
        filters["entity_type"] = entity_type
    if search:
        term = search.strip()
        if term:
            # entity_id is the column every current writer (log_admin_action,
            # log_user_action, the PII-reveal endpoint below) actually
            # populates — resource_id is the pre-migration-57 legacy column
            # that nothing writes anymore. Searching resource_id silently
            # matched zero modern rows instead of erroring, so this was a
            # quiet SOC search gap, not a crash.
            filters["$or"] = [
                {"actor_id": {"$regex": term, "$options": "i"}},
                {"entity_id": {"$regex": term, "$options": "i"}},
                {"details": {"$regex": term, "$options": "i"}},
            ]
    logs = await db_supabase.get_rows("audit_logs", filters, order="created_at", desc=True, limit=limit, offset=offset)
    return logs


@router.get("/audit-logs/top-actors")
async def get_audit_log_top_actors(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=200),
    _admin: dict = Depends(require_module("audit")),
):
    """Corporate + admin portal review, round 2: "no 'who touched the
    most' rollup views — every threat hunt needs raw SQL." Aggregates
    audit_logs by actor over a bounded recent window so a SOC investigation
    doesn't start from a blank raw-SQL prompt every time.

    Same shape as monitoring.py::get_email_deliverability (bounded days
    window, single get_rows fetch capped at 5000, Counter aggregation in
    Python) rather than a Postgres GROUP BY RPC — this table doesn't have
    one, and standing one up is out of scope for a SOC convenience view.
    """
    from collections import Counter

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db_supabase.get_rows(
        "audit_logs",
        {"created_at": {"$gte": since}},
        order="created_at",
        desc=True,
        limit=5000,
    )
    by_actor: Counter = Counter()
    actions_by_actor: Dict[str, Counter] = {}
    for row in rows:
        actor = row.get("actor_id") or "unknown"
        by_actor[actor] += 1
        actions_by_actor.setdefault(actor, Counter())[row.get("action") or "unknown"] += 1

    top = [
        {
            "actor_id": actor,
            "action_count": count,
            "top_actions": [
                {"action": action, "count": action_count}
                for action, action_count in actions_by_actor[actor].most_common(5)
            ],
        }
        for actor, count in by_actor.most_common(limit)
    ]
    return {
        "days": days,
        "window_start": since,
        "rows_scanned": len(rows),
        "rows_scanned_capped": len(rows) >= 5000,
        "actors": top,
    }


class PiiRevealRequest(BaseModel):
    entity_type: str
    entity_id: str


@router.post("/audit/pii-reveal")
async def admin_log_pii_reveal(
    body: PiiRevealRequest,
    admin: dict = Depends(get_admin_user),
):
    """Log when an admin reveals PII for a specific entity (PIPEDA audit trail)."""
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "pii_revealed",
            "entity_type": body.entity_type,
            "entity_id": body.entity_id,
            "details": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"ok": True}
