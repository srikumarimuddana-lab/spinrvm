"""
A1c Sub-tier C coverage: backend/utils/driver_online.py (70% -> target 100%).

No prior dedicated test file for this module existed (confirmed via
`grep -rl "driver_online" backend/tests/*.py` matching only incidental
string hits in unrelated files, not a real test file). This module is the
Uber/Lyft-style dispatch-critical intent/reachability composition that
replaced the retired `presence_sweeper` — every reader that asks "is this
driver online?" is meant to route through here, so its branch coverage
matters for dispatch correctness even though the functions are pure.

Closes:

- `_parse_ts`: None/empty -> None, naive vs. tz-aware datetime passthrough,
  ISO string with `Z` suffix, and the malformed-value `except` swallow.
- `intent_online`: all four composition branches (both set -> most-recent-
  wins in both directions; only one set; neither set -> legacy `is_online`
  fallback).
- `effective_online`: intent offline short-circuits before checking
  presence; intent online but not present; intent online and present;
  missing `id` on the driver row.
- `effective_available`: online+present+not-on-ride -> available;
  online+present+on-ride -> not available; not online -> not available
  regardless of `on_active_ride`.
- `filter_effective_online`: filters a mixed list correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from utils.driver_online import (
    _parse_ts,
    effective_available,
    effective_online,
    filter_effective_online,
    intent_online,
)

# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


class TestParseTs:
    def test_none_returns_none(self):
        assert _parse_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_naive_datetime_gets_utc_attached(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = _parse_ts(naive)
        assert result.tzinfo is not None

    def test_tz_aware_datetime_passthrough(self):
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert _parse_ts(aware) is aware

    def test_iso_string_with_z_suffix(self):
        result = _parse_ts("2026-01-01T12:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_malformed_string_returns_none(self):
        assert _parse_ts("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# intent_online
# ---------------------------------------------------------------------------


class TestIntentOnline:
    def test_neither_set_falls_back_to_legacy_is_online_true(self):
        assert intent_online({"is_online": True}) is True

    def test_neither_set_falls_back_to_legacy_is_online_false(self):
        assert intent_online({"is_online": False}) is False

    def test_neither_set_missing_legacy_defaults_false(self):
        assert intent_online({}) is False

    def test_only_online_set_is_online(self):
        assert intent_online({"went_online_at": "2026-01-01T00:00:00Z"}) is True

    def test_only_offline_set_is_offline(self):
        assert intent_online({"went_offline_at": "2026-01-01T00:00:00Z"}) is False

    def test_both_set_online_more_recent_wins(self):
        driver = {
            "went_online_at": "2026-01-02T00:00:00Z",
            "went_offline_at": "2026-01-01T00:00:00Z",
        }
        assert intent_online(driver) is True

    def test_both_set_offline_more_recent_wins(self):
        driver = {
            "went_online_at": "2026-01-01T00:00:00Z",
            "went_offline_at": "2026-01-02T00:00:00Z",
        }
        assert intent_online(driver) is False


# ---------------------------------------------------------------------------
# effective_online
# ---------------------------------------------------------------------------


class TestEffectiveOnline:
    def test_intent_offline_short_circuits_before_presence_check(self):
        driver = {"id": "d1", "is_online": False}
        assert effective_online(driver, present_ids={"d1"}) is False

    def test_intent_online_but_not_present(self):
        driver = {"id": "d1", "is_online": True}
        assert effective_online(driver, present_ids=set()) is False

    def test_intent_online_and_present(self):
        driver = {"id": "d1", "is_online": True}
        assert effective_online(driver, present_ids={"d1"}) is True

    def test_missing_driver_id_is_never_effective_online(self):
        driver = {"is_online": True}
        assert effective_online(driver, present_ids={"d1"}) is False


# ---------------------------------------------------------------------------
# effective_available
# ---------------------------------------------------------------------------


class TestEffectiveAvailable:
    def test_online_present_not_on_ride_is_available(self):
        driver = {"id": "d1", "is_online": True}
        assert effective_available(driver, present_ids={"d1"}, on_active_ride=False) is True

    def test_online_present_on_ride_is_not_available(self):
        driver = {"id": "d1", "is_online": True}
        assert effective_available(driver, present_ids={"d1"}, on_active_ride=True) is False

    def test_not_online_never_available_regardless_of_ride_flag(self):
        driver = {"id": "d1", "is_online": False}
        assert effective_available(driver, present_ids={"d1"}, on_active_ride=False) is False


# ---------------------------------------------------------------------------
# filter_effective_online
# ---------------------------------------------------------------------------


class TestFilterEffectiveOnline:
    def test_filters_mixed_list(self):
        drivers = [
            {"id": "d1", "is_online": True},  # online + present
            {"id": "d2", "is_online": True},  # online + not present
            {"id": "d3", "is_online": False},  # not online
        ]
        result = filter_effective_online(drivers, present_ids={"d1"})
        assert [d["id"] for d in result] == ["d1"]
