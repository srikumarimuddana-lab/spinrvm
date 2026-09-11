"""Random first-tick offset for background loops.

Every loop body ticks first and sleeps after, and all of them are started in
the same instant, so without an offset the whole fleet fires together at boot
(and on every deploy) and loops sharing an interval stay phase-locked for the
life of the process. On the 2026-09-11 test ride Supabase's slowest requests
were exactly those aligned polls queueing on each other (3-9 s for
single-digit-ms SQL). Shared by core/lifespan.py (API process) and worker.py
(SPINR_PROCESS_ROLE=worker) so both runners de-phase the same way.
"""

from __future__ import annotations

import random
import re

# Upper bound on the random first-tick delay any loop gets. 30 s is enough to
# spread 41 loops into a non-colliding boot window while keeping the slowest
# first tick well under every loop's own cadence.
LOOP_START_OFFSET_MAX_SECONDS = 30.0

# Longest alternatives first so "min" is not read as "m" + leftover. Anchored
# only on the left so a trailing qualifier ("(1h, Sundays)") still parses.
_CADENCE_RE = re.compile(r"\((\d+)\s*(sec|min|s|m|h)")
_UNIT_SECONDS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}


def loop_start_offset_seconds(name: str, rng: random.Random | None = None) -> float:
    """Random first-tick delay for a background loop, from its registry name.

    Loop names carry their cadence — ``"route_finalizer (15s)"``,
    ``"surge_engine (2min)"``, ``"orphaned_hold_reconciler (15m)"``,
    ``"auto_payout (1h, Sundays)"`` — so the offset is bounded by the loop's
    own interval and never by more than ``LOOP_START_OFFSET_MAX_SECONDS``. A
    name whose cadence cannot be parsed (``"reconciliation (daily 02:00
    UTC)"``) gets the plain cap: those loops compute their own wall-clock
    schedule after they start, and a few seconds' start delay is irrelevant to
    them. Pure so it is unit-testable; the only randomness is the injected
    ``rng``.
    """
    match = _CADENCE_RE.search(name or "")
    cap = LOOP_START_OFFSET_MAX_SECONDS
    if match:
        interval_s = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        cap = min(cap, float(interval_s))
    if cap <= 0:
        return 0.0
    return (rng or random).uniform(0, cap)
