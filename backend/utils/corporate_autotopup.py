"""Auto-top-up scheduled task for corporate wallets.

Runs every 10 minutes (wired from lifespan startup). Each tick:
  1. Finds wallets below threshold with auto_topup_enabled.
  2. Skips any that would exceed today's daily cap.
  3. Creates an off-session Stripe PaymentIntent against the customer's
     default payment method with confirm=True.
  4. The webhook handler (Task 5) credits the wallet when the charge
     clears — no work here beyond kicking off the intent.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

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


logger = logging.getLogger(__name__)


async def run_autotopup_tick() -> None:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        logger.warning("autotopup: no stripe secret configured, skipping tick")
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
        logger.warning("wallet %s has no stripe_customer_id", wallet["id"])
        return

    topup_amount = float(wallet["auto_topup_amount"])
    daily_cap = float(wallet.get("auto_topup_daily_cap") or 5000)
    today_sum = await sum_autotopups_today(wallet["id"])
    if today_sum + topup_amount > daily_cap:
        logger.info(
            "autotopup: wallet %s at daily cap (%s + %s > %s)",
            wallet["id"],
            today_sum,
            topup_amount,
            daily_cap,
        )
        return

    pm_id = await get_default_payment_method(company["stripe_customer_id"], stripe_secret)
    if not pm_id:
        logger.warning("wallet %s has no default payment method", wallet["id"])
        return

    stripe.PaymentIntent.create(
        amount=int(round(topup_amount * 100)),
        currency="cad",
        customer=company["stripe_customer_id"],
        payment_method=pm_id,
        off_session=True,
        confirm=True,
        metadata={
            "scope": "corporate_topup",
            "company_id": company["id"],
            "wallet_id": wallet["id"],
            "initiated_by": "autotopup",
        },
        api_key=stripe_secret,
    )
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
            logger.error("autotopup loop error: %s", e)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "corporate_autotopup"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "corporate_autotopup"})
        _record_heartbeat("corporate_autotopup (10min)")
        await asyncio.sleep(600 * (0.9 + random.random() * 0.2))
