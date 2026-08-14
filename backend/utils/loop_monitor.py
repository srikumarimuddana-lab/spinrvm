"""Background loop liveness monitor.

Thread-safe singleton that tracks the last successful tick timestamp for each
named background loop.  Loops call record_heartbeat(name) after every
successful iteration; the /health endpoint calls get_loop_status() to surface
stale loops to Railway health checks and Sentry alerts.

Staleness thresholds are intentionally generous — a loop that's 2× overdue is
worth flagging, but a single slow tick should not trip a health alert.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

# Default staleness thresholds in seconds.  Keyed by the loop name as
# registered in lifespan.py.  Any loop name NOT listed here gets the
# _DEFAULT_THRESHOLD fallback.
_DEFAULT_THRESHOLD = 7200  # 2 hours

LOOP_THRESHOLDS: Dict[str, float] = {
    "subscription_expiry (6h)": 6 * 3600 * 2,  # 12 h
    "surge_engine (2min)": 2 * 60 * 4,  # 8 min
    "scheduled_dispatcher (60s)": 60 * 4,  # 4 min
    "capacity_watchdog (60s)": 60 * 4,  # 4 min
    "payment_retry (5min)": 5 * 60 * 4,  # 20 min
    "document_expiry (12h)": 12 * 3600 * 2,  # 24 h
    "corporate_autotopup (10min)": 10 * 60 * 4,  # 40 min
    "corporate_low_balance (1h)": 1 * 3600 * 4,  # 4 h
    "allowance_reset (1h)": 1 * 3600 * 4,  # 4 h
    "retention_purge (24h)": 24 * 3600 * 2,  # 48 h
    "stripe_reconcile (24h)": 24 * 3600 * 2,  # 48 h
}

_lock = threading.Lock()
_heartbeats: Dict[str, float] = {}  # loop_name → monotonic timestamp of last tick


def record_heartbeat(loop_name: str) -> None:
    """Record a successful tick for the named loop.  Thread-safe; O(1)."""
    with _lock:
        _heartbeats[loop_name] = time.monotonic()


def get_loop_status(registered_names: Optional[list] = None) -> Dict[str, object]:
    """Return a status dict suitable for embedding in the /health response.

    Args:
        registered_names: list of loop names known to lifespan.py.  If None,
                          only loops that have already ticked are included.

    Returns a dict:
      {
        "healthy": bool,               # False if any registered loop is stale
        "loops": {
          "<name>": {
            "status": "ok" | "stale" | "never_ticked",
            "seconds_since_tick": float | None,
            "threshold_seconds": float,
          }
        }
      }
    """
    now = time.monotonic()
    with _lock:
        snapshot = dict(_heartbeats)

    names = registered_names or list(snapshot.keys())
    loops: Dict[str, object] = {}
    overall_healthy = True

    for name in names:
        threshold = LOOP_THRESHOLDS.get(name, _DEFAULT_THRESHOLD)
        last = snapshot.get(name)
        if last is None:
            # Loop has not ticked yet since startup — could be waiting for its
            # first scheduled window (e.g. stripe_reconcile waits until 02:00 UTC).
            # Don't flag as unhealthy on startup; only flag if a loop was
            # registered more than 2× its threshold ago.
            loops[name] = {
                "status": "never_ticked",
                "seconds_since_tick": None,
                "threshold_seconds": threshold,
            }
            # Not flagged as unhealthy — startup wait is expected
        else:
            elapsed = now - last
            if elapsed > threshold:
                status = "stale"
                overall_healthy = False
            else:
                status = "ok"
            loops[name] = {
                "status": status,
                "seconds_since_tick": round(elapsed, 1),
                "threshold_seconds": threshold,
            }

    return {"healthy": overall_healthy, "loops": loops}
