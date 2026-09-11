"""Admin route for the driver dormancy flagging tool (owner-confirmed
2026-09-11 dormancy criteria discussion).

Two endpoints, no file upload -- like the pre-launch legacy data flagging
tool, this operates entirely on already-migrated production data (the
drivers table), not an uploaded CSV:

- ``POST /api/admin/drivers/dormancy/preview`` -- read-only plan, no
  writes.
- ``POST /api/admin/drivers/dormancy/commit`` -- re-plans fresh (same
  idempotent re-plan pattern every other importer's commit endpoint uses)
  and, if there's anything to flag, applies it via
  ``driver_dormancy_service.apply_dormancy_flags``.

Additive only: sets ``legacy_import_metadata.dormant = true`` (plus
``dormancy_tier``/``dormancy_flag`` audit keys) on matched rows. Never
deletes, deactivates, or suspends anything, and never changes go-online
eligibility. See ``services/driver_dormancy_service.py``'s module
docstring for the exact matching criteria and tier thresholds.

super_admin only, matching every other bulk-write legacy-data tool on
this page -- this is a bulk write across the core `drivers` table, not
scoped to one admin module.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import driver_dormancy_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        driver_dormancy_commit_limit,
        driver_dormancy_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_dormancy_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        driver_dormancy_commit_limit,
        driver_dormancy_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Driver dormancy flagging requires super_admin")


async def _build_plan() -> svc.DormancyPlan:
    """build_dormancy_plan does its own Supabase reads synchronously, so it
    runs in a worker thread to avoid blocking the event loop -- same
    reasoning as every other importer's _build_plan."""
    return await asyncio.to_thread(svc.build_dormancy_plan)


def _report(plan: svc.DormancyPlan, batch: str) -> dict:
    total = plan.stats.get("dormant_candidates", 0) + plan.stats.get("long_dormant_candidates", 0)
    return {
        "batch": batch,
        "counts": dict(plan.stats),
        "can_commit": total > 0,
    }


@router.post("/drivers/dormancy/preview")
@driver_dormancy_preview_limit
async def preview_driver_dormancy(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build the plan and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()
    return _report(plan, batch)


@router.post("/drivers/dormancy/commit")
@driver_dormancy_commit_limit
async def commit_driver_dormancy(
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
        conflicts = await asyncio.to_thread(svc.apply_dormancy_flags, plan, batch=batch)
    except Exception as e:
        logger.error("driver dormancy flag commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Flag commit failed; some rows may have been flagged") from e

    flagged = len(plan.candidates) - len(conflicts)

    await log_admin_action(
        admin,
        "driver_dormancy_flag",
        "drivers",
        batch,
        {
            "drivers_flagged": flagged,
            "conflicts": len(conflicts),
        },
    )
    return {
        **report,
        "committed": True,
        "drivers_flagged": flagged,
        "conflicts": len(conflicts),
    }
