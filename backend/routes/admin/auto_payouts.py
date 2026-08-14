"""Admin read-only views for the weekly auto-payout batch.

Two surfaces, both read-only (no money moves through this router):

- ``GET /api/admin/auto-payouts/batches`` — the weekly batch ledger:
  what each Sunday run paid, skipped, and failed.
- ``GET /api/admin/auto-payouts/blocked-drivers`` — live preflight: who
  the batch cannot pay right now and why, so ops can chase blockers
  BEFORE the run instead of reading last week's summary.

Gated by the ``earnings`` module (mounted in ``routes/admin/__init__``),
matching the other driver-payout admin surfaces. Deliberately NOT
super_admin: these are monitoring views finance/ops staff need weekly,
and neither one writes. The batch itself is driven by
``utils/auto_payout.py``; nothing here can trigger or alter a payout.

Reports carry ``driver_id`` only — never names, phones, or bank details,
per the PIPEDA rules in CLAUDE.md.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/auto-payouts/batches")
async def list_auto_payout_batches(
    limit: int = 20,
    admin: dict = Depends(get_admin_user),
):
    """Weekly auto-payout batch ledger (``auto_payout_batches``), newest first.

    Each row carries the run's status (running / completed / partial /
    failed), driver counts, total transferred, the aggregated error
    summary, and ``skipped_summary`` — counts by reason plus the driver
    ids that had money waiting. Uses the ``idx_auto_payout_batches_created``
    index from migration 314.
    """
    limit = max(1, min(int(limit or 20), 100))
    try:
        rows = await db_supabase.get_rows("auto_payout_batches", {}, limit=limit, order="created_at", desc=True)
    except Exception as e:
        logger.error(f"Failed to list auto-payout batches: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Auto-payout batches temporarily unavailable") from e
    return {"batches": rows, "count": len(rows)}


@router.get("/auto-payouts/blocked-drivers")
async def list_blocked_drivers(
    limit: int = 50,
    service_area_id: str | None = None,
    admin: dict = Depends(get_admin_user),
):
    """Drivers with money waiting that the weekly batch cannot pay right now.

    Live preflight, not a replay of last week: it evaluates the same
    eligibility gates the Sunday run uses (Stripe account present and
    payouts enabled, not suspended, CRA GST BN + SIN on file), so blockers
    can be chased before the batch. Each row carries the reason and the
    amount being held.

    ``service_area_id`` scopes the list to one market. That filters the
    VIEW only — the batch always runs fleet-wide; segregation here is a
    reporting concern, not a payment one.

    Diagnostic endpoint — it computes a payable balance per blocked
    driver, so keep ``limit`` modest.
    """
    limit = max(1, min(int(limit or 50), 200))
    try:
        from ...utils.auto_payout import find_blocked_drivers
    except ImportError:
        from utils.auto_payout import find_blocked_drivers  # type: ignore

    try:
        rows = await find_blocked_drivers(limit, service_area_id=service_area_id)
    except Exception as e:
        logger.error(f"Failed to list blocked drivers: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Blocked-driver preflight temporarily unavailable") from e

    by_reason: dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    return {"blocked": rows, "count": len(rows), "by_reason": by_reason}
