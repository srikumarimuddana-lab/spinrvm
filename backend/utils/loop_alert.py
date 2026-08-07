"""Background loop staleness alerter.

Reads loop heartbeat state from loop_monitor and posts a Slack-compatible
webhook message when a loop goes stale.  Throttles to one alert per loop
per COOLDOWN_SECONDS to avoid flooding the channel during a prolonged outage.

Usage (from the watchdog loop):
    await check_and_alert(registered_names=[...], webhook_url="https://...")
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import httpx

try:
    from .loop_monitor import get_loop_status
except ImportError:
    from utils.loop_monitor import get_loop_status  # type: ignore

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 3600  # one alert per loop per hour

# In-process throttle: loop_name → monotonic timestamp of last alert sent.
# Lost on restart — acceptable; fresh replicas re-alert after one watchdog tick.
_last_alerted: Dict[str, float] = {}


async def check_and_alert(
    registered_names: Optional[List[str]] = None,
    webhook_url: Optional[str] = None,
) -> None:
    """Check loop staleness and post alerts for newly-stale loops.

    Args:
        registered_names: loop names to check (pass the same list used in
                          lifespan.py so never-ticked loops are visible).
        webhook_url:       Slack-compatible incoming webhook URL.  No-op when
                          None (keeps development noise-free).
    """
    if not webhook_url:
        return

    status = get_loop_status(registered_names)
    now = time.monotonic()

    for name, info in status["loops"].items():
        if info["status"] != "stale":
            continue

        # "Never alerted" must be an explicit None, NOT a 0.0 default.
        # time.monotonic() counts from an arbitrary origin that is near zero
        # early in a process's life, so `now - 0.0 < COOLDOWN_SECONDS` is true
        # for the first COOLDOWN_SECONDS (1 h) of uptime — which silently
        # suppressed EVERY stale-loop alert for the first hour after each
        # deploy, restart, or Fly machine wake. That is the window where a loop
        # is most likely to fail to start at all, and this is the only alerting
        # path live in production, so the outage it exists to report was exactly
        # the one it could not report.
        last_sent = _last_alerted.get(name)
        if last_sent is not None and now - last_sent < COOLDOWN_SECONDS:
            continue  # already alerted recently

        elapsed = info.get("seconds_since_tick")
        threshold = info.get("threshold_seconds")
        elapsed_str = f"{int(elapsed)}s" if elapsed is not None else "unknown"
        threshold_str = f"{int(threshold)}s" if threshold is not None else "unknown"

        text = (
            f":warning: *Spinr loop stale*: `{name}`\n"
            f"Last tick: *{elapsed_str}* ago (threshold: {threshold_str})\n"
            f"Check Railway logs — the loop may have crashed or deadlocked."
        )
        payload = {"text": text}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=payload)
                resp.raise_for_status()
            _last_alerted[name] = now
            logger.info("loop_alert: posted stale-loop alert for %s", name)
        except Exception as exc:
            logger.error("loop_alert: failed to post webhook for %s: %s", name, exc)
