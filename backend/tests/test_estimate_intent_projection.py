"""Regression: /rides/estimate must project the columns intent_online reads.

The estimate candidate pool is filtered through _filter_reachable_drivers, which
keeps a driver only if `id in present AND intent_online(d)`. intent_online reads
went_online_at / went_offline_at and falls back to the is_online column. If the
driver query omits those three columns, intent_online sees every field as NULL
-> bool(None) -> False for EVERY driver, so the estimate drops the whole pool and
every vehicle type renders "No cars available" even with a live, in-range,
present driver. (Dispatch is unaffected: it presence-filters directly and never
calls intent_online.)
"""

from __future__ import annotations

import inspect
import re

from backend.utils.driver_online import intent_online


def test_estimate_driver_projection_includes_intent_columns():
    """The estimate's driver candidate fetch must select is_online +
    went_online_at + went_offline_at so intent_online can evaluate them."""
    from backend.routes import rides as rides_mod

    src = inspect.getsource(rides_mod.compute_ride_estimates)
    # The driver candidate projection is the columns="..." string that lists
    # destination_mode (distinct from the fare_config projections in the same fn).
    match = re.search(r'columns="([^"]*destination_mode[^"]*)"', src)
    assert match, "could not find the estimate driver candidate projection"
    projection = match.group(1)
    for col in ("is_online", "went_online_at", "went_offline_at"):
        assert col in projection, (
            f"estimate driver projection is missing '{col}' — _filter_reachable_drivers "
            f"calls intent_online(d), which needs it; without it every driver is dropped "
            f"and the rider sees 'No cars available'."
        )


def test_intent_online_is_false_when_intent_columns_absent():
    """Reproduces the bug: a driver row that omits is_online AND both intent
    timestamps (i.e. they were not SELECTed) evaluates to offline."""
    assert intent_online({"id": "d1", "lat": 50.4, "lng": -104.6}) is False


def test_intent_online_falls_back_to_is_online_when_timestamps_absent():
    """When is_online IS projected and the intent timestamps are absent,
    intent_online falls back to the column and keeps an online driver."""
    assert intent_online({"id": "d1", "is_online": True}) is True
    assert intent_online({"id": "d1", "is_online": False}) is False
