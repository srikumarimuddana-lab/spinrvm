"""Monthly allowance reset — rolls fixed_recurring periods forward and
zeroes `used` for non-rollover allowances.

Runs as a scheduled loop (pattern: utils/scheduled_rides.py). Replay-safe:
`list_allowances_due_for_reset` short-circuits when `period_end >= today`, and
each period roll-forward is claimed via an atomic compare-and-swap on
`period_end` so only one replica processes a given allowance per period (F8).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date
from typing import Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db_supabase import (  # type: ignore
        get_corporate_member_by_id,
        get_corporate_wallet_by_company,
        list_allowances_due_for_reset,
        reset_allowance_period,
    )
    from ..services.corporate_allowance_service import apply_reset  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        get_corporate_member_by_id,
        get_corporate_wallet_by_company,
        list_allowances_due_for_reset,
        reset_allowance_period,
    )
    from services.corporate_allowance_service import apply_reset  # type: ignore

try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge


logger = logging.getLogger(__name__)


def _add_one_month(d: date) -> date:
    """Month-add: when the day doesn't exist next month (e.g. Jan 31 → Feb),
    clamp down to the last valid day."""
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    for day in range(d.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


async def run_allowance_reset_tick(now: Optional[date] = None) -> int:
    today = now or date.today()
    rows = await list_allowances_due_for_reset(as_of=today.isoformat())
    processed = 0
    for r in rows:
        try:
            old_end = date.fromisoformat(r["period_end"])
            new_start = old_end
            new_end = _add_one_month(old_end)
            member = await get_corporate_member_by_id(r["member_id"])
            if not member:
                continue
            if member.get("status") != "active":
                # A removed/suspended member's allowance must not keep
                # replenishing indefinitely — this loop previously only
                # checked that the member row existed, not that it was
                # still active, so a removed employee's budget kept
                # resetting to full every period forever. See corporate
                # module review gap #3.
                continue
            wallet = await get_corporate_wallet_by_company(member["company_id"])
            if not wallet:
                continue
            # Replay-safety (F8): claim this period roll-forward atomically via a
            # compare-and-swap on period_end BEFORE zeroing `used`. On >=2 replicas
            # only the winner advances the period (the others match zero rows and
            # skip), so apply_reset's reset ledger entry is written once per period.
            # apply_reset is itself idempotent (re-zeroing `used` is a no-op and a
            # reset moves no master funds), so a crash between the claim and the
            # reset fails safe — `used` simply stays as-is (less budget, never more).
            claimed = await reset_allowance_period(
                allowance_id=r["id"],
                period_start=new_start.isoformat(),
                period_end=new_end.isoformat(),
                expected_period_end=r["period_end"],
            )
            if not claimed:
                continue
            if not r.get("rollover"):
                await apply_reset(
                    wallet_id=wallet["id"],
                    allowance_id=r["id"],
                    member_id=r["member_id"],
                    actor_user_id=None,
                    notes=f"period reset {new_start} -> {new_end}",
                )
            processed += 1
        except Exception:
            logger.exception("allowance reset failed for %s", r.get("id"))
    return processed


async def allowance_reset_loop(interval_seconds: int = 3600) -> None:
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await run_allowance_reset_tick()
        except Exception:
            logger.exception("allowance reset tick raised")
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "allowance_reset"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "allowance_reset"})
        _record_heartbeat("allowance_reset (1h)")
        await asyncio.sleep(interval_seconds * (0.9 + random.random() * 0.2))
