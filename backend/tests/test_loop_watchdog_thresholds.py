"""Regression guard for REL-001 / ROADMAP N17: every loop the watchdog
covers must have an explicit, tuned `LOOP_THRESHOLDS` entry — not the
generic 2h `_DEFAULT_THRESHOLD` fallback, which is either far too loose
(a hung 10-30s safety/dispatch loop) or simply untuned for the rest.

Scoped to `_WATCHDOG_LOOP_NAMES`-equivalent coverage: every name
`active_api_loop_names("all")` returns (every LOOP_CATALOG entry except the
watchdog itself, which watches the others and is intentionally excluded
from its own list — see core/lifespan.py's watchdog-registration comment).
"""

from __future__ import annotations

import pytest

from backend.core.background_loop_registry import LOOP_WATCHDOG_NAME, active_api_loop_names
from backend.utils.loop_monitor import _DEFAULT_THRESHOLD, LOOP_THRESHOLDS

pytestmark = pytest.mark.unit


def _watchable_loop_names() -> list[str]:
    return [name for name in active_api_loop_names("all") if name != LOOP_WATCHDOG_NAME]


def test_every_watched_loop_has_an_explicit_threshold():
    """A future loop added to LOOP_CATALOG without a matching LOOP_THRESHOLDS
    entry fails this test immediately, instead of silently falling back to
    the 2h default (REL-001's core finding)."""
    missing = sorted(name for name in _watchable_loop_names() if name not in LOOP_THRESHOLDS)
    assert missing == [], f"loops with no explicit LOOP_THRESHOLDS entry (falls back to 2h default): {missing}"


def test_no_threshold_equals_the_generic_default_by_accident():
    """Catches a threshold that was added but happens to equal the 2h
    default verbatim — almost certainly a copy-paste that skipped actually
    tuning the value to the loop's real cadence."""
    accidental_defaults = sorted(
        name for name in _watchable_loop_names() if LOOP_THRESHOLDS.get(name) == _DEFAULT_THRESHOLD
    )
    assert accidental_defaults == [], (
        f"loops whose explicit threshold equals the untuned 2h default: {accidental_defaults}"
    )


def test_t4a_annual_job_has_a_tuned_threshold():
    """Pinned explicitly: REL-001's Direction C loop (t4a_annual_job) had no
    threshold at all before this change, on top of never heartbeating."""
    assert "t4a_annual_job (yearly Feb 28)" in LOOP_THRESHOLDS
    assert LOOP_THRESHOLDS["t4a_annual_job (yearly Feb 28)"] < _DEFAULT_THRESHOLD


@pytest.mark.parametrize(
    "name",
    [
        "safety_checkin (30s)",
        "route_deviation_alerter (30s)",
        "offer_expiry_reaper (10s)",
        "driver_readiness_reconciler (20s)",
        "driver_claim_reaper (60s)",
        "stuck_ride_sweeper (60s)",
    ],
)
def test_safety_and_dispatch_loops_are_tuned_well_below_the_default(name):
    """The priority group per ROADMAP N17: a hung safety/dispatch loop must
    not be able to hide behind the old 2h default."""
    assert name in LOOP_THRESHOLDS
    assert LOOP_THRESHOLDS[name] <= 5 * 60, f"{name} threshold is not cadence-proportionate: {LOOP_THRESHOLDS[name]}"
