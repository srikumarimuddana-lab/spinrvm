"""Admin route for the pre-launch legacy data flagging tool (owner-confirmed
2026-08-30 launch date, 2026-03-30).

Two endpoints, no file upload -- unlike every other importer in this
migration effort, this tool operates entirely on already-migrated
production data (drivers/rides tables), not an uploaded CSV:

- ``POST /api/admin/legacy/pre-launch-flag/preview`` -- read-only plan, no
  writes.
- ``POST /api/admin/legacy/pre-launch-flag/commit`` -- re-plans fresh (same
  idempotent re-plan pattern every other importer's commit endpoint uses)
  and, if there's anything to flag, applies it via
  ``pre_launch_flag_service.apply_pre_launch_flags``.

Additive only: sets ``legacy_import_metadata.pre_launch_test = true`` on
matched rows. Never deletes or deactivates anything. See
``services/pre_launch_flag_service.py``'s module docstring for the exact
matching criteria and why they're narrower than a blanket
"created before launch" rule.

super_admin only, matching the wallet importer's posture -- this is a bulk
write across core `drivers`/`rides` tables, not scoped to one admin module.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import pre_launch_flag_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        pre_launch_flag_commit_limit,
        pre_launch_flag_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import pre_launch_flag_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        pre_launch_flag_commit_limit,
        pre_launch_flag_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Pre-launch legacy data flagging requires super_admin")


async def _build_plan() -> svc.PreLaunchFlagPlan:
    """build_pre_launch_flag_plan does its own Supabase reads synchronously,
    so it runs in a worker thread to avoid blocking the event loop -- same
    reasoning as every other importer's _build_plan."""
    return await asyncio.to_thread(svc.build_pre_launch_flag_plan)


def _report(plan: svc.PreLaunchFlagPlan, batch: str) -> dict:
    return {
        "batch": batch,
        "counts": dict(plan.stats),
        "can_commit": (plan.stats.get("driver_candidates", 0) + plan.stats.get("ride_candidates", 0)) > 0,
    }


@router.post("/legacy/pre-launch-flag/preview")
@pre_launch_flag_preview_limit
async def preview_pre_launch_flag(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build the plan and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()
    return _report(plan, batch)


@router.post("/legacy/pre-launch-flag/commit")
@pre_launch_flag_commit_limit
async def commit_pre_launch_flag(
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
        conflicts = await asyncio.to_thread(svc.apply_pre_launch_flags, plan, batch=batch)
    except Exception as e:
        logger.error("pre-launch flag commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Flag commit failed; some rows may have been flagged") from e

    flagged_drivers = len(plan.driver_candidates) - len(conflicts["drivers"])
    flagged_rides = len(plan.ride_candidates) - len(conflicts["rides"])

    await log_admin_action(
        admin,
        "legacy_pre_launch_flag",
        "drivers,rides",
        batch,
        {
            "drivers_flagged": flagged_drivers,
            "rides_flagged": flagged_rides,
            "driver_conflicts": len(conflicts["drivers"]),
            "ride_conflicts": len(conflicts["rides"]),
        },
    )
    return {
        **report,
        "committed": True,
        "drivers_flagged": flagged_drivers,
        "rides_flagged": flagged_rides,
        "driver_conflicts": len(conflicts["drivers"]),
        "ride_conflicts": len(conflicts["rides"]),
    }
