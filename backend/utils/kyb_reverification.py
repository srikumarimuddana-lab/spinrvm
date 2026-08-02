"""KYB re-verification staleness reminder (corporate + admin portal review
round 2, business decision: scheduled staleness reminder for admins — NOT
automatic re-verification, NOT an automatic status change, NOT a
third-party KYB provider integration).

Runs in its own background loop (wired in core/lifespan.py). Every tick
scans active, KYB-approved companies whose kyb_reviewed_at predates the
configured threshold, logs + increments a metric once per company per
cooldown window (never on every tick — mirrors
utils/corporate_low_balance.py's cooldown-in-Python pattern), and stamps
kyb_reverify_flagged_at as its own replay-safety claim. The admin-
dashboard "needs re-verification" filter never reads that claim flag — it
computes staleness live from kyb_reviewed_at, so it's correct even for a
company this loop hasn't ticked over yet.
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
        list_companies_needing_kyb_reverification,
        mark_kyb_reverify_flagged,
    )
    from ..settings_loader import get_app_settings  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        list_companies_needing_kyb_reverification,
        mark_kyb_reverify_flagged,
    )
    from settings_loader import get_app_settings  # type: ignore

try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD_MONTHS = 12
# Re-flag (re-log/re-count) a still-stale company at most once every 30
# days, not on every hourly tick — same reasoning as
# corporate_low_balance's 12h notification cooldown, scaled to how
# infrequently KYB review actually needs re-surfacing.
_REFLAG_COOLDOWN = timedelta(days=30)


async def run_kyb_reverification_tick() -> None:
    settings = await get_app_settings()
    if not settings.get("corporate_kyb_reverification_enabled", True):
        return

    threshold_months = settings.get("corporate_kyb_reverify_after_months")
    try:
        threshold_months = int(threshold_months) if threshold_months else _DEFAULT_THRESHOLD_MONTHS
    except (TypeError, ValueError):
        threshold_months = _DEFAULT_THRESHOLD_MONTHS

    cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_months * 30)
    companies = await list_companies_needing_kyb_reverification(reviewed_before_iso=cutoff.isoformat())

    newly_flagged = 0
    for company in companies:
        last_flagged = company.get("kyb_reverify_flagged_at")
        if last_flagged:
            try:
                last_dt = datetime.fromisoformat(str(last_flagged).replace("Z", "+00:00"))
            except ValueError:
                last_dt = None
            if last_dt and datetime.now(timezone.utc) - last_dt < _REFLAG_COOLDOWN:
                continue
        try:
            await mark_kyb_reverify_flagged(company_id=company["id"])
            newly_flagged += 1
            logger.info(
                "Corporate KYB re-verification due: company=%s last_reviewed=%s",
                company["id"],
                company.get("kyb_reviewed_at"),
                extra={"domain": "corporate"},
            )
        except Exception:
            logger.error("Failed to flag KYB re-verification for company %s", company.get("id"), exc_info=True)

    if newly_flagged:
        _metric_inc("spinr_corporate_kyb_reverification_due_total", {}, by=newly_flagged)
    _metric_gauge("spinr_corporate_kyb_reverification_pending", len(companies))


async def kyb_reverification_loop() -> None:
    """Background loop — one tick per 24 hours."""
    logger.info("Corporate KYB re-verification reminder started")
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await run_kyb_reverification_tick()
        except Exception as e:
            logger.error("kyb_reverification loop error: %s", e)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "kyb_reverification"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "kyb_reverification"})
        _record_heartbeat("kyb_reverification (24h)")
        await asyncio.sleep(86400 * (0.9 + random.random() * 0.2))
