"""
Unit tests for scripts/threat-hunting/anomaly_detection.py (Phase 4b of the
2026-09-19 security automation roadmap), using mocked auth/payment event
data -- no live data source is connected, so these fixtures stand in for a
real Sentry/Supabase query until one is wired in (see the module
docstring's "Wiring in real data" section).

Runs directly:

    pytest scripts/threat-hunting/test_anomaly_detection.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anomaly_detection import (  # noqa: E402
    _assert_safe_event,
    detect_auth_bursts,
    detect_card_testing_rings,
    detect_payment_retry_storms,
    render_markdown,
)


def _auth_event(ts, device_id=None, ip_hash=None, phone_last4=None, outcome="otp_failed"):
    return {"event_id": f"e-{ts}", "timestamp": ts, "outcome": outcome, "device_id": device_id, "ip_hash": ip_hash, "phone_last4": phone_last4}


def _payment_event(ts, card_fingerprint="fp_abc123", outcome="declined", device_id=None, ip_hash=None):
    return {"event_id": f"p-{ts}", "timestamp": ts, "outcome": outcome, "card_fingerprint": card_fingerprint, "device_id": device_id, "ip_hash": ip_hash}


def test_assert_safe_event_rejects_raw_ip():
    with pytest.raises(ValueError, match="raw IP"):
        _assert_safe_event({"ip_hash": "192.168.1.1"})


def test_assert_safe_event_accepts_hashed_ip():
    _assert_safe_event({"ip_hash": "sha256:abcdef1234567890"})  # should not raise


def test_assert_safe_event_rejects_full_phone():
    with pytest.raises(ValueError, match="full phone number"):
        _assert_safe_event({"phone_last4": "13065551234"})


def test_assert_safe_event_rejects_formatted_full_phone():
    """A real phone string is normally punctuated, not bare digits --
    the guard must catch these formats too, not just "13065551234"."""
    for formatted in ["306-555-1234", "(306) 555-1234", "+1 306 555 1234"]:
        with pytest.raises(ValueError, match="full phone number"):
            _assert_safe_event({"phone_last4": formatted})


def test_assert_safe_event_accepts_last4():
    _assert_safe_event({"phone_last4": "1234"})  # should not raise


def test_assert_safe_event_rejects_raw_ip_in_device_id_or_user_id():
    """The guard covers every identifier field it accepts, not just
    ip_hash/phone_last4 -- a caller could mistakenly pass raw PII into
    device_id/user_id/card_fingerprint instead."""
    with pytest.raises(ValueError):
        _assert_safe_event({"device_id": "10.0.0.5"})
    with pytest.raises(ValueError):
        _assert_safe_event({"user_id": "306-555-1234"})


def test_assert_safe_event_accepts_opaque_ids_in_all_fields():
    _assert_safe_event({"device_id": "dev-abc-123", "user_id": "user_xyz", "card_fingerprint": "fp_abc123"})


def test_detect_auth_bursts_flags_many_distinct_targets_from_one_device():
    events = [
        _auth_event("2026-09-19T10:00:00Z", device_id="dev-1", phone_last4="1111"),
        _auth_event("2026-09-19T10:00:10Z", device_id="dev-1", phone_last4="2222"),
        _auth_event("2026-09-19T10:00:20Z", device_id="dev-1", phone_last4="3333"),
        _auth_event("2026-09-19T10:00:30Z", device_id="dev-1", phone_last4="4444"),
        _auth_event("2026-09-19T10:00:40Z", device_id="dev-1", phone_last4="5555"),
    ]
    anomalies = detect_auth_bursts(events, window_seconds=300, distinct_target_threshold=5)
    assert len(anomalies) == 1
    assert anomalies[0]["key"] == "dev-1"
    assert anomalies[0]["key_type"] == "device_id"
    assert anomalies[0]["distinct_targets"] == 5


def test_detect_auth_bursts_ignores_repeated_failures_against_same_target():
    """A user mistyping their own OTP repeatedly must not be flagged --
    only many DISTINCT targets from one device/IP counts as a burst."""
    events = [_auth_event(f"2026-09-19T10:00:0{i}Z", device_id="dev-2", phone_last4="1111") for i in range(6)]
    anomalies = detect_auth_bursts(events, window_seconds=300, distinct_target_threshold=5)
    assert anomalies == []


def test_detect_auth_bursts_ignores_events_outside_window():
    events = [
        _auth_event("2026-09-19T10:00:00Z", device_id="dev-3", phone_last4="1111"),
        _auth_event("2026-09-19T10:10:00Z", device_id="dev-3", phone_last4="2222"),
        _auth_event("2026-09-19T10:20:00Z", device_id="dev-3", phone_last4="3333"),
        _auth_event("2026-09-19T10:30:00Z", device_id="dev-3", phone_last4="4444"),
        _auth_event("2026-09-19T10:40:00Z", device_id="dev-3", phone_last4="5555"),
    ]
    # 5 distinct targets but spread over 40 minutes, window is 5 minutes.
    anomalies = detect_auth_bursts(events, window_seconds=300, distinct_target_threshold=5)
    assert anomalies == []


def test_detect_auth_bursts_ignores_successful_otp():
    events = [_auth_event(f"2026-09-19T10:00:0{i}Z", device_id="dev-4", phone_last4=str(i) * 4, outcome="otp_success") for i in range(6)]
    assert detect_auth_bursts(events) == []


def test_detect_auth_bursts_groups_by_ip_hash_when_no_device_id():
    events = [
        _auth_event(f"2026-09-19T10:00:0{i}Z", ip_hash="sha256:deadbeef", phone_last4=str(i) * 4)
        for i in range(5)
    ]
    anomalies = detect_auth_bursts(events, distinct_target_threshold=5)
    assert len(anomalies) == 1
    assert anomalies[0]["key_type"] == "ip_hash"


def test_detect_auth_bursts_catches_randomized_device_id_via_stable_ip():
    """Evasion case a spinr-fraud-auditor review found: an attacker
    randomizing device_id per request while ip_hash stays constant must
    still be caught via the ip_hash grouping, not missed because device_id
    was checked first / instead."""
    events = [
        _auth_event(f"2026-09-19T10:00:0{i}Z", device_id=f"random-dev-{i}", ip_hash="sha256:stableip", phone_last4=str(i) * 4)
        for i in range(5)
    ]
    anomalies = detect_auth_bursts(events, distinct_target_threshold=5)
    ip_anomalies = [a for a in anomalies if a.get("key_type") == "ip_hash"]
    assert len(ip_anomalies) == 1
    assert ip_anomalies[0]["key"] == "sha256:stableip"


def test_detect_auth_bursts_reports_unattributed_events_not_silently_dropped():
    events = [_auth_event(f"2026-09-19T10:00:0{i}Z", phone_last4=str(i) * 4) for i in range(3)]  # no device_id, no ip_hash
    anomalies = detect_auth_bursts(events)
    unattributed = [a for a in anomalies if a.get("kind") == "unattributed_failures"]
    assert len(unattributed) == 1
    assert unattributed[0]["event_count"] == 3


def test_detect_auth_bursts_raises_on_unsafe_input():
    events = [_auth_event("2026-09-19T10:00:00Z", ip_hash="10.0.0.1")]
    with pytest.raises(ValueError):
        detect_auth_bursts(events)


def test_detect_payment_retry_storms_flags_many_declines_one_card():
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z") for i in range(6)]
    anomalies = detect_payment_retry_storms(events, window_seconds=600, failure_threshold=5)
    assert len(anomalies) == 1
    assert anomalies[0]["card_fingerprint"] == "fp_abc123"
    assert anomalies[0]["decline_count"] == 6


def test_detect_payment_retry_storms_ignores_successful_payments():
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z", outcome="succeeded") for i in range(6)]
    assert detect_payment_retry_storms(events) == []


def test_detect_payment_retry_storms_below_threshold_not_flagged():
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z") for i in range(3)]
    assert detect_payment_retry_storms(events, failure_threshold=5) == []


def test_detect_card_testing_rings_flags_many_distinct_cards_one_ip():
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z", card_fingerprint=f"fp_{i}", ip_hash="sha256:ringip") for i in range(5)]
    anomalies = detect_card_testing_rings(events, distinct_card_threshold=5)
    assert len(anomalies) == 1
    assert anomalies[0]["key"] == "sha256:ringip"
    assert anomalies[0]["distinct_cards"] == 5


def test_detect_card_testing_rings_ignores_single_card_repeated():
    """A single card retried many times is detect_payment_retry_storms's
    job, not the ring detector's -- distinct-card count stays at 1 here."""
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z", card_fingerprint="fp_same", ip_hash="sha256:x") for i in range(6)]
    assert detect_card_testing_rings(events, distinct_card_threshold=5) == []


def test_detect_card_testing_rings_below_threshold_not_flagged():
    events = [_payment_event(f"2026-09-19T10:00:0{i}Z", card_fingerprint=f"fp_{i}", ip_hash="sha256:x") for i in range(3)]
    assert detect_card_testing_rings(events, distinct_card_threshold=5) == []


def test_render_markdown_no_anomalies():
    report = render_markdown([], [], "test window")
    assert "No anomalies found" in report


def test_render_markdown_includes_both_anomaly_types():
    auth = [{"kind": "auth_burst", "key_type": "device_id", "key": "dev-1", "window_start": "t0", "window_seconds": 300, "distinct_targets": 5, "event_count": 5}]
    payment = [{"kind": "payment_retry_storm", "card_fingerprint": "fp_1", "window_start": "t0", "window_seconds": 600, "decline_count": 6}]
    report = render_markdown(auth, payment, "test window")
    assert "dev-1" in report
    assert "fp_1" in report
    assert "credential stuffing" in report
    assert "card testing" in report


def test_render_markdown_includes_card_testing_ring_and_unattributed():
    auth = [{"kind": "unattributed_failures", "event_count": 3, "note": "no key"}]
    payment = [{"kind": "card_testing_ring", "key_type": "ip_hash", "key": "sha256:x", "window_start": "t0", "window_seconds": 600, "distinct_cards": 6, "event_count": 6}]
    report = render_markdown(auth, payment, "test window")
    assert "3 failed attempt" in report
    assert "sha256:x" in report
    assert "6 distinct cards" in report
