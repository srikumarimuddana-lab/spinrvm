"""Low-balance email notifications for corporate wallets with auto-topup OFF.

Runs in its own background loop (wired in core/lifespan.py). Every tick
scans wallets whose balance has fallen below their auto_topup_threshold
and whose auto_topup_enabled is False, then sends a single reminder
email per wallet per 12-hour window.
"""

from __future__ import annotations

import asyncio
import logging
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


logger = logging.getLogger(__name__)

_RATE_LIMIT = timedelta(hours=12)


async def run_low_balance_tick() -> None:
    wallets = await list_wallets_low_balance_no_autotopup()
    for w in wallets:
        last = w.get("low_balance_notified_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            except ValueError:
                last_dt = None
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
        try:
            await run_low_balance_tick()
        except Exception as e:
            logger.error("low-balance loop error: %s", e)
        _record_heartbeat("corporate_low_balance (1h)")
        await asyncio.sleep(3600)
