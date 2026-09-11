"""loop_start_offset_seconds — the one-off random first-tick delay every
background loop gets from core.lifespan._restartable.

Why it exists: every loop body ticks first and sleeps after, so all 41 loops
fired in the same second at boot and same-interval loops stayed phase-locked
for the life of the process. On the 2026-09-11 test ride Supabase's slowest
requests were exactly those aligned polls queueing on each other (3–9 s for
single-digit-ms SQL). The offset must be bounded by each loop's own cadence
and by a global cap, deterministic under an injected RNG, and never negative.
"""

from __future__ import annotations

import random

import pytest

from utils.loop_start_offset import LOOP_START_OFFSET_MAX_SECONDS, loop_start_offset_seconds


@pytest.mark.parametrize(
    "name, interval_s",
    [
        ("route_finalizer (15s)", 15),
        ("route_gap_monitor (15s)", 15),
        ("safety_checkin (30s)", 30),
        ("scheduled_dispatcher (60s)", 60),
        ("surge_engine (2min)", 120),
        ("payment_retry (5min)", 300),
        ("corporate_low_balance (1h)", 3600),
        ("retention_purge (24h)", 86400),
        ("orphaned_hold_reconciler (15m)", 900),
        ("auto_payout (1h, Sundays)", 3600),
        ("offer_expiry_reaper (10s)", 10),
    ],
)
def test_offset_is_bounded_by_the_loops_own_cadence_and_the_global_cap(name, interval_s):
    rng = random.Random(1234)
    cap = min(interval_s, LOOP_START_OFFSET_MAX_SECONDS)
    for _ in range(200):
        offset = loop_start_offset_seconds(name, rng)
        assert 0 <= offset <= cap, (name, offset)


def test_short_loops_never_wait_longer_than_one_of_their_own_intervals():
    """A 15 s loop delayed by up to 30 s would skip a whole tick at boot —
    the cap must be its interval, not the global maximum."""
    rng = random.Random(7)
    assert max(loop_start_offset_seconds("route_finalizer (15s)", rng) for _ in range(500)) <= 15


def test_unparseable_cadence_falls_back_to_the_global_cap():
    rng = random.Random(99)
    for name in ("reconciliation (daily 02:00 UTC)", "distance_reconciliation (daily 04:00 UTC)", "watchdog", ""):
        for _ in range(100):
            assert 0 <= loop_start_offset_seconds(name, rng) <= LOOP_START_OFFSET_MAX_SECONDS


def test_offset_is_deterministic_under_an_injected_rng():
    a = loop_start_offset_seconds("surge_engine (2min)", random.Random(42))
    b = loop_start_offset_seconds("surge_engine (2min)", random.Random(42))
    assert a == b


def test_two_loops_with_the_same_cadence_are_dephased():
    """The whole point: the two 15 s loops should not land on the same phase.
    With a real RNG the odds of a collision within 100 ms are ~1.3 % per pair;
    a fixed seed makes this a deterministic regression check."""
    rng = random.Random(2026)
    a = loop_start_offset_seconds("route_finalizer (15s)", rng)
    b = loop_start_offset_seconds("route_gap_monitor (15s)", rng)
    assert abs(a - b) > 0.1
