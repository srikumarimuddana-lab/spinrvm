"""Ledger writes — durable header inserts plus optional double-entry legs.

Two problems this module exists to solve:

1. **The header write was silently best-effort.** ``record_payment_event`` in
   payment_service.py caught every exception, logged, and returned. A failed
   ``financial_events`` INSERT left a real Stripe charge with no ledger row, so
   the 7-year CRA/SK tax record under-stated collected revenue and GST with
   nothing but a log line to show for it. CLAUDE.md forbids exactly this
   ("never logger.warning and continue on a DB/auth/payment error").

   The write is now retried with a client-supplied primary key, so a retry
   after a timeout that actually committed is a no-op rather than a duplicate.
   If every attempt fails we escalate to Sentry with a matchable alert tag —
   but we still never raise. The money has already moved at that point; failing
   the request would show the rider a payment error for a charge that
   succeeded, which is strictly worse than a loud alert.

2. **The ledger was single-entry.** ``financial_events.delta_cents`` is a signed
   scalar with no contra-account, so it cannot be balanced or turned into a
   trial balance. ``financial_event_entries`` (migration 286) adds the legs.
   Writing them is gated on the ``ledger_double_entry_enabled`` app_settings
   flag so the schema can ship dark and be switched on without a redeploy.

Leg convention: ``amount_cents`` is ALWAYS positive and direction is carried by
``side``. This is the opposite of the header's signed ``delta_cents``, and it is
what makes "debits equal credits" a checkable assertion rather than a tautology.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
except ImportError:  # python -m backend.server vs top-level
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore


# ── Chart of accounts ────────────────────────────────────────────────
# Mirrors the CHECK constraint in migration 286. Kept in sync deliberately:
# the DB rejects an unknown account as defence-in-depth, and this set makes the
# same mistake fail in unit tests instead of silently dropping the legs.
ACCT_STRIPE_RECEIVABLE = "stripe_receivable"
ACCT_DRIVER_PAYABLE = "driver_payable"
ACCT_TAX_PAYABLE = "tax_payable"
ACCT_PLATFORM_REVENUE = "platform_revenue"
ACCT_RIDER_WALLET = "rider_wallet"
ACCT_CORPORATE_WALLET = "corporate_wallet"
ACCT_PROMO_EXPENSE = "promo_expense"

LEDGER_ACCOUNTS = frozenset(
    {
        ACCT_STRIPE_RECEIVABLE,
        ACCT_DRIVER_PAYABLE,
        ACCT_TAX_PAYABLE,
        ACCT_PLATFORM_REVENUE,
        ACCT_RIDER_WALLET,
        ACCT_CORPORATE_WALLET,
        ACCT_PROMO_EXPENSE,
    }
)

DEBIT = "debit"
CREDIT = "credit"

# Retry budget for the header insert. Short and bounded: this runs inline in the
# settlement request, and the SLA for fare settlement is P95 < 1 s.
_INSERT_ATTEMPTS = 3
_INSERT_BACKOFF_SECONDS = (0.2, 0.5)


def to_cents(amount: Any) -> int:
    """Convert a dollar amount to integer cents via Decimal only.

    Never float — CLAUDE.md money rule. ``Decimal(str(x))`` so a float input
    that slipped through upstream is still converted from its printed form
    rather than its binary expansion.
    """
    return int((Decimal(str(amount or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Leg:
    """One side of a double-entry pair. ``amount_cents`` is always > 0."""

    account: str
    side: str
    amount_cents: int


class UnbalancedLedgerError(ValueError):
    """Debits did not equal credits. Never written — always a code defect."""


def assert_balanced(legs: Sequence[Leg]) -> None:
    """Validate legs before they reach the DB.

    Raises rather than logging: an unbalanced set is a programming error in the
    leg builder, and callers treat it as "skip the legs, keep the header" —
    a half-written journal entry is worse than none.
    """
    if not legs:
        return
    for leg in legs:
        if leg.account not in LEDGER_ACCOUNTS:
            raise UnbalancedLedgerError(f"unknown ledger account {leg.account!r}")
        if leg.side not in (DEBIT, CREDIT):
            raise UnbalancedLedgerError(f"invalid side {leg.side!r}")
        if leg.amount_cents <= 0:
            raise UnbalancedLedgerError(f"leg amount must be positive, got {leg.amount_cents}")

    debits = sum(leg.amount_cents for leg in legs if leg.side == DEBIT)
    credits = sum(leg.amount_cents for leg in legs if leg.side == CREDIT)
    if debits != credits:
        raise UnbalancedLedgerError(f"debits {debits} != credits {credits}")


# ── Leg builders ─────────────────────────────────────────────────────


def build_charge_legs(total_cents: int, driver_cents: int, tax_cents: int, promo_cents: int = 0) -> List[Leg]:
    """Legs for a rider card charge.

        DR stripe_receivable   total collected from the rider
        DR promo_expense       discount Spinr absorbed (contra-revenue)
           CR driver_payable     driver's share (100% of ride fare + tip)
           CR tax_payable        GST/PST owed
           CR platform_revenue   remainder — booking fee, airport fee, residual

    ``promo_cents`` is load-bearing, not cosmetic. ``driver_earnings`` is
    derived from ``total_fare`` BEFORE any discount (fare_service: it is
    ``total_fare - admin_earnings``), while the rider is charged
    ``grand_total = total_fare + area_fees + tax - discount``. So without the
    promo debit the residual is ``area_fees + admin_earnings - discount``,
    which goes NEGATIVE on any promo bigger than the fee floor — and this
    function would then refuse to build legs at all, collapsing the whole
    charge into a degraded platform_revenue entry. With it, the residual is
    ``area_fees + admin_earnings`` regardless of discount size, which is the
    accounting answer: the platform earns its fees gross and expenses the
    promo separately.

    Platform revenue is still the PLUG, so the entry always balances by
    construction. That is deliberate but worth knowing: if ``driver_earnings``
    is mis-stated upstream, the error lands silently in platform_revenue rather
    than making the journal unbalanced. The trial-balance view cannot catch
    that class of bug — reconciliation against Stripe is what does.

    Returns [] when there is nothing to record (comp/$0 ride) or when the inputs
    are internally inconsistent (driver + tax exceeding total + promo), rather
    than fabricating a negative leg.
    """
    if total_cents <= 0:
        return []
    promo_cents = promo_cents or 0
    residual = total_cents + promo_cents - driver_cents - tax_cents
    if driver_cents < 0 or tax_cents < 0 or promo_cents < 0 or residual < 0:
        logger.error(
            "[LEDGER] refusing to build charge legs — inconsistent amounts "
            "total={} driver={} tax={} promo={} residual={}",
            total_cents,
            driver_cents,
            tax_cents,
            promo_cents,
            residual,
        )
        return []

    legs = [Leg(ACCT_STRIPE_RECEIVABLE, DEBIT, total_cents)]
    # Zero-value legs are omitted: the DB CHECK requires amount_cents > 0, and a
    # $0 tax line carries no information.
    if promo_cents > 0:
        legs.append(Leg(ACCT_PROMO_EXPENSE, DEBIT, promo_cents))
    if driver_cents > 0:
        legs.append(Leg(ACCT_DRIVER_PAYABLE, CREDIT, driver_cents))
    if tax_cents > 0:
        legs.append(Leg(ACCT_TAX_PAYABLE, CREDIT, tax_cents))
    if residual > 0:
        legs.append(Leg(ACCT_PLATFORM_REVENUE, CREDIT, residual))
    return legs


def build_refund_legs(refund_cents: int, tax_reversed_cents: int) -> List[Leg]:
    """Legs for a rider refund. Mirror image of the charge.

        DR tax_payable        GST/PST no longer owed
        DR platform_revenue   everything else the platform gives back
           CR stripe_receivable   money leaving

    ``driver_payable`` is deliberately untouched: Spinr policy is that the
    driver KEEPS their pay on a refund and the platform absorbs it (see
    payment_service.record_refund_event). That absorbed amount is part of the
    platform_revenue debit.
    """
    if refund_cents <= 0:
        return []
    tax_reversed_cents = max(0, min(tax_reversed_cents, refund_cents))
    platform_absorbed = refund_cents - tax_reversed_cents

    legs: List[Leg] = []
    if tax_reversed_cents > 0:
        legs.append(Leg(ACCT_TAX_PAYABLE, DEBIT, tax_reversed_cents))
    if platform_absorbed > 0:
        legs.append(Leg(ACCT_PLATFORM_REVENUE, DEBIT, platform_absorbed))
    legs.append(Leg(ACCT_STRIPE_RECEIVABLE, CREDIT, refund_cents))
    return legs


# ── Durable write ────────────────────────────────────────────────────


def _is_duplicate_key(exc: Exception) -> bool:
    """True when the failure is 'this row already exists'.

    Retrying an insert that actually committed (but whose response was lost)
    surfaces as a unique/PK violation. That is success, not failure.
    """
    text = f"{exc} {getattr(exc, 'details', '')} {getattr(exc, 'code', '')}".lower()
    return "23505" in text or "duplicate key" in text or "already exists" in text


# Alert tags, most to least severe. Kept distinct so on-call can route them
# differently: a lost header is a hole in the 7-year tax record, whereas lost or
# unbalanced legs are a defect in the accounting overlay with the tax record
# still intact. LEGS_DEGRADED means the projection could not decompose an event
# (missing ride, inconsistent amounts) and booked it whole to platform_revenue —
# balanced and truthful at the money-in level, but the split is lost.
ALERT_HEADER_LOST = "ledger_write_failed"
ALERT_LEGS_LOST = "ledger_legs_lost"
ALERT_LEGS_UNBALANCED = "ledger_legs_unbalanced"
ALERT_LEGS_DEGRADED = "ledger_legs_degraded"
# Settlement could not be confirmed either way — the atomic RPC returned an
# ambiguous error AND the follow-up ride re-read failed, so we cannot tell
# whether the charge committed. The rider is shown "our team has been
# notified", which makes this the one tag that must always have a human
# behind it. Highest severity here.
ALERT_SETTLEMENT_UNVERIFIABLE = "settlement_state_unverifiable"


def escalate(message: str, context: Dict[str, Any], alert: str = ALERT_HEADER_LOST) -> None:
    """Tagged Sentry event so an alert rule can page on ledger loss.

    Public (was ``_escalate``): utils/ledger_projection.py and
    services/payment_service.py both raise their own tagged alerts through it,
    so the underscore was advertising a privacy this function does not have.
    The ``alert`` tag is caller-supplied precisely so other payment-domain
    modules can route their own classes — it is not ledger-internal.

    No-op when SENTRY_DSN is unset. PIPEDA: context carries IDs and amounts
    only — never names, phone numbers, emails, or coordinates.
    """
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            message,
            level="error",
            tags={
                "spinr_alert": alert,
                "domain": "payments",
                "surface": "backend",
            },
            contexts={"ledger": context},
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break settlement
        logger.debug("[LEDGER] Sentry escalation unavailable: {}", sentry_err)


async def _insert_with_retry(table: str, row: Dict[str, Any], *, what: str) -> bool:
    """Insert one row with bounded retry. Returns True on success (or duplicate)."""
    return await _attempt_insert(lambda: db_supabase.insert_one(table, row), what=what)


async def _insert_many_with_retry(table: str, rows: List[Dict[str, Any]], *, what: str) -> bool:
    """Insert all rows in ONE statement, with bounded retry.

    Batched deliberately: a per-row loop that fails halfway leaves a
    half-written journal entry in the DB — legs that no longer balance — which
    is exactly the state the balance validation exists to prevent. A single
    batch INSERT is one transaction, so either every leg lands or none does.
    """
    return await _attempt_insert(lambda: db_supabase.insert_many(table, rows), what=what)


def _client_unavailable() -> bool:
    """True when the Supabase client was never initialised.

    This has to be checked explicitly because ``insert_one`` and
    ``insert_many`` return ``None``/``[]`` in that state WITHOUT raising
    (repositories/_base.py). A bare ``await do_insert(); return True`` therefore
    reports the 7-year CRA/SK tax-ledger row as durably written when nothing
    reached a database at all — precisely the silent swallow CLAUDE.md forbids
    on a payment path, and invisible because it produces no exception to log.

    Production always has the client, so the reachable case is a startup init
    that failed or was skipped while the app went on serving traffic — which is
    exactly when a false "written" costs the most.

    Reads ``repositories._base.supabase``, not ``db_supabase.supabase``:
    db_supabase only re-exports the CRUD helpers, so ``db_supabase.insert_one``
    IS ``_base.insert_one`` and reads _base's globals. Checking the re-export
    would test a binding the writer never consults (the same reason
    tests/conftest.py patches both spellings).
    """
    try:
        from ..repositories import _base
    except ImportError:  # python -m backend.server vs top-level
        from repositories import _base  # type: ignore

    return not getattr(_base, "supabase", None)


async def _attempt_insert(do_insert, *, what: str) -> bool:
    """Shared retry loop. Returns True on success (or duplicate)."""
    if _client_unavailable():
        # Not retried: no number of attempts fixes an absent client, and the
        # caller's escalation is the point — a lost ledger row must be loud.
        logger.error(
            "[LEDGER] {} NOT WRITTEN — Supabase client is not initialised, so the insert "
            "would silently no-op. Reporting failure rather than a false success.",
            what,
        )
        return False

    last_err: Optional[Exception] = None
    for attempt in range(_INSERT_ATTEMPTS):
        try:
            await do_insert()
            return True
        except Exception as err:
            if _is_duplicate_key(err):
                # A previous attempt committed; the response was just lost.
                logger.info("[LEDGER] {} already present (duplicate key) — treating as written", what)
                return True
            last_err = err
            if attempt < _INSERT_ATTEMPTS - 1:
                await asyncio.sleep(_INSERT_BACKOFF_SECONDS[attempt])

    logger.opt(exception=last_err).error(
        "[LEDGER] {} FAILED after {} attempts — ledger row lost: {}", what, _INSERT_ATTEMPTS, last_err
    )
    return False


async def double_entry_enabled() -> bool:
    """Read the app_settings flag. Defaults to False on any read failure."""
    try:
        cfg = await get_app_settings()
        return bool(cfg.get("ledger_double_entry_enabled", False))
    except Exception as err:
        logger.warning("[LEDGER] could not read ledger_double_entry_enabled, assuming off: {}", err)
        return False


async def record_event(
    *,
    event_type: str,
    user_id: str,
    ride_id: Optional[str],
    delta_cents: int,
    ref: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    legs: Optional[Sequence[Leg]] = None,
) -> Optional[str]:
    """Write one journal header, plus its double-entry legs when enabled.

    Returns the event id on success, ``None`` if the header could not be
    written. Never raises — callers are on the far side of a completed money
    movement and must not fail the request because bookkeeping failed.
    """
    event_id = str(uuid.uuid4())
    header = {
        "id": event_id,  # client-supplied so a retry is idempotent
        "event_type": event_type,
        "user_id": user_id,
        "ride_id": ride_id,
        "delta_cents": delta_cents,
        "ref": ref,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    ok = await _insert_with_retry(
        "financial_events", header, what=f"financial_events {event_type} ride={ride_id} ref={ref}"
    )
    if not ok:
        escalate(
            "LEDGER WRITE FAILED — financial_events row lost",
            {
                "event_type": event_type,
                "ride_id": ride_id,
                "user_id": user_id,
                "delta_cents": delta_cents,
                "ref": ref,
            },
        )
        return None

    if legs:
        await write_legs(event_id, legs, ride_id=ride_id, event_type=event_type)

    return event_id


async def write_legs(
    event_id: str,
    legs: Sequence[Leg],
    *,
    ride_id: Optional[str],
    event_type: str,
    check_flag: bool = True,
) -> bool:
    """Validate and insert the double-entry legs. Never raises.

    Returns True when the legs are durably present (written now, or already
    there from an earlier attempt), False when they were skipped or lost.

    ``check_flag=True`` (default) gates on the ``ledger_double_entry_enabled``
    app setting — the right behavior for request-path callers. The projection
    loop passes ``check_flag=False`` because it checks the flag ONCE per tick
    before fetching its batch; re-reading per event would only add settings
    churn and a mid-batch flag flip would leave a partially-projected batch
    either way (harmless — the next tick picks up the remainder).

    A leg failure does NOT invalidate the header: the header is the tax record
    and is already durable. Missing legs surface via the reconciliation loop.
    """
    if check_flag and not await double_entry_enabled():
        return False
    try:
        assert_balanced(legs)
    except UnbalancedLedgerError as err:
        logger.error(
            "[LEDGER] refusing to write unbalanced legs for event {} ride {} ({}): {}",
            event_id,
            ride_id,
            event_type,
            err,
        )
        escalate(
            "LEDGER LEGS UNBALANCED — legs skipped",
            {"event_id": event_id, "ride_id": ride_id, "event_type": event_type, "error": str(err)},
            alert=ALERT_LEGS_UNBALANCED,
        )
        return False

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "event_id": event_id,
            "account": leg.account,
            "side": leg.side,
            "amount_cents": leg.amount_cents,
            "currency": "CAD",
            "created_at": now,
        }
        for leg in legs
    ]

    # One statement, one transaction — all legs or none. See
    # _insert_many_with_retry for why this must not be a per-row loop.
    ok = await _insert_many_with_retry(
        "financial_event_entries", rows, what=f"financial_event_entries x{len(rows)} event={event_id}"
    )
    if not ok:
        # The header (the tax record) is already durable, so this is a
        # completeness gap in the accounting overlay, not lost money. The
        # unbalanced-entries view stays clean because nothing was written.
        escalate(
            "LEDGER LEGS LOST — header written without double-entry legs",
            {"event_id": event_id, "ride_id": ride_id, "event_type": event_type, "leg_count": len(rows)},
            alert=ALERT_LEGS_LOST,
        )
    return ok
