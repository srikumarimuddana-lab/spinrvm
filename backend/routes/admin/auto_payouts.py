"""Admin views for the weekly auto-payout batch.

Three surfaces:

- ``GET /api/admin/auto-payouts/batches`` — the weekly batch ledger:
  what each Sunday run paid, skipped, and failed.
- ``GET /api/admin/auto-payouts/blocked-drivers`` — live preflight: who
  the batch cannot pay right now and why, so ops can chase blockers
  BEFORE the run instead of reading last week's summary.
- ``POST /api/admin/auto-payouts/run-now`` — manually trigger this
  week's batch outside the Sunday 06:00 America/Regina window the
  hourly loop normally gates on. THE ONLY WRITE SURFACE in this router.

The two GET views are gated by the ``earnings`` module (mounted in
``routes/admin/__init__``), matching the other driver-payout admin
surfaces — monitoring views finance/ops staff need weekly, and neither
one writes.

``run-now`` is additionally gated ``require_super_admin`` (money-moving,
same posture as this repo's other Stripe-ledger-sensitive admin routes —
stripe_payout_sync, stripe_connect_ledger, dispute evidence submission)
and every call is audit-logged. It exists because the batch itself
(``utils/auto_payout.py::run_weekly_auto_payout``) is otherwise reachable
ONLY on Sundays: if ``auto_payout_enabled`` was off during that window
for any reason, the earliest recovery without this endpoint is next
Sunday. ``run_weekly_auto_payout`` already carries every safety property
this needs — idempotent on ``week_key``, resumable, leader-locked at the
scheduled-loop layer, and the ``auto_payout_batches`` unique-week-key
insert + staleness-gated resume claim make a second concurrent call
converge rather than double-run — so this endpoint adds nothing but "let
a human invoke the function on demand" instead of a manual
``app_settings`` flip-and-wait or hand-written SQL, either of which
would bypass those guarantees.

Reports carry ``driver_id`` only — never names, phones, or bank details,
per the PIPEDA rules in CLAUDE.md.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user, require_super_admin
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase  # type: ignore
    from dependencies import get_admin_user, require_super_admin  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

# Postgres "relation does not exist" — i.e. migration 314 has not been applied
# to this environment yet. Worth naming explicitly: the generic "temporarily
# unavailable" message reads as a transient blip an operator should wait out,
# when in fact nothing will work until someone runs the migration.
_UNDEFINED_TABLE = "42P01"


def _db_unavailable(what: str, table: str, exc: Exception) -> HTTPException:
    """503 that says WHICH failure this is, per CLAUDE.md's rule that DB
    errors surface loudly enough to fix rather than being masked."""
    detail = f"{what} temporarily unavailable"
    text = f"{exc} {getattr(exc, 'details', '')}"
    if _UNDEFINED_TABLE in text or f'relation "{table}" does not exist' in text:
        detail = (
            f"{what} unavailable: the '{table}' table does not exist in this environment. "
            "Apply migration 314 (python -m backend.scripts.run_migrations), then reload."
        )
    return HTTPException(status_code=503, detail=detail)


@router.get("/auto-payouts/batches")
async def list_auto_payout_batches(
    limit: int = 20,
    week_key: str | None = None,
    admin: dict = Depends(get_admin_user),
):
    """Weekly auto-payout batch ledger (``auto_payout_batches``), newest first.

    Each row carries the run's status (running / completed / partial /
    failed), driver counts, total transferred, the aggregated error
    summary, ``skipped_summary`` (counts by reason plus the driver ids
    that had money waiting), and ``area_summary`` (per-service-area
    slice). Uses the ``idx_auto_payout_batches_created`` index from
    migration 314.

    ``week_key`` ("2026-W33") returns just that week — a direct lookup
    for a run older than the page's default window, hitting the unique
    ``week_key`` index instead of paging back through history.
    """
    limit = max(1, min(int(limit or 20), 100))
    filters: dict = {"week_key": week_key} if week_key else {}
    try:
        rows = await db_supabase.get_rows("auto_payout_batches", filters, limit=limit, order="created_at", desc=True)
    except Exception as e:
        logger.error(f"Failed to list auto-payout batches: {e}", exc_info=True)
        raise _db_unavailable("Auto-payout batches", "auto_payout_batches", e) from e
    # An empty table is a valid state, not an error: no batch has run yet.
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
        raise _db_unavailable("Blocked-driver preflight", "drivers", e) from e

    by_reason: dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    return {"blocked": rows, "count": len(rows), "by_reason": by_reason}


@router.post("/auto-payouts/run-now")
async def run_auto_payout_now(
    background_tasks: BackgroundTasks,
    admin: dict = Depends(require_super_admin),
):
    """Manually trigger this week's auto-payout batch right now.

    super_admin only (money-moving) — see module docstring for why this
    endpoint exists and why it's safe to expose: ``run_weekly_auto_payout``
    is idempotent on ``week_key`` and resumable, so a call here can never
    double-run or double-pay a week the scheduled Sunday loop already
    handled, or that another admin's ``run-now`` call is already mid-flight.

    Runs in the background rather than inline (per CLAUDE.md's rule against
    awaiting a long third-party call chain in a request handler — this walks
    the full driver base and can make many Stripe Transfer calls). Poll
    ``GET /auto-payouts/batches?week_key=...`` for the outcome.
    """
    try:
        from ...utils.auto_payout import current_week_key, run_weekly_auto_payout
    except ImportError:
        from utils.auto_payout import current_week_key, run_weekly_auto_payout  # type: ignore

    week_key = current_week_key()
    batch_id = f"auto-batch-{week_key}"

    # Audit BEFORE enqueueing: a security-relevant admin action per
    # CLAUDE.md's observability conventions, recorded even if the run
    # itself later no-ops (e.g. already completed, or disabled via flag).
    await log_admin_action(admin, "auto_payout_run_now_triggered", "auto_payout_batch", batch_id, {})

    async def _run() -> None:
        try:
            result = await run_weekly_auto_payout()
            logger.info("[AUTO-PAYOUT] admin-triggered run for %s: %s", week_key, result)
        except Exception:
            logger.exception("[AUTO-PAYOUT] admin-triggered run failed for %s", week_key)

    background_tasks.add_task(_run)
    return {
        "status": "triggered",
        "week_key": week_key,
        "batch_id": batch_id,
        "detail": "Running in the background. Poll GET /auto-payouts/batches?week_key= for status.",
    }
