"""Tests for utils/driver_online.py — the effective-online composition helper.

This module is the single source of truth for "is this driver online / available"
reads across dispatch and admin. CLAUDE.md documents a hard invariant:

    is_available ⇒ is_online   (the inverse does NOT hold)

Every test in the "invariant" section below exists to guard that specific
property — a driver must never be reported available while not online. Losing
that invariant would mean dispatch offers rides to a driver the app itself
doesn't think is online, which is the exact class of bug the retired
presence_sweeper introduced by mass-flipping drivers offline on a Redis blip.

Two source-of-truth halves are composed here:
  * intent_online() — durable Postgres columns (went_online_at/went_offline_at,
    with a legacy is_online fallback for un-migrated rows)
  * effective_online()/effective_available() — intent AND Redis-backed
    reachability (present_ids), plus (for available) "not on an active ride"
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.utils.driver_online import (
    _parse_ts,
    effective_available,
    effective_online,
    filter_effective_online,
    intent_online,
)

_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
_EARLIER = _NOW - timedelta(hours=2)
_LATER = _NOW + timedelta(hours=2)


# ── intent_online: timestamp composition ────────────────────────────


def test_intent_online_true_when_only_went_online_at_set():
    driver = {"went_online_at": _NOW.isoformat(), "went_offline_at": None}
    assert intent_online(driver) is True


def test_intent_online_false_when_only_went_offline_at_set():
    driver = {"went_online_at": None, "went_offline_at": _NOW.isoformat()}
    assert intent_online(driver) is False


def test_intent_online_both_set_online_more_recent_wins():
    driver = {"went_online_at": _LATER.isoformat(), "went_offline_at": _NOW.isoformat()}
    assert intent_online(driver) is True


def test_intent_online_both_set_offline_more_recent_wins():
    driver = {"went_online_at": _NOW.isoformat(), "went_offline_at": _LATER.isoformat()}
    assert intent_online(driver) is False


def test_intent_online_equal_timestamps_offline_wins_not_greater_than():
    """`on_ts > off_ts` is a strict inequality — a tie (e.g. a bad backfill
    writing identical stamps) must not be treated as "just went online"."""
    driver = {"went_online_at": _NOW.isoformat(), "went_offline_at": _NOW.isoformat()}
    assert intent_online(driver) is False


@pytest.mark.parametrize("legacy_is_online", [True, False])
def test_intent_online_falls_back_to_legacy_is_online_when_neither_timestamp_set(legacy_is_online):
    """Un-migrated rows (migration 97 backfill not yet run for this driver)
    must behave exactly as before the backfill: read is_online directly."""
    driver = {"went_online_at": None, "went_offline_at": None, "is_online": legacy_is_online}
    assert intent_online(driver) is legacy_is_online


def test_intent_online_falls_back_when_is_online_key_missing_entirely():
    driver = {"went_online_at": None, "went_offline_at": None}
    assert intent_online(driver) is False


def test_intent_online_empty_string_timestamps_treated_as_unset():
    """PostgREST can hand back empty strings rather than NULL for some drivers;
    _parse_ts must treat that as "not set", not as a malformed-but-present value."""
    driver = {"went_online_at": "", "went_offline_at": "", "is_online": True}
    assert intent_online(driver) is True


def test_intent_online_malformed_online_timestamp_falls_through_to_offline_only():
    """A malformed went_online_at parses to None, which is the same code path
    as "never toggled online" — off_ts set means offline."""
    driver = {"went_online_at": "not-a-timestamp", "went_offline_at": _NOW.isoformat()}
    assert intent_online(driver) is False


# ── _parse_ts edge cases ─────────────────────────────────────────────


def test_parse_ts_none_and_empty_string_return_none():
    assert _parse_ts(None) is None
    assert _parse_ts("") is None


def test_parse_ts_malformed_string_returns_none_not_raise():
    assert _parse_ts("definitely-not-a-timestamp") is None


def test_parse_ts_accepts_z_suffix_iso_format():
    parsed = _parse_ts("2026-08-02T12:00:00Z")
    assert parsed == _NOW


def test_parse_ts_naive_datetime_object_gets_utc_attached():
    naive = datetime(2026, 8, 2, 12, 0, 0)
    parsed = _parse_ts(naive)
    assert parsed.tzinfo is not None
    assert parsed == _NOW


def test_parse_ts_tz_aware_datetime_object_passthrough():
    parsed = _parse_ts(_NOW)
    assert parsed == _NOW


def test_parse_ts_naive_iso_string_gets_utc_attached():
    parsed = _parse_ts("2026-08-02T12:00:00")
    assert parsed == _NOW


# ── effective_online: intent AND presence ────────────────────────────


@pytest.mark.parametrize(
    "intent_kwargs,present_ids,expected",
    [
        ({"went_online_at": _NOW.isoformat()}, {"d1"}, True),
        ({"went_online_at": _NOW.isoformat()}, set(), False),
        ({"went_offline_at": _NOW.isoformat()}, {"d1"}, False),
        ({"went_offline_at": _NOW.isoformat()}, set(), False),
    ],
)
def test_effective_online_requires_both_intent_and_presence(intent_kwargs, present_ids, expected):
    driver = {"id": "d1", **intent_kwargs}
    assert effective_online(driver, present_ids) is expected


def test_effective_online_false_when_driver_has_no_id_even_if_intent_online():
    driver = {"went_online_at": _NOW.isoformat()}
    assert effective_online(driver, {"d1"}) is False


def test_effective_online_short_circuits_before_touching_present_ids():
    """intent_online is checked first — a driver who intends offline is
    offline regardless of what present_ids says (defends against a present_ids
    set that is stale/wrong from an upstream bug)."""
    driver = {"id": "d1", "went_offline_at": _NOW.isoformat()}
    assert effective_online(driver, {"d1"}) is False


# ── effective_available: online AND not on an active ride ────────────


@pytest.mark.parametrize(
    "online_kwargs,present_ids,on_active_ride,expected",
    [
        ({"went_online_at": _NOW.isoformat()}, {"d1"}, False, True),
        ({"went_online_at": _NOW.isoformat()}, {"d1"}, True, False),
        ({"went_online_at": _NOW.isoformat()}, set(), False, False),
        ({"went_offline_at": _NOW.isoformat()}, {"d1"}, False, False),
        ({"went_offline_at": _NOW.isoformat()}, {"d1"}, True, False),
    ],
)
def test_effective_available_composition(online_kwargs, present_ids, on_active_ride, expected):
    driver = {"id": "d1", **online_kwargs}
    assert effective_available(driver, present_ids, on_active_ride=on_active_ride) is expected


# ── filter_effective_online ──────────────────────────────────────────


def test_filter_effective_online_keeps_only_online_and_present_drivers():
    drivers = [
        {"id": "online-and-present", "went_online_at": _NOW.isoformat()},
        {"id": "online-not-present", "went_online_at": _NOW.isoformat()},
        {"id": "offline", "went_offline_at": _NOW.isoformat()},
    ]
    present_ids = {"online-and-present"}

    result = filter_effective_online(drivers, present_ids)

    assert [d["id"] for d in result] == ["online-and-present"]


def test_filter_effective_online_empty_input_returns_empty_list():
    assert filter_effective_online([], set()) == []


# ── the invariant: is_available ⇒ is_online (never the reverse) ─────


def _driver_variants():
    """Every combination of intent state this module can compose from."""
    return [
        ("intent_online_via_timestamp", {"id": "d1", "went_online_at": _NOW.isoformat()}),
        ("intent_offline_via_timestamp", {"id": "d1", "went_offline_at": _NOW.isoformat()}),
        ("intent_online_via_legacy_flag", {"id": "d1", "is_online": True}),
        ("intent_offline_via_legacy_flag", {"id": "d1", "is_online": False}),
        ("intent_offline_no_flags_at_all", {"id": "d1"}),
    ]


@pytest.mark.parametrize("label,driver", _driver_variants())
@pytest.mark.parametrize("present_ids", [set(), {"d1"}], ids=["absent", "present"])
@pytest.mark.parametrize("on_active_ride", [False, True], ids=["free", "on_active_ride"])
def test_invariant_available_implies_online_across_every_state_combo(label, driver, present_ids, on_active_ride):
    """CLAUDE.md: 'the invariant is_available ⇒ is_online must hold; the inverse
    does not.' Exercise every (intent × presence × active-ride) combination this
    module can produce and assert the forward implication holds in each one.

    This must never observe available=True with online=False — that state would
    mean dispatch could offer a ride to a driver nothing else considers online.
    """
    online = effective_online(driver, present_ids)
    available = effective_available(driver, present_ids, on_active_ride=on_active_ride)

    # The invariant itself: available implies online.
    assert not (available and not online), (
        f"INVARIANT VIOLATION for {label} (present_ids={present_ids!r}, "
        f"on_active_ride={on_active_ride}): effective_available=True but "
        f"effective_online=False"
    )

    # available is available only when: online, present, and free — verify the
    # converse doesn't silently drift, i.e. available is never True without the
    # driver actually being present too (a stricter sibling of the invariant
    # that would also break the ⇒ property if it regressed).
    if available:
        assert driver.get("id") in present_ids
        assert on_active_ride is False


def test_invariant_online_true_does_not_imply_available_true():
    """The documented inverse-does-NOT-hold case: a driver can be online
    (intent + present) yet unavailable because they're mid-trip."""
    driver = {"id": "d1", "went_online_at": _NOW.isoformat()}
    present_ids = {"d1"}

    online = effective_online(driver, present_ids)
    available = effective_available(driver, present_ids, on_active_ride=True)

    assert online is True
    assert available is False
