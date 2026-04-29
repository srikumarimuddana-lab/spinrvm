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

        last_sent = _last_alerted.get(name, 0.0)
        if now - last_sent < COOLDOWN_SECONDS:
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
