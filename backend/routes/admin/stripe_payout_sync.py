"""Admin endpoints for the Stripe payout-history sync.

Downloads each mapped driver's Stripe Transfer history and materializes the
transfers that no ``payouts`` row tracks as ``payout_type='stripe_sync'``
records — the legacy-app payout history the operator chose NOT to import from
the old app's export (Stripe is trusted; the export is not). Those rows feed
T4A generation (see ``services/stripe_payout_sync_service``) and the driver's
payout-history screen; they are excluded from payable-balance math.

Mirrors the validate/commit contract of the other migration importers:

- ``POST /api/admin/stripe/payout-sync/validate`` — dry-run: list transfers,
  return counts + per-year totals + warnings/errors. No writes.
- ``POST /api/admin/stripe/payout-sync/commit`` — rebuild the plan and, only
  if there are no errors, insert the synced rows. Deterministic row ids make
  a re-sent commit converge rather than duplicate.

Super-admin only, matching the legacy booking import: it writes to
``payouts``, one of the two tables that feed driver payout math (the synced
rows are balance-inert by payout_type, but the boundary stays conservative).
The handlers re-check the role themselves so the guard survives a re-mount.

Reports carry only ``driver_id`` / ``acct_…`` / ``tr_…`` identifiers — never
names, phones, or bank details — per the PIPEDA rules in CLAUDE.md.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...services import stripe_payout_sync_service as sync_svc
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase  # type: ignore  # noqa: F401
    from dependencies import get_admin_user  # noqa: F401
    from services import stripe_payout_sync_service as sync_svc  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_super_admin(admin: dict) -> None:
    """403 unless super_admin — writing payouts history is above module grants."""
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Stripe payout sync requires super_admin")


class PayoutSyncRequest(BaseModel):
    """Sync scope. Omit everything for all mapped drivers, full history."""

    driver_ids: Optional[list[str]] = Field(None, max_length=500)
    # Restrict to one calendar year (UTC) — e.g. the T4A tax year being rebuilt.
    year: Optional[int] = Field(None, ge=2015, le=2100)
    batch: Optional[str] = None


def _year_bounds(year: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if year is None:
        return None, None
    gte = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    lte = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()) - 1
    return gte, lte


def _serialize_items(items: list[sync_svc.SyncReportItem]) -> list[dict[str, str]]:
    return [{"row_ref": i.row_ref, "field": i.field, "message": i.message} for i in items]


async def _build_plan(body: PayoutSyncRequest, batch: str) -> Any:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    gte, lte = _year_bounds(body.year)
    # A missing key surfaces as a stripe_not_configured plan error; per-driver
    # Stripe failures become plan errors. Anything that still escapes (DB
    # outage during prefetch) → clean 503 so the operator retries.
    try:
        return await sync_svc.build_plan(
            stripe_secret,
            batch,
            driver_ids=body.driver_ids,
            created_gte=gte,
            created_lte=lte,
        )
    except Exception as e:
        logger.error("stripe payout sync plan build failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Could not build the payout sync plan (database/Stripe error); retry"
        ) from e


def _report(plan: Any, batch: str) -> dict[str, Any]:
    return {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "stats": plan.stats,
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }


@router.post("/stripe/payout-sync/validate")
async def validate_payout_sync(
    body: PayoutSyncRequest,
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: list Stripe transfers and report what a commit would insert."""
    _require_super_admin(admin)
    batch = body.batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan(body, batch)
    return _report(plan, batch)


@router.post("/stripe/payout-sync/commit")
async def commit_payout_sync(
    body: PayoutSyncRequest,
    admin: dict = Depends(get_admin_user),
):
    """Rebuild the plan against live Stripe/DB state and, only if clean,
    insert the synced payout rows."""
    _require_super_admin(admin)
    batch = body.batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    plan = await _build_plan(body, batch)

    if plan.errors:
        # Data drifted since validate (or validate was skipped). Refuse but
        # return 200 with committed=false + the full report — same contract as
        # the other importers.
        return {**_report(plan, batch), "committed": False}

    try:
        result = await asyncio.to_thread(sync_svc.commit_plan, plan)
    except Exception as e:
        logger.error("stripe payout sync commit failed", extra={"batch": batch}, exc_info=True)
        # Inserts are not transactional: some chunks may already be written.
        # Deterministic row ids make a re-sent commit converge (existing rows
        # are skipped), so the operator just re-sends.
        raise HTTPException(
            status_code=502,
            detail="Sync commit failed partway; re-send — already-written rows are skipped",
        ) from e

    # Audit trail carries counts + batch only — never PII.
    await log_admin_action(
        admin,
        "stripe_payout_sync",
        "payouts",
        batch,
        {
            "inserted": result["inserted"],
            "skipped_existing": result["skipped_existing"],
            "drivers_scanned": plan.stats.get("drivers_scanned"),
            "sum_planned": plan.stats.get("sum_planned"),
            "year": body.year,
        },
    )
    return {**_report(plan, batch), "committed": True, **result}


@router.get("/auto-payouts/batches")
async def list_auto_payout_batches(
    limit: int = 20,
    admin: dict = Depends(get_admin_user),
):
    """Weekly auto-payout batch ledger (auto_payout_batches), newest first.

    Ops visibility for the Sunday batch: status (running/completed/partial/
    failed), per-week counts, total amount, and the aggregated error summary.
    Read-only; any admin role may view (no money moves here). Uses the
    idx_auto_payout_batches_created index from migration 314.
    """
    limit = max(1, min(int(limit or 20), 100))
    try:
        rows = await db_supabase.get_rows("auto_payout_batches", {}, limit=limit, order="created_at", desc=True)
    except Exception as e:
        logger.error(f"Failed to list auto-payout batches: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Auto-payout batches temporarily unavailable") from e
    return {"batches": rows, "count": len(rows)}
