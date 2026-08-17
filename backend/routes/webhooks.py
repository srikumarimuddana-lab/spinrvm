from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Request, Response

try:
    from .. import db_supabase
    from ..core.config import settings as app_config
    from ..db_supabase import (
        DatabaseError,
        DuplicateRecordError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
    from ..utils.money import cents_to_dollars
    from ..utils.rate_limiter import default_limiter
    from ..utils.rider_emails import send_refund_email, send_wallet_topup_email
except ImportError:
    import db_supabase
    from core.config import settings as app_config
    from db_supabase import (
        DatabaseError,
        DuplicateRecordError,
        claim_stripe_event,
        mark_stripe_event_processed,
        unclaim_stripe_event,
    )
    from features import send_push_notification
    from settings_loader import get_app_settings
    from utils.money import cents_to_dollars
    from utils.rate_limiter import default_limiter
    from utils.rider_emails import send_refund_email, send_wallet_topup_email
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

_TWO_PLACES = Decimal("0.01")

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# B-P2-2: Explicit allowlist of Stripe event types we process. Any event type
# NOT in this set is logged and acknowledged (200) without processing.
# Return 200 (not 400) for unknown events — 400 causes Stripe to retry for
# 3 days and creates noise; we want Stripe to stop re-sending them.
_STRIPE_HANDLED_EVENTS = frozenset(
    {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "checkout.session.completed",
        "charge.refunded",
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.closed",
        # Recurring Spinr Pass (mode="subscription" plans): renewal succeeded,
        # renewal failed (dunning), and status changes (past_due → canceled).
        "invoice.paid",
        "invoice.payment_failed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        # Connect Express KYC mirror — fired whenever the driver progresses
        # through Stripe's hosted onboarding (uploads ID, accepts ToS,
        # links bank), or whenever Stripe's verification team updates
        # the account's status. Drives the Payouts tab's Tax & Identity
        # section in the admin slideout.
        "account.updated",
        # Connected-account bank settlement of a driver payout. Lets us
        # reconcile the payouts row to the true outcome — an instant payout
        # marked "completed" synchronously can still be rejected by the bank
        # days later (closed account, etc.); payout.failed is how we learn.
        "payout.paid",
        "payout.failed",
    }
)
# Public alias exported for tests
ALLOWED_STRIPE_EVENTS = _STRIPE_HANDLED_EVENTS

# Routine Stripe lifecycle events we knowingly do NOT act on. They fire during
# normal payment flows (e.g. a manual-capture hold emits
# payment_intent.amount_capturable_updated; a capture emits charge.succeeded)
# when the Dashboard webhook is configured to send "all events". They are not
# bugs and not actionable, so log them at debug rather than warning to keep the
# signal-to-noise high — a genuinely unexpected event type still logs a warning
# so a missing handler stands out. Not exhaustive: anything not listed here and
# not handled still warns, by design.
_STRIPE_IGNORED_EVENTS = frozenset(
    {
        "payment_intent.created",
        "payment_intent.amount_capturable_updated",
        "payment_intent.canceled",
        "payment_intent.processing",
        "payment_intent.requires_action",
        "charge.succeeded",
        "charge.captured",
        "charge.updated",
        "charge.pending",
        "payment_method.attached",
        "payment_method.detached",
        "payment_method.updated",
        "customer.created",
        "customer.updated",
        "setup_intent.created",
        "setup_intent.succeeded",
        "invoice.created",
        "invoice.finalized",
        "invoice.updated",
        "invoice.payment_succeeded",
        "payout.created",
    }
)


async def _record_orphan_refund(
    *,
    charge: dict,
    payment_intent_id: str | None,
    event_id: str,
    reason: str,
) -> None:
    """Persist a charge.refunded event that cannot be linked to a ride.

    Without this the refund silently vanishes from the books — only a log
    warning was left behind. The ``stripe_orphan_refunds`` table lets finance
    reconcile or manually link these later.
    """
    charge_id = charge.get("id", "")
    refunded_cents = int(charge.get("amount_refunded", 0))
    currency = charge.get("currency", "cad")
    meta = charge.get("metadata") or {}

    try:
        await db_supabase.insert_one(
            "stripe_orphan_refunds",
            {
                "stripe_charge_id": charge_id,
                "payment_intent_id": payment_intent_id,
                "amount_refunded_cents": refunded_cents,
                "currency": currency,
                "reason": reason,
                "stripe_event_id": event_id,
                "raw_metadata": meta if meta else None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except DuplicateRecordError:
        # Already recorded by an earlier delivery of this same event (see the
        # unique index in migration 255). The row we wanted exists — that is
        # the desired end state, not a failure worth paging on.
        logger.info(
            "charge.refunded orphan already recorded for event %s — skipping duplicate",
            event_id,
            extra={"domain": "payments", "event_id": event_id},
        )
        return
    except Exception:
        logger.error(
            "Failed to persist orphan refund charge=%s pi=%s — refund data may be lost",
            charge_id,
            payment_intent_id,
            exc_info=True,
            extra={"domain": "payments", "event_id": event_id},
        )
        return

    logger.error(
        "charge.refunded orphan: charge=%s pi=%s reason=%s cents=%d — "
        "recorded in stripe_orphan_refunds for manual reconciliation",
        charge_id,
        payment_intent_id,
        reason,
        refunded_cents,
        extra={"domain": "payments", "event_id": event_id},
    )


def _extract_invoice_payment_intent(invoice: dict, stripe_secret: str = "") -> str | None:
    """Resolve the PaymentIntent id for a paid invoice across Stripe API versions.

    Under the pinned API version (2025-04-30.basil) the top-level
    ``invoice.payment_intent`` field is removed — the PI now lives under
    ``invoice.payments.data[].payment.payment_intent``. We MUST persist this id
    so later charge.refunded / dispute webhooks (which look rides up by
    payment_intent_id) can find the ride. Tries, in order:
      1. legacy top-level ``payment_intent`` (pre-Basil / expanded)
      2. the Basil ``payments.data[].payment.payment_intent`` shape
      3. a fresh ``Invoice.retrieve(expand=['payments'])`` (webhook payloads do
         not expand ``payments`` by default)
    Returns None only if all three fail (logged by the caller).
    """

    def _pi_id(val) -> str | None:
        if not val:
            return None
        return val if isinstance(val, str) else (val.get("id") if isinstance(val, dict) else None)

    def _from_payload(inv: dict) -> str | None:
        pi = _pi_id(inv.get("payment_intent"))
        if pi:
            return pi
        payments = inv.get("payments")
        data = payments.get("data") if isinstance(payments, dict) else None
        for entry in data or []:
            payment = (entry or {}).get("payment") or {}
            pi = _pi_id(payment.get("payment_intent"))
            if pi:
                return pi
        return None

    pi = _from_payload(invoice)
    if pi:
        return pi

    invoice_id = invoice.get("id")
    if not (invoice_id and stripe_secret):
        return None
    try:
        import stripe as _stripe

        refreshed = _stripe.Invoice.retrieve(invoice_id, expand=["payments"], api_key=stripe_secret)
        # stripe-python returns a typed object; normalize to a plain dict.
        as_dict = refreshed.to_dict_recursive() if hasattr(refreshed, "to_dict_recursive") else dict(refreshed)
        return _from_payload(as_dict)
    except Exception:
        logger.error(
            "invoice.paid: could not retrieve invoice %s to resolve payment_intent",
            invoice_id,
            exc_info=True,
            extra={"domain": "payments"},
        )
        return None


async def _handle_ride_invoice_paid(invoice: dict, ride_id: str, event_id: str, stripe_secret: str = "") -> None:
    """Settle a ride paid via an admin-sent payable Stripe Invoice.

    Mirrors settle_card's success bookkeeping (financial_events ledger row +
    payment_status='paid' + WS push + receipt) so the driver is credited the
    same way as an in-app charge. Idempotent: a ride already paid/waived is a
    no-op, so a duplicate or replayed invoice.paid never double-credits.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        # The rider's invoice payment succeeded but the ride row is unreadable
        # (transient DB failure, or a genuinely missing ride). Do NOT ack the
        # event — raise so processed_at stays NULL and the stuck-event
        # reconciliation path surfaces it (webhooks dispatch contract). Silently
        # returning here would take the rider's money while leaving the ride
        # unpaid and the driver uncredited.
        logger.error(
            "invoice.paid for unknown ride %s (invoice %s) — not acking; reconcile required",
            ride_id,
            invoice.get("id"),
            extra={"domain": "payments", "ride_id": ride_id, "event_id": event_id},
        )
        raise HTTPException(status_code=500, detail="Ride not found for paid invoice — Stripe will retry")
    _pstatus = ride.get("payment_status")
    # 'refunded' is terminal too: if charge.refunded marked the ride refunded
    # before a delayed invoice.paid lands, re-settling here would append another
    # ledger row and flip payment_status back to 'paid', erasing the refund.
    if _pstatus in ("paid", "waived_admin", "refunded"):
        logger.info(
            "invoice.paid: ride %s already settled (%s) — skipping",
            ride_id,
            _pstatus,
            extra={"domain": "payments", "ride_id": ride_id, "event_id": event_id},
        )
        return
    if _pstatus == "processing":
        # An in-app settlement holds the atomic 'processing' claim (or a card
        # charge was captured-but-unconfirmed). Settling the invoice here too
        # would double-credit the driver and double-write the ledger. Do NOT ack
        # the event: a bare return would stamp processed_at and permanently drop
        # this paid invoice (driver never credited) if the in-app charge later
        # fails. Raise so processed_at stays NULL — once the 'processing' claim
        # resolves (→ paid: idempotent skip; → failed: invoice settles) a replay
        # settles correctly. ERROR so the concurrent-charge anomaly surfaces (a
        # refund may be owed if the in-app charge also collected).
        logger.error(
            "invoice.paid for ride %s while payment_status='processing' — possible "
            "concurrent in-app charge; deferring invoice settlement, reconcile required",
            ride_id,
            extra={"domain": "payments", "ride_id": ride_id, "event_id": event_id},
        )
        raise HTTPException(status_code=500, detail="Ride payment is processing — deferring invoice settlement")

    rider_id = ride.get("rider_id")
    amount_cents = invoice.get("amount_paid")
    if amount_cents is None:
        amount_cents = invoice.get("amount_due") or 0
    payment_intent_id = _extract_invoice_payment_intent(invoice, stripe_secret)
    if not payment_intent_id:
        # No PI means later refund/dispute webhooks (keyed on payment_intent_id)
        # cannot find this ride. This is usually a transient Stripe-retrieve
        # failure in the expand fallback. Do NOT write a half-settled ride
        # (payment_status=paid with payment_intent_id=NULL); raise so the event
        # is not acked (processed_at stays NULL) and the retry/reconciliation
        # path re-resolves the PI. Matches the missing-ride contract above.
        logger.error(
            "invoice.paid: could not resolve payment_intent for ride %s (invoice %s) — not settling; Stripe will retry",
            ride_id,
            invoice.get("id"),
            extra={"domain": "payments", "ride_id": ride_id, "event_id": event_id},
        )
        raise HTTPException(status_code=500, detail="Could not resolve invoice payment_intent — Stripe will retry")
    tip_amount = Decimal(str(ride.get("tip_amount") or 0))

    try:
        from ..services.payment_service import _tip_ride_update, record_payment_event, send_ride_receipt
    except ImportError:
        from services.payment_service import _tip_ride_update, record_payment_event, send_ride_receipt  # type: ignore

    # Ledger first (recovery record exists even if the ride update fails).
    await record_payment_event(
        ride_id=ride_id,
        user_id=rider_id,
        amount_cents=int(amount_cents),
        payment_intent_id=payment_intent_id,
        ride=ride,
        tip_amount=tip_amount,
    )
    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "paid",
            "payment_intent_id": payment_intent_id,
            "stripe_invoice_id": invoice.get("id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            # Mirror settle_card: apply tip delta so driver_earnings reflects any
            # tip that was captured before the original card decline.
            **_tip_ride_update(ride, tip_amount),
        },
    )
    logger.info(
        "Ride %s settled via Stripe invoice %s",
        ride_id,
        invoice.get("id"),
        extra={"domain": "payments", "ride_id": ride_id, "event_id": event_id},
    )

    # Best-effort: nudge the rider's app out of the stuck payment screen.
    if rider_id:
        try:
            from ..socket_manager import manager as _manager
        except ImportError:
            from socket_manager import manager as _manager  # type: ignore
        try:
            await _manager.send_personal_message(
                {
                    "type": "payment_completed",
                    "ride_id": ride_id,
                    "charged_amount": str(cents_to_dollars(int(amount_cents))),
                },
                f"rider_{rider_id}",
            )
        except Exception:
            logger.warning("invoice.paid: WS payment_completed push failed", exc_info=True)
        # Re-fetch so the receipt reflects the just-written paid status / PI /
        # invoice id rather than the pre-update snapshot. The receipt is the
        # rider's tax document — a send failure is logged at ERROR (it may hide
        # a broken email pipeline), not swallowed at warning.
        updated_ride = await db_supabase.get_ride(ride_id) or ride
        try:
            await send_ride_receipt(updated_ride, rider_id, tip_amount)
        except Exception:
            logger.error("invoice.paid: receipt email failed for ride %s", ride_id, exc_info=True)


def _invoice_period_end_iso(invoice: dict) -> str | None:
    """Extract the billing-period end (ISO UTC) from a Stripe Invoice.

    Stripe's invoice line carries the authoritative period end for the cycle
    just paid. Returns None if the structure is missing so the caller can fall
    back to plan duration. Defensive on every nested path — Stripe trims
    fields and StripeObjects flatten to plain dicts via to_dict_recursive.
    """
    try:
        lines = (invoice.get("lines") or {}).get("data") or []
        if lines:
            period = lines[0].get("period") or {}
            end = period.get("end")
            if end:
                return datetime.fromtimestamp(int(end), tz=timezone.utc).isoformat()
    except Exception:  # noqa: S110 — best-effort period parse, fall through to None
        pass
    return None


def _invoice_period_start_iso(invoice: dict) -> str | None:
    """Extract the billing-period start (ISO UTC) from a Stripe Invoice.

    Mirror of ``_invoice_period_end_iso``. Used to re-anchor a renewed pass's
    ``started_at`` to the new cycle so the row's lifetime
    (``started_at``..``expires_at``) stays ~one period — keeping a recurring
    1-day pass classified as a 24h "hourly" pass instead of silently widening
    into a multi-day calendar-day window after the first renewal.
    """
    try:
        lines = (invoice.get("lines") or {}).get("data") or []
        if lines:
            period = lines[0].get("period") or {}
            start = period.get("start")
            if start:
                return datetime.fromtimestamp(int(start), tz=timezone.utc).isoformat()
    except Exception:
        return None
    return None


def _event_to_plain_dict(event):
    """Normalize a verified Stripe webhook Event to a plain, recursively-plain dict.

    stripe-python v15 Events are NOT dict subclasses and lack ``.get()`` /
    ``.to_dict_recursive()`` — calling either AttributeError'd and 500'd every
    webhook. ``_to_dict_recursive()`` yields plain nested dicts on v15. Test
    fixtures and legacy StripeObjects are already dicts. Last resort: a JSON
    round-trip via ``str()`` (a StripeObject's ``__str__`` is JSON).
    """
    if isinstance(event, dict):
        return event
    # Only the v15 Event needs conversion. Detect it precisely: accessing ``.get``
    # on a v15 Event raises AttributeError (via __getattr__), whereas a dict-like
    # object or a configured test mock exposes a usable ``.get`` — leave those
    # untouched so we don't transform an object that already works.
    try:
        getter = event.get
    except AttributeError:
        getter = None
    if callable(getter):
        return event
    fn = getattr(event, "_to_dict_recursive", None) or getattr(event, "to_dict_recursive", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: S110  # pragma: no cover — fall through to JSON
            pass
    try:
        return json.loads(str(event))
    except Exception:  # pragma: no cover — last-ditch shallow dict
        return dict(event)


async def _sync_corporate_subscription_event(event_type: str, data_object: dict, corp_sub: dict, event_id: str) -> None:
    """Mirror a corporate flat-SaaS subscription's Stripe state into
    corporate_subscriptions. Stripe owns the recurring-charge schedule
    (services/corporate_subscription_service.py only ever creates/cancels
    the Subscription object); this is the read-model sync, the corporate
    equivalent of the driver_subscriptions handling elsewhere in this file
    — deliberately a separate table and a separate code path, zero shared
    state or logic with Spinr Pass.
    """
    row_id = corp_sub["id"]
    terminal = corp_sub.get("status") == "cancelled"

    if event_type == "customer.subscription.deleted":
        if not terminal:
            await db_supabase.update_corporate_subscription(
                row_id,
                {
                    "status": "cancelled",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                    "cancel_at_period_end": False,
                },
            )
        logger.info(
            "Corporate subscription cancelled via Stripe: row=%s sub=%s",
            row_id,
            corp_sub.get("stripe_subscription_id"),
            extra={"domain": "corporate", "event_id": event_id},
        )
        return

    # A late/duplicate event arriving after a terminal cancel must never
    # resurrect the row — same guard the driver_subscriptions invoice.paid
    # handler applies for the same reason.
    if terminal:
        logger.warning(
            "Corporate subscription event %s ignored for cancelled row=%s",
            event_type,
            row_id,
            extra={"domain": "corporate", "event_id": event_id},
        )
        return

    if event_type == "customer.subscription.updated":
        stripe_status = data_object.get("status")
        patch: dict = {
            "cancel_at_period_end": bool(data_object.get("cancel_at_period_end")),
        }
        period_end = data_object.get("current_period_end")
        if period_end:
            patch["current_period_end"] = datetime.fromtimestamp(int(period_end), tz=timezone.utc).isoformat()
        if stripe_status in ("active", "trialing"):
            patch["status"] = "active"
        elif stripe_status == "past_due":
            patch["status"] = "past_due"
        elif stripe_status in ("canceled", "unpaid", "incomplete_expired"):
            patch["status"] = "cancelled"
            patch["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_corporate_subscription(row_id, patch)
        logger.info(
            "Corporate subscription updated: row=%s stripe_status=%s -> status=%s",
            row_id,
            stripe_status,
            patch.get("status", corp_sub.get("status")),
            extra={"domain": "corporate", "event_id": event_id},
        )

    elif event_type == "invoice.paid":
        patch = {"status": "active"}
        new_period_end = _invoice_period_end_iso(data_object)
        if new_period_end:
            patch["current_period_end"] = new_period_end
        await db_supabase.update_corporate_subscription(row_id, patch)
        logger.info(
            "Corporate subscription renewed: row=%s sub=%s until=%s",
            row_id,
            corp_sub.get("stripe_subscription_id"),
            new_period_end,
            extra={"domain": "corporate", "event_id": event_id},
        )

    elif event_type == "invoice.payment_failed":
        await db_supabase.update_corporate_subscription(row_id, {"status": "past_due"})
        logger.warning(
            "Corporate subscription payment failed: row=%s sub=%s — flagged past_due, Stripe dunning in progress",
            row_id,
            corp_sub.get("stripe_subscription_id"),
            extra={"domain": "corporate", "event_id": event_id},
        )


@api_router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for server-side payment confirmation."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    settings = await get_app_settings()
    webhook_secret = settings.get("stripe_webhook_secret", "")
    connect_webhook_secret = settings.get("stripe_connect_webhook_secret", "")
    stripe_secret = settings.get("stripe_secret_key", "")

    if not webhook_secret:
        logger.error("stripe_webhook_secret not set — rejecting unverified webhook")
        raise HTTPException(
            status_code=500,
            detail="Webhook signature verification not configured",
        )

    if not stripe_secret:
        logger.error("Stripe secret key not configured in app settings")
        raise HTTPException(status_code=500, detail="Stripe not configured")

    import stripe

    # Both the platform endpoint and the Connected-accounts endpoint POST to
    # this same URL, but Stripe signs each with that endpoint's own whsec_.
    # We can't tell them apart before verifying, so try the platform secret
    # first, then the connect secret (if configured). A SignatureVerification
    # failure on one is expected for events from the other — only reject when
    # BOTH fail. ValueError = malformed payload, reject immediately.
    candidate_secrets = [webhook_secret]
    if connect_webhook_secret:
        candidate_secrets.append(connect_webhook_secret)

    event = None
    last_sig_error: Exception | None = None
    for secret in candidate_secrets:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload") from None
        except stripe.error.SignatureVerificationError as e:
            last_sig_error = e
            continue
        except Exception as e:
            last_sig_error = e
            continue

    if event is None:
        logger.error(f"Stripe webhook signature verification failed: {last_sig_error}")
        raise HTTPException(status_code=400, detail="Invalid signature") from last_sig_error

    # stripe-python v15: the returned Event is NOT a dict subclass — it has no
    # ``.get()`` or ``.to_dict_recursive()`` (both raise AttributeError via
    # __getattr__, which 500'd every webhook). Normalize it to a plain,
    # recursively-plain dict for all field access + jsonb storage.
    event = _event_to_plain_dict(event)

    event_id = event.get("id", "")
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    if not event_id:
        # Should never happen for real Stripe events, but guard anyway —
        # we cannot dedup without a stable key.
        logger.error("Stripe webhook event missing id — cannot dedup")
        raise HTTPException(status_code=400, detail="Missing event id")

    # ── Idempotency gate ─────────────────────────────────────────────
    # Stripe retries every event (network blip, >20s handler, any non-2xx)
    # so we MUST treat a replay of the same event.id as a no-op. The
    # stripe_events table (migration 22) has event_id as PRIMARY KEY;
    # claim_stripe_event returns False on a unique-violation replay. event is
    # already a plain (json.loads) dict, safe to store directly in jsonb.
    event_payload = event

    try:
        is_new = await claim_stripe_event(event_id, event_type, event_payload)
    except Exception as e:
        # Surface the REAL cause: for DatabaseError, str(e) is only
        # "Database operation failed" — the underlying Postgres error (e.g.
        # "relation stripe_events does not exist") lives in details["original"].
        # Without this the webhook just logs a generic message and the root cause
        # (missing table / RLS / schema drift) stays invisible.
        _orig = e.details.get("original") if isinstance(e, DatabaseError) else None
        logger.error(
            "Failed to persist stripe event %s (type=%s): %s | original=%s",
            event_id,
            event_type,
            e,
            _orig,
            exc_info=True,
        )
        # Let Stripe retry — 5xx keeps the event in their queue.
        raise HTTPException(status_code=500, detail="Event persistence failed") from e

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": event_id}

    # ── Corporate flat-SaaS subscription events ─────────────────────
    # Checked first (cheap lookup) and, if matched, handled entirely by
    # _sync_corporate_subscription_event and returned immediately — the
    # driver-specific dispatch below assumes a driver_subscriptions row for
    # these same event types and must never run against a corporate one.
    if event_type in (
        "customer.subscription.deleted",
        "customer.subscription.updated",
        "invoice.paid",
        "invoice.payment_failed",
    ):
        _corp_stripe_sub_id = (
            data_object.get("subscription") if event_type.startswith("invoice.") else data_object.get("id")
        )
        corp_sub = (
            await db_supabase.get_corporate_subscription_by_stripe_id(_corp_stripe_sub_id)
            if _corp_stripe_sub_id
            else None
        )
        if corp_sub:
            await _sync_corporate_subscription_event(event_type, data_object, corp_sub, event_id)
            await mark_stripe_event_processed(event_id)
            return {"received": True, "scope": "corporate_subscription", "event_id": event_id}

    # ── Dispatch ─────────────────────────────────────────────────────
    # Any exception raised below propagates as 5xx, leaving processed_at
    # NULL so Stripe retries. If Stripe's own retry window is exhausted
    # before this succeeds, utils/stripe_reconcile.py's daily sweep
    # (_reconcile_stuck_stripe_events, ACTION_ITEMS.md C10) will surface
    # the row for manual review -- it does not auto-replay the payload
    # (see that function's docstring for why).
    if event_type == "payment_intent.succeeded":
        meta = data_object.get("metadata") or {}

        if meta.get("scope") == "corporate_topup":
            try:
                from ..services.corporate_wallet_service import apply_topup  # type: ignore
            except ImportError:
                from services.corporate_wallet_service import apply_topup  # type: ignore

            amount_cents = data_object.get("amount_received") or data_object.get("amount", 0)
            # Decimal-safe cents→dollars (2-dp HALF_UP). Float division
            # ``cents / 100`` drifts for arbitrary cent values; quantize
            # first, then hand the float to apply_topup (Postgres NUMERIC
            # rounds to column scale).
            amount_cad = cents_to_dollars(amount_cents)
            await apply_topup(
                wallet_id=meta["wallet_id"],
                amount=amount_cad,
                stripe_payment_intent_id=data_object["id"],
                actor_user_id=meta.get("initiated_by"),
                notes=f"Stripe top-up via {event_id}",
            )
            await mark_stripe_event_processed(event_id)
            return {"received": True, "scope": "corporate_topup", "event_id": event_id}

        if meta.get("scope") == "wallet_topup":
            try:
                from ..db_supabase import wallet_apply_credit  # type: ignore
            except ImportError:
                from db_supabase import wallet_apply_credit  # type: ignore

            wallet_id = meta.get("wallet_id")
            user_id = meta.get("user_id")
            amount_cad_str = meta.get("amount_cad", "0")
            amount = Decimal(amount_cad_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # C6: credit the balance AND write the ledger row atomically and
            # idempotently, keyed on the Stripe payment_intent id. The old path
            # incremented the balance and inserted the ledger row separately with
            # no dedup, so a crash before mark_stripe_event_processed (below) plus
            # a Stripe retry double-credited the wallet. wallet_apply_credit dedups
            # on (wallet_id, reference_id, type) inside the wallet row lock, so the
            # retry is a no-op that returns the original balance.
            credit = await wallet_apply_credit(
                wallet_id=wallet_id,
                user_id=user_id,
                type_="top_up",
                amount=amount,
                reference_id=data_object["id"],
                description=f"Wallet top-up ${amount}",
                metadata={"stripe_payment_intent_id": data_object["id"]},
            )
            new_balance = credit.get("balance_after")

            logger.info(
                f"Wallet top-up confirmed: wallet={wallet_id} amount={amount} "
                f"new_balance={new_balance} deduped={credit.get('deduped')}",
                extra={"domain": "payments", "event_id": event_id},
            )
            await mark_stripe_event_processed(event_id)

            if user_id:
                try:
                    await send_push_notification(
                        user_id,
                        title="Wallet Topped Up",
                        body=f"${amount} has been added to your wallet.",
                        data={"type": "wallet_topup", "amount": str(amount)},
                    )
                except Exception:
                    logger.warning("Push notification failed for wallet_topup", exc_info=True)
                # Receipt for money the rider just moved into Spinr.
                # Gated on the credit NOT being a dedup hit: wallet_apply_credit
                # is idempotent on the payment_intent, so a replay returns the
                # original balance without crediting again — mailing a second
                # receipt for it would claim a top-up that did not happen.
                if not credit.get("deduped"):
                    await send_wallet_topup_email(user_id, amount, new_balance)

            return {"received": True, "scope": "wallet_topup", "event_id": event_id}

        ride_id = meta.get("ride_id")
        user_id = meta.get("user_id")
        payment_intent_id = data_object.get("id")

        if ride_id:
            # SECURITY: mirror the /payments/confirm underpay guard. The
            # metadata binds the PI to this ride, but a partially-captured
            # or cheaper intent must not settle it — verify amount_received
            # covers the authoritative owed total before marking paid.
            ride = await db_supabase.get_ride(ride_id)
            if ride is None:
                logger.error(
                    f"Webhook payment_intent.succeeded: ride {ride_id} not found — "
                    f"payment {payment_intent_id} unlinked",
                    extra={"domain": "payments", "event_id": event_id, "ride_id": ride_id},
                )
                # A raw 5xx would NOT get this event re-run: claim_stripe_event
                # dedupes the retry delivery even with processed_at NULL. Release
                # the claim first (no side effects have happened for this event)
                # so Stripe's retry genuinely re-processes once the ride row is
                # visible / the DB blip has passed.
                if not await unclaim_stripe_event(event_id):
                    # Claim NOT released — Stripe's retry will be deduped and
                    # this payment stays unlinked until an operator replays the
                    # event. CRITICAL so the reconciliation alert fires.
                    logger.critical(
                        "Stripe event %s could not be unclaimed after ride lookup failure — "
                        "retry path NOT restored; manual replay required for payment %s",
                        event_id,
                        payment_intent_id,
                    )
                raise HTTPException(status_code=500, detail="Ride lookup failed — Stripe will retry")

            # Owed = grand_total + stored tip (fallback total_fare + tip) —
            # the same authoritative captured amount settlement and the nightly
            # reconciler use. Checking grand_total alone would let a fare-only
            # capture settle a ride that also has a persisted driver tip.
            owed = ride.get("grand_total")
            if owed is None:
                owed = ride.get("total_fare", 0)
            owed_d = Decimal(str(owed or 0)) + Decimal(str(ride.get("tip_amount") or 0))
            owed_cents = int((owed_d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP) * 100).to_integral_value())
            received_cents = int(data_object.get("amount_received") or 0)
            if received_cents < owed_cents:
                # Permanent condition — retrying won't change the amounts.
                # Leave the ride unpaid (payment_retry / reconciliation own it)
                # but mark the EVENT processed: the refusal IS this event's
                # outcome, and an unstamped row would raise a false CRITICAL
                # "STUCK" alert on every subsequent delivery of this event_id.
                logger.error(
                    "[webhook][security] underpay ride=%s pi=%s received=%d owed=%d — refusing to mark paid",
                    ride_id,
                    payment_intent_id,
                    received_cents,
                    owed_cents,
                    extra={"domain": "payments", "event_id": event_id, "ride_id": ride_id},
                )
                await mark_stripe_event_processed(event_id)
                return {"received": True, "underpaid": True, "event_id": event_id}

            # C1: PaymentSheet / Google-Pay rides settle ONLY via this webhook —
            # process_payment (settle_card) never runs — so they must get the
            # SAME GST receipt + financial_events ledger row + driver tip credit
            # here, mirroring _handle_ride_invoice_paid. Idempotent: do the
            # bookkeeping only when the ride was NOT already settled in-app
            # (payment_status paid/waived_admin/processing), so a process_payment
            # ride that also emits this webhook isn't double-written.
            _already_settled = ride.get("payment_status") in ("paid", "waived_admin", "processing")
            _tip = Decimal(str(ride.get("tip_amount") or 0))
            try:
                from ..services.payment_service import (
                    _tip_ride_update,
                    record_payment_event,
                    send_ride_receipt,
                )
            except ImportError:
                from services.payment_service import (  # type: ignore
                    _tip_ride_update,
                    record_payment_event,
                    send_ride_receipt,
                )

            _paid_fields: dict = {
                "payment_status": "paid",
                "payment_intent_id": payment_intent_id,
                "paid_at": datetime.now(timezone.utc).isoformat(),
            }
            if not _already_settled:
                # Credit the stored tip to the driver exactly once — the delta
                # model is idempotent with the /rate path that may also apply it.
                _paid_fields.update(_tip_ride_update(ride, _tip))

            updated = await db_supabase.update_ride(ride_id, _paid_fields)
            if updated is None:
                logger.error(
                    f"Webhook payment_intent.succeeded: ride {ride_id} not found or 0 rows "
                    f"updated — payment {payment_intent_id} unlinked",
                    extra={
                        "domain": "payments",
                        "event_id": event_id,
                        "ride_id": ride_id,
                    },
                )
                raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")

            logger.info(f"Payment confirmed via webhook for ride {ride_id}")
            if not _already_settled:
                await record_payment_event(
                    ride_id=ride_id,
                    user_id=ride.get("rider_id") or user_id or "",
                    amount_cents=received_cents,
                    payment_intent_id=payment_intent_id,
                    ride=ride,
                    tip_amount=_tip,
                )
                _rcpt_rider = ride.get("rider_id")
                if _rcpt_rider:
                    try:
                        await send_ride_receipt(
                            dict(updated) if isinstance(updated, dict) else dict(ride),
                            _rcpt_rider,
                            _tip,
                        )
                    except Exception:
                        logger.error(
                            "[webhook] GST receipt send failed for ride %s (payment recorded)",
                            ride_id,
                            exc_info=True,
                        )

        if user_id:
            # Wrap push notification so a Firebase outage does not cause Stripe
            # to retry the webhook for days (which would re-process the payment event).
            try:
                await send_push_notification(
                    user_id,
                    "Payment Confirmed ✅",
                    "Your payment has been processed successfully.",
                    {"type": "payment_confirmed", "ride_id": ride_id or ""},
                )
            except Exception as _push_err:
                logger.error(f"Webhook: push notification failed for user {user_id}: {_push_err}")

    elif event_type == "payment_intent.payment_failed":
        ride_id = data_object.get("metadata", {}).get("ride_id")
        user_id = data_object.get("metadata", {}).get("user_id")
        payment_intent_id = data_object.get("id")
        failure_message = data_object.get("last_payment_error", {}).get("message", "Payment failed")

        if ride_id:
            updated = await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "failed",
                    "payment_intent_id": payment_intent_id,
                    "payment_failure_reason": failure_message,
                },
            )
            if updated is None:
                logger.error(
                    f"Webhook payment_intent.payment_failed: ride {ride_id} not found or 0 rows "
                    f"updated — payment failure {payment_intent_id} unlinked",
                    extra={
                        "domain": "payments",
                        "event_id": event_id,
                        "ride_id": ride_id,
                    },
                )
                raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")
            logger.warning(f"Payment failed for ride {ride_id}: {failure_message}")

        if user_id:
            try:
                await send_push_notification(
                    user_id,
                    "Payment Failed ❌",
                    f"Your payment could not be processed: {failure_message}",
                    {"type": "payment_failed", "ride_id": ride_id or ""},
                )
            except Exception as _push_err:
                logger.error(f"Webhook: push notification failed for user {user_id}: {_push_err}")

        # Notify the driver so they know the rider's payment failed (13-10)
        if ride_id:
            try:
                ride_row = await db_supabase.get_ride(ride_id)
                driver_user_id = None
                if ride_row:
                    driver_id = ride_row.get("driver_id")
                    if driver_id:
                        driver_rows = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
                        if driver_rows:
                            driver_user_id = driver_rows[0].get("user_id")
            except Exception as lookup_err:
                logger.error(f"Driver payment-failed lookup error: {lookup_err}", exc_info=True)
                driver_user_id = None
            if driver_user_id:
                try:
                    await send_push_notification(
                        driver_user_id,
                        "Rider payment failed",
                        "The payment for your last ride could not be collected.",
                        {
                            "type": "payment_failed",
                            "ride_id": ride_id,
                            "deeplink": "/driver/earnings",
                        },
                    )
                except Exception:
                    logger.warning(
                        "Push notification failed for payment_failed event; continuing",
                        exc_info=True,
                    )

    elif event_type == "checkout.session.completed":
        # ── Spinr Pass subscription payment confirmed ──────────
        # The /drivers/subscription/subscribe endpoint creates a pending
        # subscription row and a Stripe Checkout Session with the
        # subscription_id in the metadata. This webhook fires after the
        # driver completes payment — we activate the subscription here.
        metadata = data_object.get("metadata", {})
        subscription_id = metadata.get("subscription_id")
        plan_id = metadata.get("plan_id")
        driver_id = metadata.get("driver_id")

        if subscription_id and data_object.get("payment_status") == "paid":
            try:
                from ..routes.drivers import subscriptions as _drv_subs  # type: ignore
            except ImportError:
                from routes.drivers import subscriptions as _drv_subs  # type: ignore

            # Pass the session's actual mode so one-off vs recurring ledger
            # recording is decided by what was created, not the plan's current
            # stripe_price_id.
            await _drv_subs._activate_subscription(subscription_id, plan_id, data_object.get("mode"))

            # Re-read: _activate_subscription is a no-op for a row that was
            # superseded by a newer checkout (or otherwise non-pending). Only
            # link the Stripe Subscription id when the row actually activated —
            # linking a superseded row would let a later invoice.paid /
            # customer.subscription.updated flip it back to active.
            row = await db_supabase.find_one("driver_subscriptions", {"id": subscription_id})
            stripe_subscription_id = data_object.get("subscription")

            if row and row.get("status") == "active":
                if stripe_subscription_id:
                    await db_supabase.update_one(
                        "driver_subscriptions",
                        {"id": subscription_id},
                        {"stripe_subscription_id": stripe_subscription_id},
                    )
                logger.info(
                    f"[WEBHOOK] Spinr Pass activated via checkout.session.completed: "
                    f"subscription={subscription_id} driver={driver_id} plan={plan_id} "
                    f"stripe_sub={stripe_subscription_id}"
                )
            else:
                # Stale session paid after being superseded — don't activate or
                # link. Cancel the orphaned Stripe subscription so the driver
                # isn't billed for a plan they already replaced.
                if stripe_subscription_id:
                    try:
                        from ..routes.drivers import subscriptions as _drv_subs  # type: ignore
                    except ImportError:
                        from routes.drivers import subscriptions as _drv_subs  # type: ignore
                    await _drv_subs._cancel_stripe_subscription(stripe_subscription_id)
                logger.warning(
                    "[WEBHOOK] checkout.session.completed for non-active row %s "
                    "(superseded?) — not linking; cancelled orphan Stripe sub %s",
                    subscription_id,
                    stripe_subscription_id,
                    extra={"domain": "drivers", "event_id": event_id},
                )
        else:
            logger.info(
                f"[WEBHOOK] checkout.session.completed but payment not yet paid: "
                f"status={data_object.get('payment_status')} subscription={subscription_id}"
            )

    elif event_type == "charge.refunded":
        charge = data_object
        payment_intent_id = charge.get("payment_intent")
        if payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride = rides[0]
                ride_id = ride["id"]
                refunded_cents = int(charge.get("amount_refunded", 0))
                refunded_amount = Decimal(str(refunded_cents)) / Decimal("100")
                refunded_amount = refunded_amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                await db_supabase.update_one(
                    "rides",
                    {"id": ride_id},
                    {
                        "payment_status": "refunded",
                        "refund_amount": str(refunded_amount),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                # C3: write a compensating ledger row so the 7-year tax/audit
                # ledger nets the refund out (it previously recorded none). Per
                # policy the driver KEEPS their pay — driver_earnings is NOT
                # clawed back — but the reversed rider-side GST/PST is captured
                # here for remittance. Idempotent-ish: a duplicate charge.refunded
                # delivery is deduped upstream by claim_stripe_event(event_id).
                try:
                    from ..services.payment_service import record_refund_event
                except ImportError:
                    from services.payment_service import record_refund_event  # type: ignore
                await record_refund_event(
                    ride_id=ride_id,
                    user_id=ride.get("rider_id") or "",
                    refund_cents=refunded_cents,
                    payment_intent_id=payment_intent_id,
                    ride=ride,
                )
                logger.info(
                    f"Stripe refund: ride {ride_id} marked refunded (${refunded_amount:.2f}); "
                    f"driver pay retained, ledger + GST reversal recorded",
                    extra={
                        "domain": "payments",
                        "ride_id": ride_id,
                        "event_id": event_id,
                    },
                )
                rider_id = ride.get("rider_id")
                if rider_id:
                    try:
                        await send_push_notification(
                            rider_id,
                            "Refund processed",
                            f"Your refund of ${refunded_amount:.2f} has been processed by your bank.",
                            data={"type": "refund_processed", "ride_id": ride_id},
                        )
                    except Exception as _e:
                        logger.debug(f"Refund push failed: {_e}")
                    # A refund is a financial record; a push that scrolls out of
                    # the tray is not one. Safe against a duplicate Stripe
                    # delivery because claim_stripe_event(event_id) already
                    # deduped this whole handler upstream. Self-swallowing.
                    await send_refund_email(rider_id, refunded_amount, ride=ride)
            else:
                await _record_orphan_refund(
                    charge=charge,
                    payment_intent_id=payment_intent_id,
                    event_id=event_id,
                    reason="no_ride_for_pi",
                )
        else:
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id=None,
                event_id=event_id,
                reason="no_payment_intent",
            )

    elif event_type == "charge.dispute.created":
        dispute_id_stripe = data_object.get("id", "")
        payment_intent_id = data_object.get("payment_intent") or ""
        dispute_amount_cents = data_object.get("amount", 0)
        dispute_reason = data_object.get("reason", "unknown")
        dispute_status = data_object.get("status", "")

        ride = None
        ride_id = None
        if payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride = rides[0]
                ride_id = ride["id"]

        await db_supabase.insert_one(
            "stripe_disputes",
            {
                "id": str(__import__("uuid").uuid4()),
                "stripe_dispute_id": dispute_id_stripe,
                "payment_intent_id": payment_intent_id,
                "ride_id": ride_id,
                "amount_cents": dispute_amount_cents,
                "reason": dispute_reason,
                "status": dispute_status,
                "stripe_event_id": event_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        if ride_id:
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "payment_status": "disputed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        try:
            try:
                from ..socket_manager import manager  # type: ignore
            except ImportError:
                from socket_manager import manager  # type: ignore

            await manager.broadcast_to_admins(
                {
                    "type": "charge_dispute_created",
                    "ride_id": ride_id,
                    "dispute_reason": dispute_reason,
                    "amount_cents": dispute_amount_cents,
                    "payment_intent_id": payment_intent_id,
                }
            )
        except Exception as ws_err:
            logger.warning("Dispute WS broadcast failed: %s", ws_err)

        logger.error(
            "CHARGEBACK: dispute opened reason=%s amount_cents=%d ride=%s pi=%s",
            dispute_reason,
            dispute_amount_cents,
            ride_id,
            payment_intent_id,
            extra={
                "domain": "payments",
                "event_id": event_id,
                "ride_id": ride_id or "",
            },
        )

    elif event_type == "charge.dispute.updated":
        # B27: intermediate status transitions (needs_response → under_review,
        # a new evidence deadline, etc.) previously landed nowhere — the type
        # wasn't in _STRIPE_HANDLED_EVENTS, so Stripe's own status trail was
        # invisible between `created` and `closed`. Status-mirror only; the
        # money-moving/ride-status logic lives in `closed`.
        dispute_id_stripe = data_object.get("id", "")
        dispute_status = data_object.get("status", "")
        if dispute_id_stripe:
            existing = await db_supabase.find_one(
                "stripe_disputes",
                {"stripe_dispute_id": dispute_id_stripe},
            )
            if existing:
                await db_supabase.update_one(
                    "stripe_disputes",
                    {"id": existing["id"]},
                    {
                        "status": dispute_status,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
        logger.info(
            "Dispute updated: status=%s dispute=%s",
            dispute_status,
            dispute_id_stripe,
            extra={"domain": "payments", "event_id": event_id},
        )

    elif event_type == "charge.dispute.closed":
        dispute_id_stripe = data_object.get("id", "")
        payment_intent_id = data_object.get("payment_intent") or ""
        dispute_status = data_object.get("status", "")

        # B27: key on stripe_dispute_id, the table's own unique index —
        # payment_intent_id is absent ("") on some events and non-unique, so
        # looking up by it could match (and overwrite) an unrelated dispute's
        # row when either PI is missing or a PI has more than one dispute.
        existing = None
        if dispute_id_stripe:
            existing = await db_supabase.find_one(
                "stripe_disputes",
                {"stripe_dispute_id": dispute_id_stripe},
            )
        if existing:
            await db_supabase.update_one(
                "stripe_disputes",
                {"id": existing["id"]},
                {
                    "status": dispute_status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        ride_id = existing.get("ride_id") if existing else None
        rider_id = None
        if not ride_id and payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride_id = rides[0]["id"]
                rider_id = rides[0].get("rider_id")

        if ride_id:
            # B27: `charge.dispute.closed` fires for `won`, `lost`, AND
            # `warning_closed` (an early-fraud-warning/inquiry that resolved
            # without becoming a real chargeback). Only an actual loss should
            # ever mark the ride `dispute_lost` — `warning_closed` means the
            # charge stands, same as `won`. Never reuse `dispute_lost` for a
            # non-loss outcome.
            new_payment_status = "dispute_lost" if dispute_status == "lost" else "paid"
            if rider_id is None:
                ride_rows = await db_supabase.get_rows("rides", {"id": ride_id}, limit=1)
                if ride_rows:
                    rider_id = ride_rows[0].get("rider_id")
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "payment_status": new_payment_status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        # B27: record the balance-transaction debit(s)/fee Stripe posts on
        # close so docs/runbooks/stripe-reconciliation.md doesn't show an
        # unexplained delta for every chargeback. Guarded on a resolved
        # rider_id -- financial_events.user_id is NOT NULL REFERENCES
        # users(id), so a dispute whose ride/rider can't be resolved has
        # nowhere safe to attribute the ledger row (record_dispute_close_events
        # would itself skip+log this, but skipping the call/import entirely
        # here avoids doing the balance_transactions work for nothing).
        balance_transactions = data_object.get("balance_transactions") or []
        if balance_transactions and rider_id:
            try:
                from ..services.payment_service import record_dispute_close_events
            except ImportError:
                from services.payment_service import record_dispute_close_events  # type: ignore
            await record_dispute_close_events(
                dispute_id=dispute_id_stripe,
                user_id=rider_id,
                ride_id=ride_id,
                balance_transactions=balance_transactions,
                dispute_status=dispute_status,
            )

        logger.info(
            "Dispute closed: status=%s ride=%s pi=%s dispute=%s",
            dispute_status,
            ride_id,
            payment_intent_id,
            dispute_id_stripe,
            extra={"domain": "payments", "event_id": event_id},
        )

    elif event_type == "customer.subscription.deleted":
        subscription = data_object
        stripe_sub_id = subscription.get("id")
        stripe_customer_id = subscription.get("customer")

        # Primary: match our row by the Stripe Subscription id, which we persist
        # for recurring plans. Checkout-created subs may not have
        # users.stripe_customer_id populated, so the customer lookup below can
        # miss them — without this match the row would stay active and the
        # driver would keep subscription-gated access after Stripe stopped.
        active_sub = None
        if stripe_sub_id:
            active_sub = await db_supabase.find_one("driver_subscriptions", {"stripe_subscription_id": stripe_sub_id})

        # Fallback: legacy customer-based lookup — only for rows that were never
        # linked to a Stripe subscription. A live active row carrying a DIFFERENT
        # stripe_subscription_id is a newer pass (e.g. after a plan switch on the
        # same customer); deleting this older sub must not cancel it.
        if not active_sub and stripe_customer_id:
            user_row = await db_supabase.find_one("users", {"stripe_customer_id": stripe_customer_id})
            if user_row:
                driver_row = await db_supabase.find_one("drivers", {"user_id": user_row["id"]})
                if driver_row:
                    candidate = await db_supabase.find_one(
                        "driver_subscriptions",
                        {"driver_id": driver_row["id"], "status": "active"},
                    )
                    if candidate and not candidate.get("stripe_subscription_id"):
                        active_sub = candidate
                    elif candidate:
                        logger.warning(
                            "customer.subscription.deleted: active row %s is linked to a different "
                            "stripe_sub (%s != %s) — not cancelling the newer pass",
                            candidate["id"],
                            candidate.get("stripe_subscription_id"),
                            stripe_sub_id,
                            extra={"domain": "drivers", "event_id": event_id},
                        )

        if not active_sub:
            logger.warning(
                "customer.subscription.deleted: no matching subscription row (sub=%s customer=%s)",
                stripe_sub_id,
                stripe_customer_id,
                extra={"domain": "drivers", "event_id": event_id},
            )
        else:
            if active_sub.get("status") != "cancelled":
                await db_supabase.update_one(
                    "driver_subscriptions",
                    {"id": active_sub["id"]},
                    {
                        "status": "cancelled",
                        "cancelled_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            logger.info(
                "Stripe subscription cancelled: row=%s sub=%s",
                active_sub["id"],
                stripe_sub_id,
                extra={
                    "domain": "drivers",
                    "driver_id": active_sub.get("driver_id") or "",
                    "event_id": event_id,
                },
            )
            driver_row = await db_supabase.find_one("drivers", {"id": active_sub.get("driver_id")})
            if driver_row and driver_row.get("user_id"):
                try:
                    await send_push_notification(
                        driver_row["user_id"],
                        "Subscription cancelled",
                        "Your Spinr subscription has been cancelled. Renew to continue accepting rides.",
                        data={
                            "type": "subscription_cancelled",
                            "deeplink": "/driver/subscription",
                        },
                    )
                except Exception as _e:
                    logger.debug(f"Subscription cancel push failed: {_e}")

    elif event_type == "account.updated":
        # Stripe Connect Express KYC mirror. Fires whenever the driver
        # progresses through Stripe-hosted onboarding (uploads SIN,
        # accepts ToS, links bank, etc.) or whenever Stripe's verification
        # team flips a status. We persist only the cache columns added
        # by migration 92 — never the SIN itself.
        try:
            from ..services.stripe_kyc_sync import apply_account_update
        except ImportError:
            from services.stripe_kyc_sync import apply_account_update
        await apply_account_update(data_object, event_id=event_id)

    elif event_type == "invoice.paid":
        # Recurring Spinr Pass renewal succeeded (also fires for the first
        # invoice of a new subscription). Extend the driver's row to the
        # invoice's period end — idempotent for both subscription_create and
        # subscription_cycle.
        invoice = data_object
        _ride_invoice_id = (invoice.get("metadata") or {}).get("ride_id")
        stripe_sub_id = invoice.get("subscription")
        if not _ride_invoice_id and not stripe_sub_id:
            # A ride invoice whose metadata.ride_id was stripped/lost still settles:
            # recover the ride by the persisted stripe_invoice_id (migration 177
            # indexes it) before treating this as a non-ride invoice and dropping it.
            _inv_id = invoice.get("id")
            if _inv_id:
                _by_inv = await db_supabase.find_one("rides", {"stripe_invoice_id": _inv_id})
                if _by_inv:
                    _ride_invoice_id = _by_inv.get("id")
        if _ride_invoice_id:
            # Payable ride invoice (admin "Send Invoice" remediation for a card
            # rejected at trip end). Settle the ride + credit the driver; the
            # subscription path below is untouched.
            await _handle_ride_invoice_paid(invoice, _ride_invoice_id, event_id, stripe_secret)
        elif not stripe_sub_id:
            logger.info(
                "invoice.paid without subscription id — skipping",
                extra={"domain": "drivers", "event_id": event_id},
            )
        else:
            row = await db_supabase.find_one("driver_subscriptions", {"stripe_subscription_id": stripe_sub_id})
            if not row and stripe_secret:
                # Out-of-order delivery: checkout.session.completed / verify-session
                # hasn't linked stripe_subscription_id onto the row yet. Recover it
                # from the subscription's metadata (set via subscription_data.metadata
                # at checkout) and link it, so the FIRST renewal charge still lands in
                # the ledger instead of being silently dropped.
                try:
                    import stripe as _stripe

                    _sub_obj = _stripe.Subscription.retrieve(stripe_sub_id, api_key=stripe_secret)
                    _meta_sub_id = (_sub_obj.get("metadata") or {}).get("subscription_id")
                    if _meta_sub_id:
                        row = await db_supabase.find_one("driver_subscriptions", {"id": _meta_sub_id})
                        if row and not row.get("stripe_subscription_id"):
                            await db_supabase.update_one(
                                "driver_subscriptions",
                                {"id": row["id"]},
                                {"stripe_subscription_id": stripe_sub_id},
                            )
                except Exception:
                    logger.error(
                        "invoice.paid: failed to recover subscription row from metadata for %s",
                        stripe_sub_id,
                        exc_info=True,
                        extra={"domain": "drivers", "event_id": event_id},
                    )
            if not row:
                logger.warning(
                    "invoice.paid: no subscription row for stripe_sub %s",
                    stripe_sub_id,
                    extra={"domain": "drivers", "event_id": event_id},
                )
            elif row.get("status") in ("cancelled", "superseded") or row.get("cancelled_at"):
                # Terminal row — a late/duplicate invoice.paid arriving after a
                # local cancel, customer.subscription.deleted, or supersede must
                # NOT flip the row back to active and restore gated access.
                logger.warning(
                    "invoice.paid ignored for cancelled subscription: row=%s stripe_sub=%s",
                    row["id"],
                    stripe_sub_id,
                    extra={"domain": "drivers", "event_id": event_id},
                )
            else:
                new_expires = _invoice_period_end_iso(invoice)
                if not new_expires:
                    duration_days = 30
                    if row.get("plan_id"):
                        plan = await db_supabase.find_one("subscription_plans", {"id": row["plan_id"]})
                        if plan and plan.get("duration_days"):
                            duration_days = plan["duration_days"]
                    new_expires = (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat()

                # Re-anchor started_at to the new cycle so the row's lifetime
                # tracks one period. Without this, expires_at advances each
                # renewal while started_at stays put, and a recurring 1-day pass
                # eventually reads as multi-day (lifetime > 25h) — handing the
                # driver a calendar-day reset on top of their 24h allowance.
                new_started = _invoice_period_start_iso(invoice) or datetime.now(timezone.utc).isoformat()
                await db_supabase.update_one(
                    "driver_subscriptions",
                    {"id": row["id"]},
                    {
                        "status": "active",
                        "payment_status": "paid",
                        "started_at": new_started,
                        "expires_at": new_expires,
                        "expiry_warned": False,
                        "expiry_warned_3d": False,
                    },
                )
                logger.info(
                    "Spinr Pass renewed: row=%s stripe_sub=%s until=%s",
                    row["id"],
                    stripe_sub_id,
                    new_expires,
                    extra={"domain": "drivers", "event_id": event_id},
                )

                # Record this charge (first invoice + every renewal) in the
                # subscription_payments ledger so admin revenue stats capture
                # recurring renewals. Deduped on the unique stripe_invoice_id
                # index, so a replay is a no-op.
                try:
                    from ..routes.drivers import subscriptions as _drv_subs  # type: ignore
                except ImportError:
                    from routes.drivers import subscriptions as _drv_subs  # type: ignore
                # amount_paid is the authoritative charged amount; it can be a
                # legitimate 0 (100% coupon / trial), which is falsy, so test
                # for None rather than truthiness before falling back.
                _amount_cents = invoice.get("amount_paid")
                if _amount_cents is None:
                    _amount_cents = invoice.get("amount_due") or 0
                _inv_billing_reason = invoice.get("billing_reason") or "subscription_cycle"
                _inv_amount = cents_to_dollars(_amount_cents)

                # Fetch plan for duration label and pre-tax price.
                _inv_plan = None
                if row.get("plan_id"):
                    _inv_plan = await db_supabase.find_one("subscription_plans", {"id": row["plan_id"]})
                try:
                    from ..utils.spinr_pass import pass_duration_label
                except ImportError:
                    from utils.spinr_pass import pass_duration_label  # type: ignore
                _inv_dur = pass_duration_label((_inv_plan or {}).get("duration_days", 30))
                _inv_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

                # Compute tax breakdown from the driver's service-area config.
                _plan_price = Decimal(str((_inv_plan or {}).get("price") or _inv_amount or 0))
                _wh_tax = await _drv_subs._compute_subscription_tax(row.get("driver_id"), _plan_price)

                # Use the actual Stripe-charged amount as the authoritative ledger
                # figure — _wh_tax["total"] is computed from the plan price and may
                # differ from _inv_amount when a coupon or proration is applied.
                await _drv_subs._record_subscription_payment(
                    driver_id=row.get("driver_id"),
                    subscription_id=row["id"],
                    plan_id=row.get("plan_id"),
                    plan_name=row.get("plan_name"),
                    amount=_inv_amount,
                    billing_reason=_inv_billing_reason,
                    subtotal=_wh_tax["subtotal"],
                    gst_amount=_wh_tax["gst_amount"],
                    pst_amount=_wh_tax["pst_amount"],
                    hst_amount=_wh_tax["hst_amount"],
                    tax_total=_wh_tax["tax_total"],
                    province=_wh_tax["province"],
                    stripe_invoice_id=invoice.get("id"),
                    stripe_invoice_url=invoice.get("hosted_invoice_url"),
                )

                # Send invoice email for every recurring charge (subscription_create
                # = first charge on a recurring plan, subscription_cycle = renewal).
                # One-off plans email from _activate_subscription instead.
                # Fire-and-forget so we don't hold Stripe's webhook response open.
                if _inv_amount and Decimal(str(_inv_amount)) > 0:
                    import asyncio as _asyncio

                    _asyncio.create_task(
                        _drv_subs._send_subscription_invoice_email(
                            driver_id=row.get("driver_id"),
                            plan_name=row.get("plan_name") or "Spinr Pass",
                            duration_label=_inv_dur,
                            subtotal=_wh_tax["subtotal"],
                            gst_amount=_wh_tax["gst_amount"],
                            pst_amount=_wh_tax["pst_amount"],
                            hst_amount=_wh_tax["hst_amount"],
                            tax_total=_wh_tax["tax_total"],
                            total=_wh_tax["total"],
                            province=_wh_tax["province"],
                            billing_reason=_inv_billing_reason,
                            payment_date=_inv_date,
                            stripe_invoice_url=invoice.get("hosted_invoice_url"),
                        )
                    )

                # Only push-notify on actual renewal cycles — the initial invoice's
                # activation push already went out from the checkout handler.
                if invoice.get("billing_reason") == "subscription_cycle":
                    driver_row = await db_supabase.find_one("drivers", {"id": row.get("driver_id")})
                    if driver_row and driver_row.get("user_id"):
                        try:
                            await send_push_notification(
                                driver_row["user_id"],
                                "Spinr Pass renewed",
                                "Your Spinr Pass renewed. You're all set to keep accepting rides.",
                                data={"type": "subscription_renewed", "deeplink": "/driver/subscription"},
                            )
                        except Exception:
                            logger.warning(
                                "Push notification failed for subscription_renewed; continuing",
                                exc_info=True,
                            )

    elif event_type == "invoice.payment_failed":
        # Recurring renewal charge failed. Flag the row past_due and nudge the
        # driver to update their card. We don't terminate here — Stripe retries
        # per its dunning settings, then fires customer.subscription.updated
        # (past_due) / .deleted (canceled), which we handle separately.
        invoice = data_object
        stripe_sub_id = invoice.get("subscription")
        row = None
        if stripe_sub_id:
            row = await db_supabase.find_one("driver_subscriptions", {"stripe_subscription_id": stripe_sub_id})
        if not row:
            logger.warning(
                "invoice.payment_failed: no subscription row for stripe_sub %s",
                stripe_sub_id,
                extra={"domain": "drivers", "event_id": event_id},
            )
        else:
            await db_supabase.update_one(
                "driver_subscriptions",
                {"id": row["id"]},
                {"payment_status": "past_due"},
            )
            logger.warning(
                "Spinr Pass renewal payment failed: row=%s stripe_sub=%s",
                row["id"],
                stripe_sub_id,
                extra={"domain": "drivers", "event_id": event_id},
            )
            driver_row = await db_supabase.find_one("drivers", {"id": row.get("driver_id")})
            if driver_row and driver_row.get("user_id"):
                try:
                    await send_push_notification(
                        driver_row["user_id"],
                        "Spinr Pass payment failed",
                        "We couldn't renew your Spinr Pass. Please update your card to keep accepting rides.",
                        data={"type": "subscription_past_due", "deeplink": "/driver/subscription"},
                    )
                except Exception:
                    logger.warning(
                        "Push notification failed for subscription_past_due; continuing",
                        exc_info=True,
                    )

    elif event_type == "customer.subscription.updated":
        # Mirror Stripe's authoritative subscription status onto our row.
        # Fires often; we act only on meaningful transitions and ack the rest.
        subscription = data_object
        stripe_sub_id = subscription.get("id")
        stripe_status = subscription.get("status")
        row = None
        if stripe_sub_id:
            row = await db_supabase.find_one("driver_subscriptions", {"stripe_subscription_id": stripe_sub_id})
        if not row:
            logger.info(
                "customer.subscription.updated: no row for stripe_sub %s — skipping",
                stripe_sub_id,
                extra={"domain": "drivers", "event_id": event_id},
            )
        else:
            updates: dict = {}
            if stripe_status in ("canceled", "unpaid", "incomplete_expired"):
                updates = {
                    "status": "cancelled",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                }
            elif stripe_status == "past_due":
                updates = {"payment_status": "past_due"}
            elif stripe_status == "active":
                # Don't resurrect a row already cancelled (driver/Stripe) or
                # superseded by a newer checkout — a late "active" update must
                # not restore gated access.
                if row.get("status") not in ("cancelled", "superseded") and not row.get("cancelled_at"):
                    updates = {"status": "active", "payment_status": "paid"}
            if updates:
                await db_supabase.update_one("driver_subscriptions", {"id": row["id"]}, updates)
                logger.info(
                    "Spinr Pass subscription updated: row=%s stripe_status=%s",
                    row["id"],
                    stripe_status,
                    extra={"domain": "drivers", "event_id": event_id},
                )

    elif event_type in ("payout.paid", "payout.failed"):
        # Connected-account bank settlement of a driver payout. Instant
        # payouts (routes/drivers.py request_instant_payout) do an explicit
        # Payout.create and store its po_ id in payouts.stripe_payout_id, so
        # those reconcile here. Standard payouts only do a Transfer and store
        # a tr_ id — those won't match a Payout event, and Stripe's automatic
        # scheduled payouts of the connected balance also fire here with no
        # tracked row. A no-match is therefore expected: log and ack, never
        # 5xx (a 5xx would make Stripe retry a payout we don't track for days).
        stripe_payout_id = data_object.get("id")
        payout_row = None
        if stripe_payout_id:
            payout_row = await db_supabase.find_one("payouts", {"stripe_payout_id": stripe_payout_id})

        if not payout_row:
            logger.info(
                "payout webhook %s: no tracked payout row for %s "
                "(auto-scheduled connected-account payout or standard transfer) — acking",
                event_type,
                stripe_payout_id,
                extra={"domain": "payments", "event_id": event_id},
            )
        elif event_type == "payout.paid":
            await db_supabase.update_one(
                "payouts",
                {"id": payout_row["id"]},
                {
                    "status": "completed",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "Payout settled to bank: payout_row=%s po=%s",
                payout_row["id"],
                stripe_payout_id,
                extra={"domain": "payments", "event_id": event_id},
            )
        else:  # payout.failed
            failure_message = (
                data_object.get("failure_message") or data_object.get("failure_code") or "Bank payout failed"
            )
            await db_supabase.update_one(
                "payouts",
                {"id": payout_row["id"]},
                {
                    "status": "failed",
                    "failure_reason": str(failure_message)[:500],
                    "requires_manual_review": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.error(
                "PAYOUT FAILED at bank: payout_row=%s po=%s reason=%s",
                payout_row["id"],
                stripe_payout_id,
                failure_message,
                extra={
                    "domain": "payments",
                    "event_id": event_id,
                    "driver_id": payout_row.get("driver_id") or "",
                },
            )
            # Notify the driver their money didn't land so they can fix their
            # bank details and retry.
            driver_row = await db_supabase.find_one("drivers", {"id": payout_row.get("driver_id")})
            if driver_row and driver_row.get("user_id"):
                try:
                    await send_push_notification(
                        driver_row["user_id"],
                        "Payout failed",
                        "Your payout could not be deposited. Please check your bank details and try again.",
                        data={"type": "payout_failed", "deeplink": "/driver/earnings"},
                    )
                except Exception:
                    logger.warning(
                        "Push notification failed for payout_failed event; continuing",
                        exc_info=True,
                    )

    else:
        if event_type in _STRIPE_HANDLED_EVENTS:
            logger.error(
                "[WEBHOOK] Event type %r matched allowlist but fell through dispatch — "
                "handler logic gap; check for missing elif branch",
                event_type,
                extra={"domain": "payments", "event_id": event_id},
            )
        elif event_type in _STRIPE_IGNORED_EVENTS:
            # Routine lifecycle echo we deliberately don't process — debug, not
            # warning, so it doesn't drown out genuinely unexpected event types.
            logger.debug(
                "[WEBHOOK] Ignoring routine Stripe lifecycle event %r (not actionable).",
                event_type,
                extra={"domain": "payments", "event_id": event_id},
            )
        else:
            logger.warning(
                "[WEBHOOK] Unhandled Stripe event type %r — not in _STRIPE_HANDLED_EVENTS. "
                "Either add a handler or stop sending it from the Stripe dashboard.",
                event_type,
                extra={"domain": "payments", "event_id": event_id},
            )
        # Leave processed_at NULL for unknown/unhandled events so
        # utils/stripe_reconcile.py's daily sweep surfaces them for manual
        # review if they later become actionable (it does not auto-replay
        # -- see _reconcile_stuck_stripe_events, ACTION_ITEMS.md C10).
        # Return 200 to Stripe so it does not retry indefinitely.
        return {"received": True, "unhandled": True, "event_id": event_id}

    # Success — stamp processed_at. Non-fatal if this fails (we've
    # already finished the side effects, and Stripe won't retry a 2xx).
    await mark_stripe_event_processed(event_id)

    return {"received": True, "event_id": event_id}


# ---------------------------------------------------------------------------
# Amazon SES bounce/complaint feedback (via SNS)
# ---------------------------------------------------------------------------
#
# SES publishes Bounce/Complaint/Delivery notifications to an SNS topic; SNS
# POSTs them here. We verify the SNS signature (the endpoint is public), then
# on a *permanent* bounce or a complaint we add the address to
# email_suppressions so email_provider stops sending to it — that's what keeps
# our SES bounce/complaint rate (and thus our sending reputation) healthy.


async def _confirm_sns_subscription(payload: dict) -> None:
    """Confirm an SNS subscription by GETting its SubscribeURL.

    The URL host is validated (https + sns.*.amazonaws.com) first so a forged
    confirmation can't make us issue a request to an arbitrary host.
    """
    try:
        from ..utils.sns_verify import is_trusted_sns_url
    except ImportError:
        from utils.sns_verify import is_trusted_sns_url  # type: ignore

    url = payload.get("SubscribeURL") or ""
    if not is_trusted_sns_url(url):
        logger.error("[SES] refusing to confirm subscription — untrusted SubscribeURL")
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            logger.error("[SES] subscription confirm returned %s — not confirmed", resp.status_code)
            return
        logger.info("[SES] SNS subscription confirmed topic=%s", payload.get("TopicArn"))
    except Exception:
        logger.error("[SES] subscription confirm request failed", exc_info=True)


async def _topic_arn_allowed(topic_arn) -> bool:
    """True if this SNS message's TopicArn is the one we expect.

    A valid AWS signature only proves the message came from *some* SNS topic;
    without this check an attacker could subscribe our endpoint to a foreign
    topic and suppress our addresses. Blank setting = allow + warn (dev).
    """
    settings = await get_app_settings()
    expected = (settings.get("aws_ses_sns_topic_arn") or "").strip()
    if not expected:
        logger.warning("[SES] aws_ses_sns_topic_arn not configured — accepting SNS topic without allowlist")
        return True
    if topic_arn != expected:
        logger.error("[SES] rejected SNS message from unexpected topic")
        return False
    return True


async def _suppress_address(email: str, *, reason: str, detail, message_id) -> None:
    """Idempotently add one address to email_suppressions.

    PIPEDA: the email address itself is never logged — only the reason and the
    SES message id (which carries no PII). DuplicateRecordError (a concurrent
    SNS redelivery losing the insert race) is treated as success; any other
    DatabaseError propagates so the webhook returns 503 and SNS retries.
    """
    try:
        from ..utils.email_provider import normalize_email
    except ImportError:
        from utils.email_provider import normalize_email  # type: ignore

    norm = normalize_email(email)
    if not norm:
        return
    try:
        # SNS may redeliver — skip if we already suppressed this address.
        existing = await db_supabase.find_one("email_suppressions", {"email": norm})
        if existing:
            return
        await db_supabase.insert_one(
            "email_suppressions",
            {"email": norm, "reason": reason, "detail": detail, "source": "ses", "message_id": message_id},
        )
        logger.warning("[SES] address suppressed reason=%s message_id=%s", reason, message_id or "-")
    except DuplicateRecordError:
        # Concurrent redelivery already inserted it — idempotent success.
        logger.info(
            "[SES] suppression already present (insert race) reason=%s message_id=%s", reason, message_id or "-"
        )
    except DatabaseError as e:
        logger.error(
            "[SES] suppression write failed reason=%s: %s",
            reason,
            (e.details or {}).get("original", str(e)),
            exc_info=True,
        )
        raise


async def _suppress_marketing_email(email: str, *, reason: str, detail, message_id) -> None:
    """Add an address to the MARKETING suppression list (email channel).

    Product rule: ANY bounce — transient OR permanent — permanently blocks
    MARKETING to that address, while transactional mail keeps flowing until a
    hard bounce (handled separately by _suppress_address). Complaints block
    both. Best-effort user attribution for the admin view; the suppression
    itself keys on the address. Errors propagate so the webhook 503s and SNS
    retries (add_marketing_suppression is idempotent).
    """
    try:
        from ..services import marketing_consent
    except ImportError:
        from services import marketing_consent  # type: ignore

    norm = marketing_consent.normalize_target("email", email)
    if not norm:
        return
    user_id = None
    try:
        u = await db_supabase.find_one("users", {"email": norm})
        if u:
            user_id = u.get("id")
    except Exception:
        # Attribution is best-effort; a lookup hiccup must not skip suppression.
        logger.error("[SES] marketing-suppression user lookup failed message_id=%s", message_id or "-", exc_info=True)
    await marketing_consent.add_marketing_suppression(
        "email", norm, reason=reason, source="ses", user_id=user_id, message_id=message_id
    )


async def _handle_ses_notification(payload: dict) -> dict:
    """Parse an SES notification and suppress hard-bounced/complained recipients."""
    import json

    try:
        inner = json.loads(payload.get("Message") or "{}")
    except (ValueError, TypeError):
        logger.error("[SES] notification Message was not valid JSON — ignoring")
        return {"received": True, "ignored": "bad_message"}

    ntype = inner.get("notificationType") or inner.get("eventType")
    message_id = (inner.get("mail") or {}).get("messageId")
    # `suppressed` counts TRANSACTIONAL suppressions (the historical contract:
    # addresses blocked from all mail). `marketing_suppressed` counts the
    # marketing-only blocks, which fire far more eagerly.
    suppressed = 0
    marketing_suppressed = 0

    if ntype == "Bounce":
        bounce = inner.get("bounce") or {}
        subtype = bounce.get("bounceSubType")
        is_permanent = bounce.get("bounceType") == "Permanent"
        for r in bounce.get("bouncedRecipients") or []:
            addr = r.get("emailAddress")
            # TRANSACTIONAL: only PERMANENT (hard) bounces suppress all mail;
            # transient bounces may recover, so receipts keep trying.
            if is_permanent:
                await _suppress_address(addr, reason="bounce", detail=subtype, message_id=message_id)
                suppressed += 1
            # MARKETING: any bounce (transient OR permanent) blocks marketing.
            await _suppress_marketing_email(addr, reason="bounce", detail=subtype, message_id=message_id)
            marketing_suppressed += 1
    elif ntype == "Complaint":
        complaint = inner.get("complaint") or {}
        subtype = complaint.get("complaintFeedbackType")
        for r in complaint.get("complainedRecipients") or []:
            addr = r.get("emailAddress")
            # A complaint blocks BOTH transactional and marketing mail.
            await _suppress_address(addr, reason="complaint", detail=subtype, message_id=message_id)
            suppressed += 1
            await _suppress_marketing_email(addr, reason="complaint", detail=subtype, message_id=message_id)
            marketing_suppressed += 1
    # Delivery / other: acknowledged, no suppression.

    return {"received": True, "type": ntype, "suppressed": suppressed, "marketing_suppressed": marketing_suppressed}


@api_router.post("/ses")
@default_limiter.limit("100/minute")
async def ses_sns_webhook(request: Request):
    """Receive SES bounce/complaint feedback via SNS. Signature-verified."""
    import json

    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid JSON") from None

    try:
        from ..utils.sns_verify import verify_sns_signature
    except ImportError:
        from utils.sns_verify import verify_sns_signature  # type: ignore

    # Verification fetches the signing cert + does an RSA verify — both
    # blocking — so run it off the event loop.
    if not await asyncio.to_thread(verify_sns_signature, payload):
        # 403: do not act on an unverified message.
        raise HTTPException(status_code=403, detail="invalid SNS signature")

    # Even a validly-signed message must be from our expected topic.
    if not await _topic_arn_allowed(payload.get("TopicArn")):
        raise HTTPException(status_code=403, detail="unexpected SNS topic")

    msg_type = payload.get("Type")
    if msg_type == "SubscriptionConfirmation":
        await _confirm_sns_subscription(payload)
        return {"received": True, "confirmed": True}
    if msg_type == "Notification":
        try:
            return await _handle_ses_notification(payload)
        except DatabaseError as e:
            # Surface as 503 so SNS retries rather than dropping the bounce.
            raise HTTPException(status_code=503, detail="suppression store unavailable") from e

    # UnsubscribeConfirmation / unknown — acknowledge without action.
    return {"received": True, "ignored": msg_type}


# ── Inbound SMS (Twilio): STOP/START for marketing consent ──────────────────

# Twilio's standard opt-out / opt-in keywords (carriers also honour these).
_SMS_STOP_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})
_SMS_START_KEYWORDS = frozenset({"START", "YES", "UNSTOP"})
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _resolve_user_id_by_phone(phone: str) -> "str | None":
    """Best-effort user_id for an inbound number. Never raises."""
    try:
        try:
            from ..services import marketing_consent
        except ImportError:
            from services import marketing_consent  # type: ignore
        norm = marketing_consent.normalize_target("sms", phone)
        user = await db_supabase.find_one("users", {"phone": norm})
        return user.get("id") if user else None
    except Exception:
        logger.error("[TWILIO] inbound user lookup failed", exc_info=True)
        return None


async def _handle_sms_keyword(phone: str, opted_in: bool) -> None:
    """Apply an SMS opt-in/opt-out for the number. On opt-out we also add the
    number to the marketing suppression list so a future broadcast can't reach
    it even before the preference is re-read."""
    try:
        from ..services import marketing_consent
    except ImportError:
        from services import marketing_consent  # type: ignore

    user_id = await _resolve_user_id_by_phone(phone)
    if user_id:
        await marketing_consent.set_consent(
            user_id, "sms", opted_in, source="sms_stop" if not opted_in else "rider_app"
        )
    if not opted_in:
        await marketing_consent.add_marketing_suppression(
            "sms", phone, reason="sms_stop", source="twilio", user_id=user_id
        )


@api_router.post("/twilio-inbound")
@default_limiter.limit("60/minute")
async def twilio_inbound_sms(request: Request):
    """Inbound SMS webhook (Twilio). Honours STOP/START for marketing SMS.

    Signature-verified with the Twilio auth token over the PUBLIC URL + POST
    params (RequestValidator). When the auth token is unset (dev) we skip
    verification and warn, mirroring the SES topic-blank behaviour. Always
    returns empty TwiML so Twilio doesn't surface a delivery error.
    """
    form = await request.form()
    body = (form.get("Body") or "").strip()
    from_phone = (form.get("From") or "").strip()

    settings = await get_app_settings()
    auth_token = (settings.get("twilio_auth_token") or "").strip()
    signature = request.headers.get("X-Twilio-Signature", "")
    if auth_token:
        try:
            from twilio.request_validator import RequestValidator
        except ImportError:
            # FAIL CLOSED. This previously set RequestValidator = None and fell
            # through to processing the webhook unverified: an admin had
            # configured a token, so verification was *expected*, and an import
            # failure silently turned the check off while still honouring the
            # STOP/START it carried — anyone who could reach the endpoint could
            # toggle marketing consent for an arbitrary phone number.
            #
            # twilio is a hard dependency (requirements.txt), so this branch is
            # unreachable in production; the danger was never the missing
            # package, it was that a security control could disable itself
            # without anyone noticing. It stayed unnoticed for exactly that
            # reason: three test modules stub sys.modules["twilio"] at import
            # time, which sent the full-suite run down this path and turned
            # test_invalid_signature_returns_403 red — reported as a flaky test
            # rather than as the fail-open it was pointing at.
            logger.error(
                "[TWILIO] twilio.request_validator unavailable but twilio_auth_token "
                "is configured — refusing to process an unverified inbound webhook"
            )
            return Response(status_code=503)
        # Twilio signs the PUBLIC URL configured in its console, not the
        # internal one FastAPI sees behind the proxy — rebuild from config.
        base = (app_config.PUBLIC_API_BASE_URL or "").rstrip("/")
        url = f"{base}{request.url.path}"
        params = {k: v for k, v in form.items()}
        if not RequestValidator(auth_token).validate(url, params, signature):
            logger.warning("[TWILIO] inbound SMS signature invalid — rejecting")
            return Response(status_code=403)
    else:
        logger.warning("[TWILIO] inbound SMS not signature-verified (twilio_auth_token unset)")

    if not from_phone:
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    keyword = body.upper()
    if keyword in _SMS_STOP_KEYWORDS:
        await _handle_sms_keyword(from_phone, opted_in=False)
        logger.info("[TWILIO] inbound STOP processed")
    elif keyword in _SMS_START_KEYWORDS:
        await _handle_sms_keyword(from_phone, opted_in=True)
        logger.info("[TWILIO] inbound START processed")

    return Response(content=_EMPTY_TWIML, media_type="application/xml")
