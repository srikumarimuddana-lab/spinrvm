"""Admin route for the legacy ride duration_estimated marker backfill
(residual sub-item of ACTION_ITEMS.md A41 -- the one migration backfill of
the original four that was still CLI-only; see
docs/runbooks/migration-tool-order.md's "Not part of the ordered chain"
section and docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md
for the tool's own history).

Two endpoints, no file upload -- like migration_data_quality.py and
driver_dormancy.py, this operates entirely on already-migrated production
data (the `rides` table), not an uploaded CSV:

- ``POST /api/admin/legacy/duration-estimated-backfill/preview`` --
  read-only plan, no writes.
- ``POST /api/admin/legacy/duration-estimated-backfill/commit`` --
  re-plans fresh (same idempotent re-plan pattern every other importer's
  commit endpoint uses) and, if there's anything to stamp, applies it via
  ``booking_import_service.apply_duration_estimated_backfill``.

This is a thin HTTP wrapper only -- all matching/validation/write logic is
``services/booking_import_service.py``'s ``plan_duration_estimated_backfill``
/ ``apply_duration_estimated_backfill``, the exact pair
``scripts/backfill_legacy_ride_duration_estimated.py`` already calls from
the CLI. Nothing here re-implements that logic.

Additive only: stamps ``legacy_import_metadata.duration_estimated`` (plus
this backfill's own ``legacy_duration_estimated_backfill`` audit key) on
matched rows. Never deletes, never touches ``rides.status``, and never
touches ``duration_minutes`` itself -- see the service module's docstring
section for the full write-time guard (never-clobber + whole-column
optimistic-concurrency guard against a concurrent writer of the same JSONB
column).

super_admin only, matching migration_data_quality.py/driver_dormancy.py's
posture -- this is a bulk write across the core `rides` table, not scoped
to one admin module.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request

try:
    from ...dependencies import get_admin_user
    from ...services import booking_import_service as svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        duration_estimated_backfill_commit_limit,
        duration_estimated_backfill_preview_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import booking_import_service as svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        duration_estimated_backfill_commit_limit,
        duration_estimated_backfill_preview_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Duration-estimated marker backfill requires super_admin")


async def _build_plan() -> svc.DurationEstimatedBackfillPlan:
    """plan_duration_estimated_backfill does its own Supabase reads
    synchronously, so it runs in a worker thread to avoid blocking the event
    loop -- same reasoning as every other importer's _build_plan."""
    return await asyncio.to_thread(svc.plan_duration_estimated_backfill)


def _report(plan: svc.DurationEstimatedBackfillPlan, batch: str) -> dict:
    n_estimated = sum(1 for u in plan.updates if u.duration_estimated)
    n_measured = len(plan.updates) - n_estimated
    return {
        "batch": batch,
        "counts": {
            "legacy_rides_scanned": plan.total_legacy_rides_scanned,
            "already_marked_skipped": plan.skipped_already_marked,
            "rows_to_stamp": len(plan.updates),
            "duration_estimated_true": n_estimated,
            "duration_estimated_false": n_measured,
        },
        # Defensive-only in practice (a row missing its own id) -- surfaced
        # so the UI can show it rather than a silent can_commit=false. Never
        # ride PII: see plan_duration_estimated_backfill's own error text.
        "errors": list(plan.errors),
        "can_commit": not plan.errors and len(plan.updates) > 0,
    }


@router.post("/legacy/duration-estimated-backfill/preview")
@duration_estimated_backfill_preview_limit
async def preview_duration_estimated_backfill(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: build the plan and return counts. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()
    return _report(plan, batch)


@router.post("/legacy/duration-estimated-backfill/commit")
@duration_estimated_backfill_commit_limit
async def commit_duration_estimated_backfill(
    request: Request,
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-plan fresh and, if there's anything to stamp, apply it."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan()

    report = _report(plan, batch)
    if not report["can_commit"]:
        return {**report, "committed": False}

    try:
        conflicts = await asyncio.to_thread(svc.apply_duration_estimated_backfill, plan, batch=batch)
    except Exception as e:
        logger.error("duration-estimated backfill commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Backfill commit failed; some rows may have been stamped") from e

    updated = len(plan.updates) - len(conflicts)

    # Audit trail carries counts + batch only -- ride ids are internal UUIDs,
    # never PII, but a count is all an operator needs here (mirrors
    # migration_data_quality_flag's audit shape, not the SIN/DOB backfill's
    # id-list one) -- a re-run automatically picks up any conflicted row.
    await log_admin_action(
        admin,
        "legacy_duration_estimated_backfill",
        "rides",
        batch,
        {"updated": updated, "conflicts": len(conflicts)},
    )
    return {
        **report,
        "committed": True,
        "updated": updated,
        "conflicts": len(conflicts),
    }
