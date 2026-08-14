"""Spinr-controlled weekly auto-payout — replaces driver-initiated cashout.

Every Sunday (checked hourly by the background loop in lifespan.py), this
service scans all drivers with a Stripe Connect account and a payable
balance >= $10, creates a ``stripe.Transfer`` for each, and records the
payout row in ``payouts`` with ``payout_type='auto'``.

Replay-safety contract (mandatory per CLAUDE.md background-task rules):
  - Redis leader lock (``spinr:auto_payout:lock``) prevents concurrent runs
    across replicas. Fail-open: if Redis is unavailable, the week_key
    unique index on ``auto_payout_batches`` is the hard guard.
  - ``auto_payout_batches.week_key`` unique index: only one batch row per
    ISO week. A re-run on the same Sunday is a no-op at the DB level.
  - Per-driver Stripe idempotency key: ``auto-payout-{driver_id}-{week_key}``
    ensures a retry never double-transfers.
  - Per-driver payout row id: ``auto-{driver_id}-{week_key}`` — deterministic,
    so a re-run that gets past the batch guard converges on the same row.

Money-safety:
  - payable_balance is recomputed per driver using the SAME formula as
    ``routes/drivers/earnings.get_driver_balance`` — ride income + tax +
    incentives + cancel fees + bonuses - payouts (excluding reversed/failed
    and stripe_sync/legacy_import). See that endpoint's comments for the
    rationale behind each term.
  - All arithmetic is ``Decimal`` — never float.
  - The payout row is inserted with status='reserved' BEFORE the Stripe
    Transfer (reserve-then-transfer pattern, same as manual payouts) so a
    crash after transfer still leaves a DB record.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

try:
    from .. import db_supabase
    from ..utils.money import dollars_to_cents
    from ..utils.redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual-import pattern
    import db_supabase  # type: ignore
    from utils.money import dollars_to_cents  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

MIN_PAYOUT_AMOUNT = Decimal("10.00")
LOCK_KEY = "spinr:auto_payout:lock"
LOCK_TTL_SECONDS = 3600  # 1 hour — covers the full batch run
_TWO_PLACES = Decimal("0.01")

_PAGE_SIZE = 500


def _d(v) -> Decimal:
    from decimal import InvalidOperation

    try:
        return Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def _ride_income(r: dict) -> Decimal:
    if r.get("driver_earnings") is not None:
        return _d(r.get("driver_earnings"))
    return _d(r.get("base_fare")) + _d(r.get("distance_fare")) + _d(r.get("time_fare")) + _d(r.get("tip_amount"))


def _ride_tax(r: dict) -> Decimal:
    tax = _d(r.get("tax_amount"))
    if tax != Decimal("0"):
        return tax
    snap = r.get("fare_breakdown_snapshot") or {}
    for line in snap.get("lines") or []:
        if line.get("type") in ("tax", "gst", "pst"):
            tax += _d(line.get("amount"))
    return tax


def current_week_key() -> str:
    """ISO year-week string for today, e.g. '2026-W33'."""
    now = datetime.now(timezone.utc)
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


def _payout_id_for(driver_id: str, week_key: str) -> str:
    return f"auto-{driver_id}-{week_key}"


async def _compute_payable_balance(driver_id: str) -> Decimal:
    """Recompute a driver's payable balance using the same formula as the
    balance endpoint. Kept minimal — no HTTP/auth deps."""
    ZERO = Decimal("0")

    rides = await db_supabase.get_rows(
        "rides",
        {
            "driver_id": driver_id,
            "status": "completed",
            "legacy_import_metadata": {"$eq": {}},
        },
        limit=10000,
    )
    ride_earnings = sum((_ride_income(r) for r in rides), ZERO)
    total_tax = sum((_ride_tax(r) for r in rides), ZERO)

    total_incentives = ZERO
    ride_ids = [r["id"] for r in rides if r.get("id")]
    if ride_ids:
        claims = (
            db_supabase.supabase.table("ride_incentive_claims")
            .select("bonus_amount")
            .in_("ride_id", ride_ids)
            .execute()
        ).data or []
        total_incentives = sum((_d(c.get("bonus_amount") or 0) for c in claims), ZERO)

    cancelled_rides = await db_supabase.get_rows("rides", {"driver_id": driver_id, "status": "cancelled"}, limit=10000)
    total_cancel_fees = sum((_d(r.get("cancellation_fee_driver") or 0) for r in cancelled_rides), ZERO)

    total_earnings = ride_earnings + total_tax + total_incentives + total_cancel_fees

    bonus_rows = await db_supabase.get_rows("driver_bonuses", {"driver_id": driver_id}, limit=10000)
    total_bonuses = sum((_d(b.get("amount") or 0) for b in bonus_rows), ZERO)

    payout_rows = await db_supabase.get_rows("payouts", {"driver_id": driver_id}, limit=5000)
    _not_money_out = {"reversed", "failed"}
    total_payouts = sum(
        (
            _d(p.get("amount") or 0)
            for p in payout_rows
            if str(p.get("status") or "").lower() not in _not_money_out
            and p.get("payout_type") not in ("stripe_sync", "legacy_import")
        ),
        ZERO,
    )

    return total_earnings + total_bonuses - total_payouts


async def _fetch_eligible_drivers() -> list[dict]:
    """All drivers with a Stripe Connect account (required for Transfer)."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = await db_supabase.get_rows(
            "drivers",
            {},
            limit=_PAGE_SIZE,
            offset=offset,
            order="id",
        )
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return [d for d in rows if d.get("stripe_account_id")]


async def run_weekly_auto_payout() -> dict:
    """Execute the weekly auto-payout batch. Returns a summary dict.

    Called by the background loop on Sundays. The week_key unique index
    on auto_payout_batches is the hard idempotency guard — if this week
    already has a completed batch, we skip immediately.
    """
    import stripe as stripe_lib

    week_key = current_week_key()
    batch_id = f"auto-batch-{week_key}"
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = await db_supabase.get_rows("auto_payout_batches", {"week_key": week_key}, limit=1)
    if existing and existing[0].get("status") == "completed":
        logger.info("[AUTO-PAYOUT] week %s already completed, skipping", week_key)
        return {"status": "already_completed", "week_key": week_key}

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()
    # Ops kill switch (app_settings pattern — flag-without-redeploy rollback).
    # Missing/None means enabled; only an explicit false turns the batch off.
    _flag = settings.get("auto_payout_enabled")
    if _flag is False or str(_flag).strip().lower() == "false":
        logger.warning("[AUTO-PAYOUT] disabled via app_settings.auto_payout_enabled, skipping week %s", week_key)
        return {"status": "disabled", "week_key": week_key}
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.error("[AUTO-PAYOUT] stripe_secret_key not configured, skipping")
        return {"status": "stripe_not_configured"}

    try:
        await db_supabase.insert_one(
            "auto_payout_batches",
            {
                "id": batch_id,
                "week_key": week_key,
                "status": "running",
                "started_at": now_iso,
                "created_at": now_iso,
            },
        )
    except Exception as e:
        if "duplicate" in str(e).lower() or "23505" in str(e):
            logger.info("[AUTO-PAYOUT] batch %s already exists (concurrent replica), skipping", batch_id)
            return {"status": "already_running", "week_key": week_key}
        raise

    drivers = await _fetch_eligible_drivers()
    drivers_eligible = 0
    drivers_paid = 0
    drivers_failed = 0
    total_amount = Decimal("0")
    errors: list[str] = []

    for driver in drivers:
        driver_id = driver["id"]
        stripe_account_id = driver.get("stripe_account_id")
        if not stripe_account_id:
            continue

        try:
            balance = await _compute_payable_balance(driver_id)
        except Exception:
            logger.exception("[AUTO-PAYOUT] balance computation failed for driver %s", driver_id)
            drivers_failed += 1
            errors.append(f"{driver_id}: balance_error")
            continue

        if balance < MIN_PAYOUT_AMOUNT:
            continue

        drivers_eligible += 1
        payout_id = _payout_id_for(driver_id, week_key)

        try:
            await db_supabase.insert_one(
                "payouts",
                {
                    "id": payout_id,
                    "driver_id": driver_id,
                    "amount": float(balance),
                    "status": "reserved",
                    "payout_type": "auto",
                    "bank_name": "Auto Payout",
                    "created_at": now_iso,
                },
            )
        except Exception as e:
            if "duplicate" in str(e).lower() or "23505" in str(e):
                logger.info("[AUTO-PAYOUT] payout %s already exists, skipping driver %s", payout_id, driver_id)
                continue
            logger.exception("[AUTO-PAYOUT] reserve failed for driver %s", driver_id)
            drivers_failed += 1
            errors.append(f"{driver_id}: reserve_error")
            continue

        try:
            transfer = await asyncio.to_thread(
                lambda _acct=stripe_account_id, _amt=balance, _pid=payout_id: stripe_lib.Transfer.create(
                    amount=dollars_to_cents(_amt),
                    currency="cad",
                    destination=_acct,
                    api_key=stripe_secret,
                    idempotency_key=f"auto-payout-{driver_id}-{week_key}",
                )
            )
            await db_supabase.update_one(
                "payouts",
                {"id": payout_id},
                {
                    "status": "completed",
                    "stripe_transfer_id": transfer.id,
                    "stripe_payout_id": transfer.id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            drivers_paid += 1
            total_amount += balance
        except Exception:
            logger.exception("[AUTO-PAYOUT] Stripe transfer failed for driver %s", driver_id)
            try:
                await db_supabase.update_one(
                    "payouts",
                    {"id": payout_id},
                    {
                        "status": "failed",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                logger.exception("[AUTO-PAYOUT] failed to mark payout as failed for %s", driver_id)
            drivers_failed += 1
            errors.append(f"{driver_id}: stripe_transfer_error")

    final_status = "completed" if not errors else "completed"
    if drivers_failed > 0 and drivers_paid == 0:
        final_status = "failed"

    try:
        await db_supabase.update_one(
            "auto_payout_batches",
            {"id": batch_id},
            {
                "status": final_status,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "drivers_eligible": drivers_eligible,
                "drivers_paid": drivers_paid,
                "drivers_failed": drivers_failed,
                "total_amount": float(total_amount),
                "error_summary": "; ".join(errors[:50]) if errors else None,
            },
        )
    except Exception:
        logger.exception("[AUTO-PAYOUT] failed to update batch row %s", batch_id)

    logger.info(
        "[AUTO-PAYOUT] batch %s: eligible=%d paid=%d failed=%d total=$%s",
        week_key,
        drivers_eligible,
        drivers_paid,
        drivers_failed,
        total_amount,
    )
    return {
        "status": final_status,
        "week_key": week_key,
        "drivers_eligible": drivers_eligible,
        "drivers_paid": drivers_paid,
        "drivers_failed": drivers_failed,
        "total_amount": str(total_amount),
    }


async def auto_payout_loop():
    """Background loop: runs hourly, fires the batch on Sundays only.

    Replay-safe: Redis leader lock prevents concurrent replicas from both
    running the batch; the week_key unique index is the hard guard if Redis
    is unavailable.
    """
    import os
    import socket

    pod_id = f"{socket.gethostname()}-{os.getpid()}"
    interval = 3600  # 1 hour

    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.weekday() != 6:  # 0=Mon, 6=Sun
                logger.debug("[AUTO-PAYOUT] not Sunday (day=%d), sleeping", now.weekday())
                await asyncio.sleep(interval)
                continue

            lock_ttl = int(interval * 0.85)
            try:
                got_lock = await redis_set_nx(LOCK_KEY, pod_id, lock_ttl)
            except Exception as lock_err:
                logger.error("[AUTO-PAYOUT] leader lock unavailable (%s), proceeding", lock_err)
                got_lock = True

            if not got_lock:
                logger.debug("[AUTO-PAYOUT] another replica holds the lock, sleeping")
                await asyncio.sleep(interval)
                continue

            result = await run_weekly_auto_payout()
            logger.info("[AUTO-PAYOUT] loop result: %s", result)

        except Exception:
            logger.exception("[AUTO-PAYOUT] loop iteration failed")

        await asyncio.sleep(interval)
