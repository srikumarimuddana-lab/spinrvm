"""Auto-top-up scheduled task for corporate wallets.

Runs every 10 minutes (wired from lifespan startup). Each tick:
  1. Finds wallets below threshold with auto_topup_enabled.
  2. Skips any that would exceed today's daily cap.
  3. Creates an off-session Stripe PaymentIntent against the customer's
     default payment method with confirm=True.
  4. The webhook handler (Task 5) credits the wallet when the charge
     clears — no work here beyond kicking off the intent.

Replay-safety contract (CLAUDE.md, Background loops):
  This loop runs on every replica simultaneously. The Stripe
  PaymentIntent.create call is the side-effect we must not duplicate.
  We pass an ``idempotency_key`` derived from
  ``(wallet_id, today_date, today_sum_cents, topup_amount_cents)`` —
  two replicas observing the same today_sum (i.e. no top-up has
  cleared between their reads) generate the same key, so Stripe
  dedupes and only one charge fires. Once the first top-up is credited
  back via webhook, today_sum advances and the next tick uses a
  different key, allowing the legitimate next top-up.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from decimal import ROUND_HALF_UP, Decimal

import stripe

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_default_payment_method,
        list_wallets_needing_autotopup,
        sum_autotopups_today,
    )
    from ..settings_loader import get_app_settings  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        get_default_payment_method,
        list_wallets_needing_autotopup,
        sum_autotopups_today,
    )
    from settings_loader import get_app_settings  # type: ignore

try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge

try:
    from ..services.corporate_stripe_identity import (  # type: ignore
        corporate_customer_is_stale,
        retire_corporate_customer,
    )
    from .stripe_mode import is_missing_on_key  # type: ignore
except ImportError:
    from services.corporate_stripe_identity import (  # type: ignore
        corporate_customer_is_stale,
        retire_corporate_customer,
    )
    from utils.stripe_mode import is_missing_on_key  # type: ignore


logger = logging.getLogger(__name__)


async def run_autotopup_tick() -> None:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.error("autotopup: no stripe secret configured, skipping tick")
        return

    wallets = await list_wallets_needing_autotopup()
    for w in wallets:
        try:
            await _process_one(w, stripe_secret)
        except Exception as e:  # don't let one failure stop the rest
            logger.exception("autotopup failed for wallet %s: %s", w.get("id"), e)


async def _process_one(wallet: dict, stripe_secret: str) -> None:
    company = await get_corporate_account_by_id(wallet["company_id"])
    if not company or company.get("status") != "active":
        return
    if not company.get("stripe_customer_id"):
        logger.error("wallet %s has no stripe_customer_id", wallet["id"])
        return

    topup_amount = Decimal(str(wallet["auto_topup_amount"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    daily_cap = Decimal(str(wallet.get("auto_topup_daily_cap") or "5000")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    today_sum = await sum_autotopups_today(wallet["id"])
    if Decimal(str(today_sum)) + topup_amount > daily_cap:
        logger.info(
            "autotopup: wallet %s at daily cap (%s + %s > %s)",
            wallet["id"],
            today_sum,
            topup_amount,
            daily_cap,
        )
        return

    # A customer stranded by a test→live key rotation is retired here, NOT
    # replaced. Nobody is present to consent to a new payment identity, and a
    # fresh customer would carry no card — so the charge this tick wanted to
    # make cannot succeed either way. Retiring converts an endless 10-minutely
    # Stripe failure into one clean "no stripe_customer_id" state that the
    # guard above skips for free, that the admin dashboard already reports as
    # a 409, and that the next admin-present path re-provisions.
    if corporate_customer_is_stale(company, stripe_secret):
        await retire_corporate_customer(company, company["stripe_customer_id"], reason="mode_mismatch")
        return

    try:
        pm_id = await get_default_payment_method(company["stripe_customer_id"], stripe_secret)
    except Exception as e:
        if not is_missing_on_key(e, company["stripe_customer_id"]):
            raise
        await retire_corporate_customer(company, company["stripe_customer_id"], reason="resource_missing")
        return
    if not pm_id:
        logger.error("autotopup: wallet %s has no default payment method — skipping", wallet["id"])
        return

    # Replay-safe idempotency key — two replicas seeing the same
    # (wallet, today_sum, topup_amount) on the same calendar day produce
    # the same key, so Stripe collapses the duplicate call. Once the
    # first top-up clears via webhook, today_sum advances and the next
    # tick uses a different key. Hash to keep the key under Stripe's
    # 255-char limit and to avoid leaking ids in transit.
    import hashlib
    from datetime import date

    seed = (
        f"autotopup:{wallet['id']}:{date.today().isoformat()}:"
        f"{int(round(today_sum * 100))}:{int(round(topup_amount * 100))}"
    )
    idempotency_key = hashlib.sha256(seed.encode()).hexdigest()

    try:
        stripe.PaymentIntent.create(
            amount=int(topup_amount * 100),
            currency="cad",
            customer=company["stripe_customer_id"],
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            # Server-side off_session confirm: disable redirect-based payment
            # methods so Stripe doesn't require a `return_url` and reject the
            # auto-top-up with invalid_request_error.
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            metadata={
                "scope": "corporate_topup",
                "company_id": company["id"],
                "wallet_id": wallet["id"],
                "initiated_by": "autotopup",
            },
            api_key=stripe_secret,
            idempotency_key=idempotency_key,
        )
    except stripe.StripeError as e:
        if is_missing_on_key(e, company["stripe_customer_id"]):
            # Unstamped row (predating migration 286) whose customer turns out
            # to be unreachable. Same treatment as the stamped case above:
            # retire so the next 143 ticks today don't each re-discover it.
            await retire_corporate_customer(company, company["stripe_customer_id"], reason="resource_missing")
            return
        logger.error("autotopup: Stripe error for wallet %s: %s", wallet["id"], e, exc_info=True)
        return
    logger.info("autotopup: kicked intent for wallet %s (%s CAD)", wallet["id"], topup_amount)


async def corporate_autotopup_loop() -> None:
    """Background loop — one tick every 10 minutes."""
    logger.info("Corporate auto-topup loop started")
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await run_autotopup_tick()
        except Exception as e:
            logger.error("autotopup loop error: %s", e, exc_info=True)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "corporate_autotopup"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "corporate_autotopup"})
        _record_heartbeat("corporate_autotopup (10min)")
        await asyncio.sleep(600 * (0.9 + random.random() * 0.2))
