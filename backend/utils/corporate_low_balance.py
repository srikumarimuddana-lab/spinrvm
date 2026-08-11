"""Low-balance email notifications for corporate wallets with auto-topup OFF.

Runs in its own background loop (wired in core/lifespan.py). Every tick
scans wallets whose balance has fallen below their auto_topup_threshold
and whose auto_topup_enabled is False, then sends a single reminder
email per wallet per 12-hour window.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        list_wallets_low_balance_no_autotopup,
        mark_low_balance_notified,
    )
    from ..features import send_email  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_account_by_id,
        list_wallets_low_balance_no_autotopup,
        mark_low_balance_notified,
    )
    from features import send_email  # type: ignore

try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge


logger = logging.getLogger(__name__)

_RATE_LIMIT = timedelta(hours=12)


async def run_low_balance_tick() -> None:
    # Kill switch (ACTION_ITEMS.md E5): pauses automatic corporate money
    # movement for an incident. Shared with settle_corporate and the other
    # 3 corporate loops. Fail-open on a settings-read error. Lazy dual
    # import -- see settle_corporate's identical pattern/comment in
    # services/payment_service.py for why (a formatter hook in this repo
    # strips additions to some files' module-level except-branch imports).
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        settings = await get_app_settings()
        if not settings.get("corporate_billing_enabled", True):
            logger.info("low-balance: corporate_billing_enabled is False, skipping tick")
            return
    except Exception as settings_err:
        logger.warning("low-balance: app_settings lookup failed (%s), proceeding as enabled", settings_err)

    wallets = await list_wallets_low_balance_no_autotopup()
    for w in wallets:
        last = w.get("low_balance_notified_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            except ValueError:
                # A malformed timestamp must not bypass the rate limit and
                # re-send every tick until the DB value is repaired — fail
                # closed by treating it as "just notified" (full rate-limit
                # window applies) rather than "never notified".
                logger.error(
                    "low-balance: malformed low_balance_notified_at %r for wallet %s — "
                    "treating as just-notified, not never-notified",
                    last,
                    w.get("id"),
                )
                last_dt = datetime.now(timezone.utc)
            if last_dt and datetime.now(timezone.utc) - last_dt < _RATE_LIMIT:
                continue
        try:
            await _notify_one(w)
        except Exception:
            logger.exception("low-balance notify failed for wallet %s", w.get("id"))


async def _notify_one(wallet: dict) -> None:
    company = await get_corporate_account_by_id(wallet["company_id"])
    if not company or not company.get("billing_email"):
        return
    if (company.get("status") or "").lower() != "active":
        # A suspended/closed company must not keep receiving "top up your
        # wallet" nudges — top-up is deliberately disabled during suspension
        # (corporate_accounts.py), and a closed account's wallet may already
        # be refunded to zero, so a low-balance email is confusing at best.
        # Corporate module lifecycle audit Finding 3.
        return
    subject = f"[Spinr Business] Wallet balance low — {company.get('name')}"
    body = (
        f"Your corporate wallet balance is ${wallet['balance']} CAD, which is below\n"
        f"your low-balance threshold of ${wallet['auto_topup_threshold']}.\n\n"
        f"Top up from the admin portal to avoid ride interruptions.\n\n"
        f"— Spinr Business"
    )
    await send_email(to=company["billing_email"], subject=subject, body=body)
    await mark_low_balance_notified(wallet_id=wallet["id"])


async def corporate_low_balance_loop() -> None:
    """Background loop — one tick per hour."""
    logger.info("Corporate low-balance notifier started")
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await run_low_balance_tick()
        except Exception as e:
            logger.error("low-balance loop error: %s", e)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "corporate_low_balance"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "corporate_low_balance"})
        _record_heartbeat("corporate_low_balance (1h)")
        await asyncio.sleep(3600 * (0.9 + random.random() * 0.2))
