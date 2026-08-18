"""Dispute evidence-deadline reminder — C23 Action item 2 (ACTION_ITEMS.md).

Every 6 hours, checks `stripe_disputes` for open chargebacks whose
`evidence_due_by` (populated by `charge.dispute.created`, C23 Action item 1)
falls within the next 3 days, and haven't already been reminded or had
evidence submitted. For each one, fires a Sentry-tagged alert + an ERROR log
so the deadline can't be missed silently — miss it and the dispute is lost
automatically with no evidence considered.

Fires once per dispute (claim-flag `evidence_reminder_sent_at`), not a
recurring countdown — this is a single "your evidence is due soon" alert,
not a running clock. `docs/runbooks/payment-dispute-evidence.md` documents
the manual response path this alert points at.

Replay-safe on every replica: the claim is an atomic `UPDATE ... WHERE
evidence_reminder_sent_at IS NULL` (same idiom as
`routes/drivers/subscriptions.py`'s `expiry_warned_3d` claim) — only the
replica whose UPDATE actually matched a row fires the alert for that
dispute; every other replica's UPDATE for the same row matches zero rows
and is silently skipped. No Redis lock needed for correctness (only for
noise reduction, and even without one, the atomic claim means at most one
alert per dispute regardless of replica count).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

try:
    from ..db_supabase import get_rows, update_one  # type: ignore
except ImportError:
    from db_supabase import get_rows, update_one  # type: ignore

logger = logging.getLogger(__name__)

_TICK_INTERVAL_SECONDS = 6 * 3600  # matches subscription_expiry's cadence
_REMINDER_WINDOW = timedelta(days=3)

# Disputes in any of these statuses are already resolved — no evidence
# deadline is actionable once a dispute is won/lost/withdrawn.
_CLOSED_STATUSES = frozenset({"won", "lost", "warning_closed"})


def _escalate(message: str, context: Dict[str, Any]) -> None:
    """Tagged Sentry event so an on-call alert rule can page on an
    approaching, still-unaddressed dispute deadline.

    No-op when SENTRY_DSN is unset. PIPEDA: context carries only IDs,
    amounts, and a day-count — never rider/driver names, phone numbers, or
    coordinates. Mirrors services/ledger_service.py's `escalate()`.
    """
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            message,
            level="warning",
            tags={
                "spinr_alert": "dispute_evidence_due_soon",
                "domain": "payments",
                "surface": "backend",
            },
            contexts={"dispute": context},
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break the loop
        logger.debug("[DISPUTE-EVIDENCE] Sentry escalation unavailable: %s", sentry_err)


async def _tick() -> int:
    """One check pass. Returns the number of disputes newly alerted on."""
    now = datetime.now(timezone.utc)
    window_end = now + _REMINDER_WINDOW

    candidates = (
        await get_rows(
            "stripe_disputes",
            {
                "$and": [
                    {"evidence_due_by": {"$gt": now.isoformat()}},
                    {"evidence_due_by": {"$lte": window_end.isoformat()}},
                ],
                "evidence_reminder_sent_at": None,
                "evidence_submitted_at": None,
                "status": {"$nin": list(_CLOSED_STATUSES)},
            },
            limit=200,
        )
        or []
    )

    alerted = 0
    for dispute in candidates:
        due_by_raw = dispute.get("evidence_due_by")
        if not due_by_raw:
            continue
        try:
            due_by = datetime.fromisoformat(str(due_by_raw).replace("Z", "+00:00"))
            if due_by.tzinfo is None:
                due_by = due_by.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            logger.warning(
                "[DISPUTE-EVIDENCE] dispute %s has an unparseable evidence_due_by=%r, skipping",
                dispute.get("id"),
                due_by_raw,
            )
            continue

        # Re-check the window and re-derive days_left from a value read at
        # claim time, not query time — the candidate list can be stale by a
        # few seconds under normal tick latency.
        if not (now < due_by <= window_end):
            continue

        # Atomic claim: only the replica whose UPDATE matches a row (still
        # NULL evidence_reminder_sent_at) proceeds to alert. Every other
        # replica racing the same tick gets zero rows back and skips.
        claimed = await update_one(
            "stripe_disputes",
            {"id": dispute["id"], "evidence_reminder_sent_at": None},
            {"$set": {"evidence_reminder_sent_at": now.isoformat()}},
        )
        if claimed is None:
            continue

        days_left = max(1, int((due_by - now).total_seconds() / 86400))
        stripe_dispute_id = dispute.get("stripe_dispute_id", "")
        ride_id = dispute.get("ride_id")

        logger.error(
            "CHARGEBACK EVIDENCE DUE SOON: dispute=%s ride=%s days_left=%d due_by=%s",
            stripe_dispute_id,
            ride_id or "",
            days_left,
            due_by.isoformat(),
            extra={
                "domain": "payments",
                "ride_id": ride_id or "",
            },
        )
        _escalate(
            f"Chargeback evidence due in {days_left} day(s) — dispute {stripe_dispute_id}",
            {
                "stripe_dispute_id": stripe_dispute_id,
                "ride_id": ride_id,
                "days_left": days_left,
                "evidence_due_by": due_by.isoformat(),
                "amount_cents": dispute.get("amount_cents"),
                "reason": dispute.get("reason"),
            },
        )
        alerted += 1

    return alerted


async def dispute_evidence_reminder_loop() -> None:
    """Background task: T-3-days chargeback evidence-deadline alert (C23).

    Runs every 6 hours from the FastAPI lifespan. Never raises — a failed
    tick logs and waits for the next one rather than crashing the loop
    (matches every other reminder-style loop's error-handling posture,
    e.g. subscription_expiry / driver_onboarding_reminders).
    """
    while True:
        try:
            alerted = await _tick()
            if alerted:
                logger.info("[DISPUTE-EVIDENCE] alerted on %d dispute(s) this tick", alerted)
        except Exception:
            logger.error("[DISPUTE-EVIDENCE] tick failed", exc_info=True)

        # Heartbeat recorded even after a caught tick exception (matches
        # subscription_expiry's placement) -- registered in
        # core/lifespan.py's _WATCHDOG_LOOP_NAMES so a silently-stalled loop
        # (not just a crashed one, which _restartable already covers) pages.
        try:
            from utils.loop_monitor import record_heartbeat as _lm_hb

            _lm_hb("dispute_evidence_reminder (6h)")
        except ImportError:
            pass

        await asyncio.sleep(_TICK_INTERVAL_SECONDS)
