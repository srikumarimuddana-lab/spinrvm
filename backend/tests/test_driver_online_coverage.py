"""
Coverage for backend/utils/driver_online.py.

This module is the single source of truth for "is this driver online" reads
(see its module docstring) and underpins the documented invariant
``is_available ⇒ is_online``: dispatch and admin surfaces must never see a
driver as available without also being online. ``effective_available()``
composes ``effective_online()`` (which itself requires ``intent_online()``)
with "not on an active ride", so the invariant is structural here — every
test below that exercises ``effective_available`` also asserts the invariant
explicitly rather than just trusting the composition.

Covers the previously-missing lines:
  * 44      — _parse_ts: a tz-aware/naive ``datetime`` instance passed directly
  * 48-49   — _parse_ts: malformed string falls into the except branch -> None
  * 68      — intent_online: went_offline_at set, went_online_at absent -> False
  * 81-84   — effective_online: full body (not intent online / no id / present / absent)
  * 100     — effective_available: full body (online+free, online+busy, offline)
  * 108     — filter_effective_online: the list-comprehension body
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.utils.driver_online import (
    _parse_ts,
    effective_available,
    effective_online,
    filter_effective_online,
    intent_online,
)

# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


def test_parse_ts_none_and_empty_string_return_none():
    assert _parse_ts(None) is None
    assert _parse_ts("") is None


def test_parse_ts_naive_datetime_instance_gets_utc_attached():
    """Line 44, falsy branch: a naive datetime instance passed directly
    (not a string) is stamped with UTC rather than left naive."""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert naive.tzinfo is None
    parsed = _parse_ts(naive)
    assert parsed == naive.replace(tzinfo=timezone.utc)
    assert parsed.tzinfo is timezone.utc


def test_parse_ts_aware_datetime_instance_passed_through():
    """Line 44, truthy branch: an already tz-aware datetime is returned as-is."""
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_ts(aware) is aware


def test_parse_ts_iso_string_with_z_suffix_parses_as_utc():
    parsed = _parse_ts("2026-01-01T12:00:00Z")
    assert parsed == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_naive_iso_string_gets_utc_attached():
    parsed = _parse_ts("2026-01-01T12:00:00")
    assert parsed == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_malformed_string_returns_none():
    """Lines 48-49: garbage that fails datetime.fromisoformat() is swallowed
    and returns None rather than raising, per the module's documented
    contract ('callers must not rely on parser errors')."""
    assert _parse_ts("not-a-timestamp") is None
    assert _parse_ts("2026-13-99T99:99:99") is None


def test_parse_ts_non_string_non_datetime_value_returns_none():
    """Lines 48-49 via a value type str()-ifies to something unparsable."""
    assert _parse_ts(12345) is None
    assert _parse_ts(object()) is None


# ---------------------------------------------------------------------------
# intent_online
# ---------------------------------------------------------------------------


def test_intent_online_neither_timestamp_falls_back_to_is_online_column():
    assert intent_online({"id": "d1", "is_online": True}) is True
    assert intent_online({"id": "d1", "is_online": False}) is False
    assert intent_online({"id": "d1"}) is False


def test_intent_online_went_online_only_is_online():
    assert intent_online({"went_online_at": "2026-01-01T12:00:00Z"}) is True


def test_intent_online_went_offline_only_is_offline():
    """Line 68: went_offline_at set, went_online_at absent/None -> False,
    regardless of any legacy is_online column (a real toggle-to-offline
    always wins once recorded)."""
    driver = {
        "went_online_at": None,
        "went_offline_at": "2026-01-01T12:00:00Z",
        "is_online": True,  # stale legacy column must NOT override the toggle
    }
    assert intent_online(driver) is False


def test_intent_online_both_set_more_recent_online_wins():
    driver = {
        "went_online_at": "2026-01-01T12:00:00Z",
        "went_offline_at": "2026-01-01T10:00:00Z",
    }
    assert intent_online(driver) is True


def test_intent_online_both_set_more_recent_offline_wins():
    driver = {
        "went_online_at": "2026-01-01T10:00:00Z",
        "went_offline_at": "2026-01-01T12:00:00Z",
    }
    assert intent_online(driver) is False


def test_intent_online_both_set_equal_timestamps_defaults_offline():
    """Tie-break: `on_ts > off_ts` is strictly-greater, so an exact tie
    resolves to offline. This fails closed (never reports online on an
    ambiguous read), which is the safe direction for the
    is_available => is_online invariant."""
    same = "2026-01-01T12:00:00Z"
    driver = {"went_online_at": same, "went_offline_at": same}
    assert intent_online(driver) is False


# ---------------------------------------------------------------------------
# effective_online
# ---------------------------------------------------------------------------


def test_effective_online_false_when_not_intent_online():
    """Line 82: intent_online() False short-circuits before touching presence."""
    driver = {"id": "d1", "is_online": False}
    assert effective_online(driver, present_ids={"d1"}) is False


def test_effective_online_false_when_present_but_missing_id():
    """Line 83-84: intent online but no id on the row -> can't be present."""
    driver = {"is_online": True}
    assert effective_online(driver, present_ids={"d1"}) is False


def test_effective_online_false_when_intent_online_but_not_present():
    driver = {"id": "d1", "is_online": True}
    assert effective_online(driver, present_ids=set()) is False
    assert effective_online(driver, present_ids={"other-driver"}) is False


def test_effective_online_true_when_intent_online_and_present():
    driver = {"id": "d1", "is_online": True}
    assert effective_online(driver, present_ids={"d1", "d2"}) is True


# ---------------------------------------------------------------------------
# effective_available (and the is_available => is_online invariant)
# ---------------------------------------------------------------------------


def test_effective_available_true_when_online_present_and_free():
    driver = {"id": "d1", "is_online": True}
    present = {"d1"}
    available = effective_available(driver, present, on_active_ride=False)
    assert available is True
    # Invariant: is_available => is_online.
    assert effective_online(driver, present) is True


def test_effective_available_false_when_on_active_ride():
    """Line 100: online + present but currently on a ride -> not available.
    Still must not violate the invariant in the other direction (this says
    nothing about is_online, which stays True — only availability drops)."""
    driver = {"id": "d1", "is_online": True}
    present = {"d1"}
    assert effective_available(driver, present, on_active_ride=True) is False
    assert effective_online(driver, present) is True  # online, just busy


def test_effective_available_false_when_offline_even_if_free():
    driver = {"id": "d1", "is_online": False}
    present = {"d1"}
    available = effective_available(driver, present, on_active_ride=False)
    assert available is False
    # Invariant holds trivially: neither is true.
    assert effective_online(driver, present) is False


def test_effective_available_false_when_intent_online_but_absent_from_presence():
    """Redis presence missing (WS/heartbeat gap) must not report available,
    even though the durable intent column says online."""
    driver = {"id": "d1", "is_online": True}
    available = effective_available(driver, present_ids=set(), on_active_ride=False)
    assert available is False


def test_effective_available_never_true_without_effective_online_across_matrix():
    """Invariant sweep: for every combination of intent/presence/active-ride,
    effective_available(...) is True implies effective_online(...) is True."""
    drivers = [
        {"id": "d1", "is_online": True},
        {"id": "d1", "is_online": False},
        {"id": "d1"},
    ]
    present_options = [set(), {"d1"}, {"other"}]
    for driver in drivers:
        for present in present_options:
            for on_active_ride in (True, False):
                available = effective_available(driver, present, on_active_ride=on_active_ride)
                online = effective_online(driver, present)
                if available:
                    assert online, (
                        "is_available => is_online invariant violated for "
                        f"driver={driver} present={present} on_active_ride={on_active_ride}"
                    )


# ---------------------------------------------------------------------------
# filter_effective_online
# ---------------------------------------------------------------------------


def test_filter_effective_online_keeps_only_online_and_present_rows():
    """Line 108: the list-comprehension body itself."""
    drivers = [
        {"id": "d1", "is_online": True},  # online + present -> kept
        {"id": "d2", "is_online": True},  # online but not present -> dropped
        {"id": "d3", "is_online": False},  # present but offline -> dropped
        {"id": "d4", "is_online": True},  # online + present -> kept
    ]
    present_ids = {"d1", "d3", "d4"}
    result = filter_effective_online(drivers, present_ids)
    assert [d["id"] for d in result] == ["d1", "d4"]


def test_filter_effective_online_empty_input_returns_empty_list():
    assert filter_effective_online([], present_ids={"d1"}) == []
    assert filter_effective_online([], present_ids=set()) == []
