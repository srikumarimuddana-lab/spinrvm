"""Admin route for the migration data-quality scan tool (owner-confirmed
2026-08-31 investigation, docs/runbooks/migration-data-quality-strategy.md).

Two endpoints, no file upload -- like pre_launch_flag.py, this tool operates
entirely on already-migrated production data (the `rides` table), not an
uploaded CSV:

- ``POST /api/admin/legacy/data-quality-scan/preview`` -- read-only plan, no
  writes.
- ``POST /api/admin/legacy/data-quality-scan/commit`` -- re-plans fresh
  (same idempotent re-plan pattern every other importer's commit endpoint
  uses) and, if there's anything to flag, applies it via
  ``migration_data_quality_service.apply_data_quality_flags``.

Additive only: merges into ``legacy_import_metadata.data_quality.issues`` on
matched rows. Never deletes, never touches ``rides.status``. See
``services/migration_data_quality_service.py``'s module docstring for the
four issue categories and why they don't reclassify ride state.

super_admin only, matching pre_launch_flag.py's posture -- this is a bulk
write across the core `rides` table, not scoped to one admin module.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import migration_data_quality_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        data_quality_scan_commit_limit,
        data_quality_scan_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import migration_data_quality_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        data_quality_scan_commit_limit,
        data_quality_scan_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Migration data-quality scan requires super_admin")


async def _build_plan() -> svc.DataQualityScanPlan:
    """build_data_quality_scan_plan does its own Supabase reads
    synchronously, so it runs in a worker thread to avoid blocking the event
    loop -- same reasoning as every other importer's _build_plan."""
    return await asyncio.to_thread(svc.build_data_quality_scan_plan)


def _report(plan: svc.DataQualityScanPlan, batch: str) -> dict:
    return {
        "batch": batch,
        "counts": dict(plan.stats),
        "can_commit": plan.stats.get("rides_affected", 0) > 0,
    }


@router.post("/legacy/data-quality-scan/preview")
@data_quality_scan_preview_limit
async def preview_data_quality_scan(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build the plan and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()
    return _report(plan, batch)


@router.post("/legacy/data-quality-scan/commit")
@data_quality_scan_commit_limit
async def commit_data_quality_scan(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-plan fresh and, if there's anything to flag, apply it."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()

    report = _report(plan, batch)
    if not report["can_commit"]:
        return {**report, "committed": False}

    try:
        conflicts = await asyncio.to_thread(svc.apply_data_quality_flags, plan, batch=batch)
    except Exception as e:
        logger.error("data-quality scan commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Flag commit failed; some rows may have been flagged") from e

    flagged_rides = len(plan.candidates) - len(conflicts)

    await log_admin_action(
        admin,
        "migration_data_quality_flag",
        "rides",
        batch,
        {
            "rides_flagged": flagged_rides,
            "conflicts": len(conflicts),
            "issue_counts": {k: v for k, v in plan.stats.items() if k != "rides_affected"},
        },
    )
    return {
        **report,
        "committed": True,
        "rides_flagged": flagged_rides,
        "conflicts": len(conflicts),
    }
