"""Admin route for the driver-repair pass (owner follow-up 2026-08-31,
services/migration_driver_repair_service.py).

Two endpoints, no file upload -- like pre_launch_flag.py and
migration_data_quality.py, this tool operates entirely on already-migrated
production data (``rides`` + ``drivers``), not an uploaded CSV:

- ``POST /api/admin/legacy/driver-repair/preview`` -- read-only plan, no
  writes.
- ``POST /api/admin/legacy/driver-repair/commit`` -- re-plans fresh (same
  idempotent re-plan pattern every other importer's commit endpoint uses)
  and, if there's anything repairable, applies it via
  ``migration_driver_repair_service.apply_driver_repair``.

Unlike the metadata-only data-quality scan, this commit mutates
``rides.driver_id`` plus writes ``driver_insurance_periods`` and ``payouts``
rows -- see the service module's docstring for why both are required, not
optional cleanup. super_admin only, same posture as every other bulk write
on this page.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import migration_driver_repair_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        driver_repair_commit_limit,
        driver_repair_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import migration_driver_repair_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        driver_repair_commit_limit,
        driver_repair_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Driver-repair pass requires super_admin")


async def _build_plan() -> svc.DriverRepairPlan:
    """build_driver_repair_plan does its own Supabase reads synchronously,
    so it runs in a worker thread to avoid blocking the event loop -- same
    reasoning as every other importer's _build_plan."""
    return await asyncio.to_thread(svc.build_driver_repair_plan)


def _report(plan: svc.DriverRepairPlan, batch: str) -> dict:
    return {
        "batch": batch,
        "counts": dict(plan.stats),
        "can_commit": plan.stats.get("repairable", 0) > 0,
    }


@router.post("/legacy/driver-repair/preview")
@driver_repair_preview_limit
async def preview_driver_repair(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build the plan and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()
    return _report(plan, batch)


@router.post("/legacy/driver-repair/commit")
@driver_repair_commit_limit
async def commit_driver_repair(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-plan fresh and, if there's anything repairable, apply it."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()

    report = _report(plan, batch)
    if not report["can_commit"]:
        return {**report, "committed": False}

    try:
        conflicts, drivers_recounted = await asyncio.to_thread(svc.apply_driver_repair, plan, batch=batch)
    except Exception as e:
        logger.error("driver-repair commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(
            status_code=502, detail="Driver-repair commit failed; some rows may have been repaired"
        ) from e

    rides_repaired = len(plan.candidates) - len(conflicts)

    await log_admin_action(
        admin,
        "migration_driver_repair",
        "rides",
        batch,
        {
            "rides_repaired": rides_repaired,
            "conflicts": len(conflicts),
            "drivers_recounted": drivers_recounted,
        },
    )
    return {
        **report,
        "committed": True,
        "rides_repaired": rides_repaired,
        "conflicts": len(conflicts),
        "drivers_recounted": drivers_recounted,
    }
