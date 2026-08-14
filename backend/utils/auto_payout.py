"""Spinr-controlled weekly auto-payout — replaces driver-initiated cashout.

Every Sunday (America/Regina — Saskatchewan is UTC-6 year-round; gating on
UTC would fire Saturday evening local and cut off Saturday-night earnings),
from 06:00 local, the hourly loop scans eligible drivers and creates one
``stripe.Transfer`` per driver with payable balance in [$10, $5,000],
recording the payout in ``payouts`` with ``payout_type='auto'``.

Replay-safety contract (mandatory per CLAUDE.md background-task rules):
  - Redis leader lock (fail-open, loud) prevents redundant concurrent runs;
    the ``auto_payout_batches.week_key`` unique index is the hard guard.
  - Deterministic payout id ``auto-{driver_id}-{week_key}`` + migration
    250's partial unique index ``idx_payouts_one_inflight_per_driver``
    (one in-flight payout row per driver, any type) make the reserve step
    mutually exclusive with instant payouts. Do NOT rename the 'reserved'
    status — that index is what prevents an auto/instant double-pay race.
  - The transfer AMOUNT IS PINNED on the reserved row. Every retry sends
    the row's amount, never a recomputed balance, so a Stripe idempotency
    key can be replayed without same-key-different-params conflicts.
  - Idempotency keys are attempt-scoped: ``auto-payout-{driver}-{week}``
    for the first attempt, ``...-r{n}`` after retryable failures.

Error taxonomy (prevents the timeout-after-success double-pay):
  - Definitive Stripe rejections (bad account, etc.)  -> status='failed'
    (+failure_reason). Money never left; next week retries fresh.
  - Retryable rejections (balance_insufficient, rate limit) -> row STAYS
    'reserved' (+failure_reason); the hourly sweep retries with a NEW key
    (Stripe replays cached 4xx responses on key reuse).
  - Ambiguous outcomes (connection error / timeout / unknown) -> row STAYS
    'reserved'; the sweep replays the SAME key within Stripe's 24h window,
    which returns the original transfer if it actually succeeded. Rows
    older than the safe window escalate to needs_manual_reconcile instead
    of blind-retrying (a post-expiry replay would double-transfer).
  'reserved' rows keep deducting from the balance, so every failure mode
  degrades toward temporarily-under-paid, never double-paid.

Crash recovery:
  - A batch row stuck in 'running' (mid-run crash) is claimed and resumed
    on the next Sunday tick (staleness-gated conditional update); 'partial'
    and 'failed' batches are likewise re-entered through the day. Existing
    payout rows dispatch per state: completed -> skip, reserved -> retry
    with pinned amount, failed -> leave for next week.
  - The stale-'reserved' sweep runs every hourly tick (any day), so money
    stranded by a crash is retried or escalated — it can no longer freeze a
    driver's payouts forever (migration 250's index blocks new rows while a
    stale one exists).

Balance parity: _compute_payable_balance mirrors
routes/drivers/earnings.get_driver_balance term for term (shared filters
imported from utils.legacy_rides; _ride_income/_ride_tax mirror
routes/drivers/_shared.py, which utils cannot import). Any change to the
endpoint's composition MUST be mirrored here — see test_auto_payout.py's
parity test.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

try:
    from .. import db_supabase
    from ..utils.error_handling import DuplicateRecordError
    from ..utils.legacy_rides import EXCLUDE_LEGACY_RIDES, drop_legacy_offset_payouts
    from ..utils.money import dollars_to_cents
    from ..utils.redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual-import pattern
    import db_supabase  # type: ignore
    from utils.error_handling import DuplicateRecordError  # type: ignore
    from utils.legacy_rides import EXCLUDE_LEGACY_RIDES, drop_legacy_offset_payouts  # type: ignore
    from utils.money import dollars_to_cents  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .metrics import inc as _metric_inc
except ImportError:  # pragma: no cover

    def _metric_inc(name: str, labels: dict | None = None) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger(__name__)

MIN_PAYOUT_AMOUNT = Decimal("10.00")
# Unattended circuit breaker (mirrors the instant-payout ceiling). A computed
# balance above this is a data-anomaly signal, not a payout — skip to manual
# review instead of wiring it straight to Stripe.
MAX_PAYOUT_AMOUNT = Decimal("5000.00")
LOCK_KEY = "spinr:auto_payout:lock"
_TWO_PLACES = Decimal("0.01")
_PAGE_SIZE = 500

# Saskatchewan never observes DST; "Sunday" must mean Sunday for the drivers.
_TZ = ZoneInfo("America/Regina")
_BATCH_START_HOUR_LOCAL = 6  # let Saturday-night rides settle before cutoff

_STALE_RUNNING_MINUTES = 45  # claim-and-resume a 'running' batch older than this
_SWEEP_MIN_AGE_MINUTES = 30  # leave freshly-written rows to the in-flight batch
_SAME_KEY_REPLAY_MAX_HOURS = 20  # safety margin under Stripe's 24h key window
_MAX_RETRYABLE_ATTEMPTS = 5
_MANUAL_RECONCILE_PREFIX = "needs_manual_reconcile"

# Mirrors payouts.py::_GST_BN_RE / _sin_on_file — the CRA gates every
# driver-initiated payout path enforces. utils cannot import routes, so the
# checks are replicated; keep in sync with routes/drivers/payouts.py.
_GST_BN_RE = re.compile(r"^\d{9}(RT\d{4})?$")


def _d(v) -> Decimal:
    from decimal import InvalidOperation

    try:
        return Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def _ride_income(r: dict) -> Decimal:
    # Mirror of routes/drivers/_shared.py::_ride_income — keep in sync.
    if r.get("driver_earnings") is not None:
        return _d(r.get("driver_earnings"))
    return _d(r.get("base_fare")) + _d(r.get("distance_fare")) + _d(r.get("time_fare")) + _d(r.get("tip_amount"))


def _ride_tax(r: dict) -> Decimal:
    # Mirror of routes/drivers/_shared.py::_ride_tax — keep in sync.
    tax = _d(r.get("tax_amount"))
    if tax != Decimal("0"):
        return tax
    snap = r.get("fare_breakdown_snapshot") or {}
    for line in snap.get("lines") or []:
        if line.get("type") in ("tax", "gst", "pst"):
            tax += _d(line.get("amount"))
    return tax


def current_week_key() -> str:
    """ISO year-week string for today in America/Regina, e.g. '2026-W33'."""
    today = datetime.now(_TZ).date()
    iso = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _payout_id_for(driver_id: str, week_key: str) -> str:
    return f"auto-{driver_id}-{week_key}"


def _idempotency_key(driver_id: str, week_key: str, attempt: int) -> str:
    base = f"auto-payout-{driver_id}-{week_key}"
    return base if attempt <= 0 else f"{base}-r{attempt}"


_WEEK_KEY_RE = re.compile(r"^auto-(?P<driver>.+)-(?P<week>\d{4}-W\d{2})$")


def _parse_payout_id(payout_id: str) -> tuple[str, str] | None:
    m = _WEEK_KEY_RE.match(payout_id or "")
    return (m.group("driver"), m.group("week")) if m else None


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_minutes(ts: str | None, now: datetime) -> float:
    parsed = _parse_iso(ts)
    if parsed is None:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 60.0


def _eligibility_skip_reason(driver: dict) -> str | None:
    """CRA + destination-account gates, mirroring the driver-initiated paths.

    A skipped driver's balance simply carries; nothing is written.
    """
    if not driver.get("stripe_account_id"):
        return "no_stripe_account"
    if not driver.get("stripe_payouts_enabled"):
        # Transfers to a payouts-disabled account "succeed" into a frozen
        # connected balance the driver can't reach — skip until KYC clears.
        return "stripe_payouts_disabled"
    if driver.get("is_suspended"):
        return "suspended"
    bn = (driver.get("gst_bn") or "").replace(" ", "").upper()
    if not _GST_BN_RE.match(bn):
        return "missing_gst"
    if not (driver.get("sin") or driver.get("stripe_id_number_provided")):
        return "missing_sin"
    return None


async def _notify_driver(driver: dict, title: str, body: str, data: dict | None = None) -> None:
    """Best-effort push — a notification failure must never affect money state."""
    user_id = driver.get("user_id")
    if not user_id:
        return
    try:
        try:
            from ..features import send_push_notification
        except ImportError:  # pragma: no cover
            from features import send_push_notification  # type: ignore
        await send_push_notification(user_id, title, body, data or {})
    except Exception:
        logger.warning("[AUTO-PAYOUT] push notification failed for driver %s", driver.get("id"))


async def _compute_payable_balance(driver_id: str) -> Decimal:
    """Same formula as routes/drivers/earnings.get_driver_balance — see the
    module docstring's parity note and the parity test."""
    ZERO = Decimal("0")

    rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "status": "completed", **EXCLUDE_LEGACY_RIDES},
        limit=10000,
    )
    ride_earnings = sum((_ride_income(r) for r in rides), ZERO)
    total_tax = sum((_ride_tax(r) for r in rides), ZERO)

    total_incentives = ZERO
    ride_ids = [r["id"] for r in rides if r.get("id")]
    if ride_ids:
        # Sync supabase client call — keep it off the event loop (every other
        # DB call here goes through run_sync's thread pool already).
        claims_result = await asyncio.to_thread(
            lambda: (
                db_supabase.supabase.table("ride_incentive_claims")
                .select("bonus_amount")
                .in_("ride_id", ride_ids)
                .execute()
            )
        )
        claims = claims_result.data or []
        total_incentives = sum((_d(c.get("bonus_amount") or 0) for c in claims), ZERO)

    cancelled_rides = await db_supabase.get_rows("rides", {"driver_id": driver_id, "status": "cancelled"}, limit=10000)
    total_cancel_fees = sum((_d(r.get("cancellation_fee_driver") or 0) for r in cancelled_rides), ZERO)

    total_earnings = ride_earnings + total_tax + total_incentives + total_cancel_fees

    bonus_rows = await db_supabase.get_rows("driver_bonuses", {"driver_id": driver_id}, limit=10000)
    total_bonuses = sum((_d(b.get("amount") or 0) for b in bonus_rows), ZERO)

    payout_rows = drop_legacy_offset_payouts(
        await db_supabase.get_rows("payouts", {"driver_id": driver_id}, limit=5000)
    )
    _not_money_out = {"reversed", "failed"}
    total_payouts = sum(
        (
            _d(p.get("amount") or 0)
            for p in payout_rows
            if str(p.get("status") or "").lower() not in _not_money_out and p.get("payout_type") != "stripe_sync"
        ),
        ZERO,
    )

    return total_earnings + total_bonuses - total_payouts


async def _fetch_eligible_drivers() -> list[dict]:
    """Drivers with a Stripe Connect account — filtered server-side."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = await db_supabase.get_rows(
            "drivers",
            {"stripe_account_id": {"$notnull": True}},
            limit=_PAGE_SIZE,
            offset=offset,
            order="id",
        )
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def _classify_stripe_error(exc: Exception) -> str:
    """'permanent' | 'retryable' | 'ambiguous' — see module docstring."""
    import stripe as stripe_lib

    err_ns = getattr(stripe_lib, "error", stripe_lib)
    conn_err = getattr(err_ns, "APIConnectionError", ())
    rate_err = getattr(err_ns, "RateLimitError", ())
    stripe_err = getattr(err_ns, "StripeError", ())

    if conn_err and isinstance(exc, conn_err):
        return "ambiguous"  # request may have reached Stripe — never assume it failed
    if rate_err and isinstance(exc, rate_err):
        return "retryable"
    if stripe_err and isinstance(exc, stripe_err):
        code = getattr(exc, "code", None) or ""
        if code == "balance_insufficient":
            return "retryable"  # platform balance replenishes as charges settle
        if code == "idempotency_error":
            return "ambiguous"  # key/params mismatch — a bug; hands off, reconcile
        return "permanent"
    return "ambiguous"  # unknown exception — safe direction is under-paid, not double


def _failure_reason_of(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return (f"{code}: {exc}" if code else str(exc))[:500]


async def _attempt_transfer(
    stripe_secret: str, driver_id: str, week_key: str, payout_id: str, amount: Decimal, attempt: int
) -> dict:
    """One Stripe Transfer attempt with the row's pinned amount.

    Returns {"outcome": "completed"|"failed"|"reserved", "transfer_id",
    "failure_reason", "classification"}.
    """
    import stripe as stripe_lib

    idem_key = _idempotency_key(driver_id, week_key, attempt)
    try:
        transfer = await asyncio.to_thread(
            lambda: stripe_lib.Transfer.create(
                amount=dollars_to_cents(amount),
                currency="cad",
                destination=_transfer_destination_cache[payout_id],
                api_key=stripe_secret,
                idempotency_key=idem_key,
                transfer_group=f"auto-{week_key}",
                metadata={"payout_id": payout_id, "driver_id": driver_id},
            )
        )
        return {"outcome": "completed", "transfer_id": transfer.id, "failure_reason": None, "classification": None}
    except Exception as e:
        classification = _classify_stripe_error(e)
        reason = _failure_reason_of(e)
        logger.error("[AUTO-PAYOUT] transfer %s for driver %s: %s (%s)", classification, driver_id, reason, payout_id)
        if classification == "permanent":
            return {
                "outcome": "failed",
                "transfer_id": None,
                "failure_reason": reason,
                "classification": classification,
            }
        # retryable/ambiguous: row stays 'reserved' (still deducts — safe),
        # the sweep or a resume pass retries per the taxonomy rules.
        return {"outcome": "reserved", "transfer_id": None, "failure_reason": reason, "classification": classification}


# destination acct id per payout row, populated just before _attempt_transfer.
# Avoids widening the retry helpers' signatures for one pass-through value.
_transfer_destination_cache: dict[str, str] = {}


async def _finalize_payout_row(payout_id: str, result: dict, retry_bump: bool) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    if result["outcome"] == "completed":
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {
                "status": "completed",
                "stripe_transfer_id": result["transfer_id"],
                "failure_reason": None,
                "updated_at": now_iso,
            },
        )
    elif result["outcome"] == "failed":
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {"status": "failed", "failure_reason": result["failure_reason"], "updated_at": now_iso},
        )
    else:  # stays reserved
        updates = {"failure_reason": result["failure_reason"], "updated_at": now_iso}
        if retry_bump:
            updates["auto_retry_count"] = result.get("next_attempt", 1)
        await db_supabase.update_one("payouts", {"id": payout_id}, updates)


async def _retry_reserved_row(row: dict, stripe_secret: str, now: datetime) -> str:
    """Retry one stale 'reserved' auto-payout row. Returns the outcome.

    Shared by the resume pass and the hourly sweep. Enforces the taxonomy:
    ambiguous rows replay the SAME key only inside the safe window; retryable
    rows get a NEW key; anything unresolvable escalates to manual reconcile
    (row stays reserved so the balance keeps the money earmarked).
    """
    payout_id = row.get("id") or ""
    parsed = _parse_payout_id(payout_id)
    if not parsed:
        return "unparseable"
    driver_id, week_key = parsed
    reason = row.get("failure_reason") or ""
    if reason.startswith(_MANUAL_RECONCILE_PREFIX):
        return "escalated"

    attempt = int(row.get("auto_retry_count") or 0)
    amount = _d(row.get("amount") or 0)
    if amount <= 0:
        return "unparseable"

    last_touch_age_h = _age_minutes(row.get("updated_at") or row.get("created_at"), now) / 60.0
    was_retryable = (
        reason.split(":", 1)[0] in ("balance_insufficient", "rate_limit") or "balance_insufficient" in reason
    )

    if attempt >= _MAX_RETRYABLE_ATTEMPTS or (not was_retryable and last_touch_age_h > _SAME_KEY_REPLAY_MAX_HOURS):
        # Past the same-key replay window (the original may have succeeded at
        # Stripe) or out of attempts — a blind retry risks double-pay. Park it
        # loudly for ops; metadata.payout_id makes the Stripe-side lookup easy.
        await db_supabase.update_one(
            "payouts",
            {"id": payout_id},
            {
                "failure_reason": f"{_MANUAL_RECONCILE_PREFIX}: {reason or 'stale reserved row'}"[:500],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.error("[AUTO-PAYOUT] payout %s escalated to manual reconcile (attempt=%d)", payout_id, attempt)
        _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})
        return "escalated"

    next_attempt = attempt + 1 if was_retryable else attempt
    driver_rows = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
    if not driver_rows or not driver_rows[0].get("stripe_account_id"):
        return "skipped"
    _transfer_destination_cache[payout_id] = driver_rows[0]["stripe_account_id"]

    result = await _attempt_transfer(stripe_secret, driver_id, week_key, payout_id, amount, next_attempt)
    result["next_attempt"] = next_attempt
    await _finalize_payout_row(payout_id, result, retry_bump=was_retryable)
    if result["outcome"] == "completed":
        await _notify_driver(
            driver_rows[0],
            "Weekly payout sent",
            f"Your payout of ${amount} is on its way to your bank.",
            {"type": "auto_payout_completed"},
        )
    return result["outcome"]


async def sweep_stale_reserved(stripe_secret: str) -> dict:
    """Hourly, any day: retry or escalate stranded 'reserved' auto rows.

    This is what guarantees a crash can no longer freeze a driver's payouts —
    migration 250's one-in-flight index blocks all new payout rows while a
    stale reserved row exists, so the stale row itself must be driven to a
    terminal state.
    """
    now = datetime.now(timezone.utc)
    rows = await db_supabase.get_rows(
        "payouts", {"payout_type": "auto", "status": "reserved"}, limit=200, order="created_at"
    )
    counts = {"retried": 0, "completed": 0, "escalated": 0}
    for row in rows:
        if _age_minutes(row.get("updated_at") or row.get("created_at"), now) < _SWEEP_MIN_AGE_MINUTES:
            continue
        try:
            outcome = await _retry_reserved_row(row, stripe_secret, now)
        except Exception:
            logger.exception("[AUTO-PAYOUT] sweep failed for payout %s", row.get("id"))
            _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})
            continue
        if outcome == "completed":
            counts["completed"] += 1
        elif outcome == "escalated":
            counts["escalated"] += 1
        elif outcome in ("failed", "reserved"):
            counts["retried"] += 1
    if any(counts.values()):
        logger.info("[AUTO-PAYOUT] sweep: %s", counts)
    return counts


async def finalize_stale_running_batches() -> None:
    """Mark batches stuck 'running' >24h as 'partial' so the ledger is honest.
    Their stranded money is handled row-by-row by the sweep."""
    now = datetime.now(timezone.utc)
    rows = await db_supabase.get_rows("auto_payout_batches", {"status": "running"}, limit=10)
    for b in rows:
        if _age_minutes(b.get("started_at"), now) > 24 * 60:
            await db_supabase.update_one(
                "auto_payout_batches",
                {"id": b["id"], "status": "running"},
                {"status": "partial", "error_summary": "auto-finalized: stale running batch (crashed mid-run)"},
            )
            logger.error("[AUTO-PAYOUT] batch %s auto-finalized as partial (stale running)", b.get("id"))


async def run_weekly_auto_payout() -> dict:
    """Execute (or resume) this week's auto-payout batch. Returns a summary."""
    week_key = current_week_key()
    batch_id = f"auto-batch-{week_key}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    existing = await db_supabase.get_rows("auto_payout_batches", {"week_key": week_key}, limit=1)
    resumed = False
    if existing:
        batch = existing[0]
        status = batch.get("status")
        if status == "completed":
            logger.info("[AUTO-PAYOUT] week %s already completed, skipping", week_key)
            return {"status": "already_completed", "week_key": week_key}
        if status == "running" and _age_minutes(batch.get("started_at"), now) < _STALE_RUNNING_MINUTES:
            logger.info("[AUTO-PAYOUT] batch %s appears live on another replica, skipping", batch_id)
            return {"status": "already_running", "week_key": week_key}
        claimed = await db_supabase.update_one(
            "auto_payout_batches",
            {"id": batch_id, "status": {"$in": ["running", "partial", "failed"]}},
            {"status": "running", "started_at": now_iso},
        )
        if not claimed:
            logger.info("[AUTO-PAYOUT] lost resume claim for %s, skipping", batch_id)
            return {"status": "already_running", "week_key": week_key}
        resumed = True
        logger.warning("[AUTO-PAYOUT] resuming batch %s (was %s)", batch_id, status)

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings()

    _flag = settings.get("auto_payout_enabled")
    if _flag is False or str(_flag).strip().lower() == "false":
        logger.warning("[AUTO-PAYOUT] disabled via app_settings.auto_payout_enabled, skipping week %s", week_key)
        return {"status": "disabled", "week_key": week_key}

    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.error("[AUTO-PAYOUT] stripe_secret_key not configured, skipping")
        return {"status": "stripe_not_configured"}

    if not resumed:
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
        except DuplicateRecordError:
            logger.info("[AUTO-PAYOUT] batch %s already claimed by a concurrent replica, skipping", batch_id)
            return {"status": "already_running", "week_key": week_key}

    drivers = await _fetch_eligible_drivers()
    drivers_eligible = 0
    drivers_paid = 0
    drivers_failed = 0
    skipped: dict[str, int] = {}
    total_amount = Decimal("0")
    errors: list[str] = []

    for driver in drivers:
        driver_id = driver["id"]

        skip_reason = _eligibility_skip_reason(driver)
        if skip_reason:
            skipped[skip_reason] = skipped.get(skip_reason, 0) + 1
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
        if balance > MAX_PAYOUT_AMOUNT:
            logger.error(
                "[AUTO-PAYOUT] driver %s balance $%s exceeds cap $%s — skipped for manual review",
                driver_id,
                balance,
                MAX_PAYOUT_AMOUNT,
            )
            errors.append(f"{driver_id}: over_cap_requires_review")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})
            continue

        drivers_eligible += 1
        payout_id = _payout_id_for(driver_id, week_key)

        try:
            await db_supabase.insert_one(
                "payouts",
                {
                    "id": payout_id,
                    "driver_id": driver_id,
                    "amount": balance,  # Decimal — _serialize_for_api handles it
                    "status": "reserved",
                    "payout_type": "auto",
                    "bank_name": "Auto Payout",
                    "auto_retry_count": 0,
                    "created_at": now_iso,
                },
            )
        except DuplicateRecordError:
            existing_rows = await db_supabase.get_rows("payouts", {"id": payout_id}, limit=1)
            if not existing_rows:
                # The conflict was migration 250's one-in-flight-per-driver
                # index, not this week's row: an instant payout is in flight
                # or a stale reserved row from another week exists. The sweep
                # owns stale rows; log loudly either way.
                logger.warning("[AUTO-PAYOUT] driver %s blocked by an unrelated in-flight payout row", driver_id)
                errors.append(f"{driver_id}: inflight_conflict")
                continue
            row = existing_rows[0]
            row_status = str(row.get("status") or "").lower()
            if row_status == "completed":
                drivers_paid += 1
                total_amount += _d(row.get("amount") or 0)
                continue
            if row_status == "failed":
                drivers_failed += 1
                errors.append(f"{driver_id}: previously_failed")
                continue
            # reserved from a crashed pass — retry with the row's pinned amount
            try:
                outcome = await _retry_reserved_row(row, stripe_secret, now)
            except Exception:
                logger.exception("[AUTO-PAYOUT] retry of reserved payout %s failed", payout_id)
                drivers_failed += 1
                errors.append(f"{driver_id}: retry_error")
                continue
            if outcome == "completed":
                drivers_paid += 1
                total_amount += _d(row.get("amount") or 0)
            else:
                drivers_failed += 1
                errors.append(f"{driver_id}: {outcome}")
            continue
        except Exception:
            logger.exception("[AUTO-PAYOUT] reserve failed for driver %s", driver_id)
            drivers_failed += 1
            errors.append(f"{driver_id}: reserve_error")
            continue

        _transfer_destination_cache[payout_id] = driver["stripe_account_id"]
        result = await _attempt_transfer(stripe_secret, driver_id, week_key, payout_id, balance, attempt=0)
        try:
            await _finalize_payout_row(payout_id, result, retry_bump=False)
        except Exception:
            logger.exception("[AUTO-PAYOUT] failed to record outcome for %s", payout_id)

        if result["outcome"] == "completed":
            drivers_paid += 1
            total_amount += balance
            await _notify_driver(
                driver,
                "Weekly payout sent",
                f"Your payout of ${balance} is on its way to your bank.",
                {"type": "auto_payout_completed"},
            )
        elif result["outcome"] == "failed":
            drivers_failed += 1
            errors.append(f"{driver_id}: stripe_{result['failure_reason'][:40]}")
            await _notify_driver(
                driver,
                "Payout needs attention",
                "We couldn't send your weekly payout. Our team has been notified and your balance is safe.",
                {"type": "auto_payout_failed"},
            )
        else:  # stays reserved; sweep/resume will retry
            drivers_failed += 1
            errors.append(f"{driver_id}: deferred_{result['classification']}")

    if drivers_failed == 0 and not errors:
        final_status = "completed"
    elif drivers_paid == 0 and drivers_failed > 0:
        final_status = "failed"
    else:
        final_status = "partial"  # some paid, some pending/failed — resumable

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
                "total_amount": total_amount,  # Decimal — serialized downstream
                "error_summary": "; ".join(errors[:50]) if errors else None,
            },
        )
    except Exception:
        logger.exception("[AUTO-PAYOUT] failed to update batch row %s", batch_id)

    if errors:
        _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})
    logger.info(
        "[AUTO-PAYOUT] batch %s: status=%s eligible=%d paid=%d failed=%d skipped=%s total=$%s%s",
        week_key,
        final_status,
        drivers_eligible,
        drivers_paid,
        drivers_failed,
        skipped or "{}",
        total_amount,
        " (resumed)" if resumed else "",
    )
    return {
        "status": final_status,
        "week_key": week_key,
        "drivers_eligible": drivers_eligible,
        "drivers_paid": drivers_paid,
        "drivers_failed": drivers_failed,
        "skipped": skipped,
        "errors": errors[:10],
        "total_amount": str(total_amount),
        "resumed": resumed,
    }


def _is_batch_window(now_local: datetime) -> bool:
    return now_local.weekday() == 6 and now_local.hour >= _BATCH_START_HOUR_LOCAL


async def auto_payout_loop():
    """Hourly loop: heartbeat + stale-reserved sweep every tick; the weekly
    batch fires on Sundays (America/Regina) from 06:00 local, leader-locked."""
    import os
    import socket

    pod_id = f"{socket.gethostname()}-{os.getpid()}"
    interval = 3600  # 1 hour

    while True:
        try:
            try:
                from ..settings_loader import get_app_settings
            except ImportError:
                from settings_loader import get_app_settings  # type: ignore
            settings = await get_app_settings()
            _flag = settings.get("auto_payout_enabled")
            enabled = not (_flag is False or str(_flag).strip().lower() == "false")
            stripe_secret = settings.get("stripe_secret_key", "")

            if enabled and stripe_secret:
                try:
                    await sweep_stale_reserved(stripe_secret)
                except Exception:
                    logger.exception("[AUTO-PAYOUT] stale-reserved sweep failed")
                try:
                    await finalize_stale_running_batches()
                except Exception:
                    logger.exception("[AUTO-PAYOUT] stale-batch finalize failed")

                now_local = datetime.now(_TZ)
                if _is_batch_window(now_local):
                    lock_ttl = int(interval * 0.85)
                    try:
                        got_lock = await redis_set_nx(LOCK_KEY, pod_id, lock_ttl)
                    except Exception as lock_err:
                        logger.error("[AUTO-PAYOUT] leader lock unavailable (%s), proceeding", lock_err)
                        got_lock = True
                    if got_lock:
                        result = await run_weekly_auto_payout()
                        logger.info("[AUTO-PAYOUT] loop result: %s", result)
                    else:
                        logger.debug("[AUTO-PAYOUT] another replica holds the lock, sleeping")
        except Exception:
            logger.exception("[AUTO-PAYOUT] loop iteration failed")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})

        _record_heartbeat("auto_payout (1h, Sundays)")
        await asyncio.sleep(interval)
