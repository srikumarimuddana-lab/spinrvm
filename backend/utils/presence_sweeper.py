"""
Presence sweeper — RETIRED.

Historically this loop reconciled ``drivers.is_online`` against Redis
presence by writing ``is_online=False`` for any "ghost online" driver.
That design assumed Redis was always authoritative across replicas. When
``REDIS_URL`` was unset (or briefly unreachable), each replica fell back
to its own in-process presence dict; the sweeper on replica B would not
see ``mark_present`` writes from replica A, conclude every driver was a
ghost, and mass-flip the persistent DB column. Driver apps that were
plainly online (foreground, location batches landing) ended up marked
offline several times a minute.

The lifespan startup no longer schedules ``presence_sweeper_loop`` (see
``backend/core/lifespan.py``). The new model — closer to how Uber and
Lyft run their dispatch — is:

  * ``drivers.is_online`` is **pure driver intent**. Only the driver
    tapping Go Online / Go Offline (or an admin force-off) ever writes
    it.
  * Reachability lives entirely in the Redis presence key. Dispatch and
    admin monitoring compose ``intent_online AND is_present`` at read
    time; no persistent flag is ever derived from a transient signal.
  * The intent timestamps ``went_online_at`` / ``went_offline_at`` (see
    migration 80) preserve the audit/analytics view that the sweeper
    used to keep "honest".

The functions below remain as documented no-ops so the loop-jitter
tests (``test_p3_loop_jitter_metrics``) keep their symbols and import
graph stable. The loop body is unreachable from production startup.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:  # pragma: no cover
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import set_gauge as _metric_gauge  # type: ignore

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60


async def _sweep_once() -> int:
    """No-op. Returns 0. See module docstring for the historical context."""
    return 0


async def presence_sweeper_loop() -> None:
    """Retained for test compatibility; no longer scheduled at startup.

    Preserves the original two-sleep shape (initial jitter + per-tick sleep)
    and the metric emissions so existing loop-jitter tests still exercise
    the same call sequence. The body of each tick is a no-op.
    """
    await asyncio.sleep(random.uniform(0, SWEEP_INTERVAL_SECONDS))
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[presence_sweeper] tick failed: {exc}", exc_info=True)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "presence_sweeper"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "presence_sweeper"})
        _record_heartbeat("presence_sweeper (60s)")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
