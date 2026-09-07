"""Admin route for the legacy ID crosswalk backfill (migration 328,
``legacy_id_crosswalk``).

Two endpoints, no file upload -- like pre_launch_flag.py/migration_data_
quality.py, this tool operates entirely on already-migrated production data
(``drivers``/``rides``), not an uploaded CSV:

- ``POST /api/admin/legacy/id-crosswalk-backfill/preview`` -- read-only plan
  (drivers + riders), no writes.
- ``POST /api/admin/legacy/id-crosswalk-backfill/commit`` -- re-plans fresh
  (same idempotent re-plan pattern every other importer's commit endpoint
  uses) and, if there's anything new to record, writes it via
  ``legacy_id_crosswalk_service.apply_crosswalk_backfill``.

Additive only: inserts new legacy_id_crosswalk rows, never updates or
deletes an existing one. See ``services/legacy_id_crosswalk_service.py``'s
module docstring for what this pass can and can't resolve, and why it's
still worth shipping ahead of the raw Mongo export.

super_admin only, matching pre_launch_flag.py's posture -- this is a bulk
write across driver/rider identity data, not scoped to one admin module.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import legacy_id_crosswalk_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        legacy_id_crosswalk_backfill_commit_limit,
        legacy_id_crosswalk_backfill_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import legacy_id_crosswalk_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        legacy_id_crosswalk_backfill_commit_limit,
        legacy_id_crosswalk_backfill_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Legacy ID crosswalk backfill requires super_admin")


async def _build_plans() -> tuple[svc.CrosswalkPlan, svc.CrosswalkPlan]:
    """Both plan builders do their own Supabase reads synchronously, so they
    run in a worker thread to avoid blocking the event loop -- same
    reasoning as every other importer's _build_plan."""
    driver_plan = await asyncio.to_thread(svc.build_driver_crosswalk_plan)
    rider_plan = await asyncio.to_thread(svc.build_rider_crosswalk_plan)
    return driver_plan, rider_plan


def _report(driver_plan: svc.CrosswalkPlan, rider_plan: svc.CrosswalkPlan, batch: str) -> dict:
    new_rows = driver_plan.stats.get("new_rows_to_write", 0) + rider_plan.stats.get("new_rows_to_write", 0)
    return {
        "batch": batch,
        "counts": {"driver": dict(driver_plan.stats), "rider": dict(rider_plan.stats)},
        "ambiguous_riders": len(rider_plan.ambiguous),
        "can_commit": new_rows > 0,
    }


@router.post("/legacy/id-crosswalk-backfill/preview")
@legacy_id_crosswalk_backfill_preview_limit
async def preview_id_crosswalk_backfill(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build both plans and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    driver_plan, rider_plan = await _build_plans()
    return _report(driver_plan, rider_plan, batch)


@router.post("/legacy/id-crosswalk-backfill/commit")
@legacy_id_crosswalk_backfill_commit_limit
async def commit_id_crosswalk_backfill(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-plan fresh and, if there's anything new to record, write it."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    driver_plan, rider_plan = await _build_plans()

    report = _report(driver_plan, rider_plan, batch)
    if not report["can_commit"]:
        return {**report, "committed": False}

    candidates = driver_plan.candidates + rider_plan.candidates
    try:
        written, failed = await asyncio.to_thread(svc.apply_crosswalk_backfill, candidates, batch=batch)
    except Exception as e:
        logger.error("legacy id crosswalk backfill commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Crosswalk commit failed; some rows may have been written") from e

    await log_admin_action(
        admin,
        "legacy_id_crosswalk_backfill",
        "legacy_id_crosswalk",
        batch,
        {
            "written": written,
            "failed": len(failed),
            "driver_counts": dict(driver_plan.stats),
            "rider_counts": dict(rider_plan.stats),
        },
    )
    return {**report, "committed": True, "written": written, "failed": len(failed)}
