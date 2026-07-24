"""Tests for the completion milestone-sanity check (_trip_window_compression).

Flags rides whose in_progress window is far shorter than the quoted duration —
the incident's shape (a ~4-minute window on a ~8-minute ride). Detection only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from backend.routes.drivers.ride_complete import _trip_window_compression
except ImportError:
    from routes.drivers.ride_complete import _trip_window_compression  # type: ignore

_START = datetime(2026, 7, 22, 21, 21, tzinfo=timezone.utc)


def test_compressed_window_is_flagged():
    # Quoted 8 min, actual window 4 min → ratio 0.5? No: 4/8 = 0.5 > 0.4 → NOT
    # flagged. Use a 2.5-min window (0.31) to trip the 0.4 threshold.
    details = _trip_window_compression(8, _START, _START + timedelta(minutes=2, seconds=30))
    assert details is not None
    assert details["quoted_duration_minutes"] == 8
    assert details["actual_window_minutes"] == 2.5
    assert details["ratio"] == 0.312


def test_normal_window_not_flagged():
    details = _trip_window_compression(8, _START, _START + timedelta(minutes=7))
    assert details is None


def test_window_at_threshold_not_flagged():
    # Exactly 0.4 ratio is not "far shorter" — boundary is exclusive.
    details = _trip_window_compression(10, _START, _START + timedelta(minutes=4))
    assert details is None


def test_missing_inputs_are_noops():
    assert _trip_window_compression(0, _START, _START + timedelta(minutes=1)) is None
    assert _trip_window_compression(8, None, _START) is None
    assert _trip_window_compression(8, _START, None) is None


def test_negative_window_ignored():
    # completed before started (clock skew) → not a valid signal.
    assert _trip_window_compression(8, _START, _START - timedelta(minutes=1)) is None
