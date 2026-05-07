"""Unit tests for corporate_policy_service.evaluate_policy.

Pure-function tests — no DB, no mocks needed.
"""

from services.corporate_policy_service import evaluate_policy

# ── helpers ──────────────────────────────────────────────────────────────────


def _ok(result):
    assert result["pass"] is True, f"Expected pass but got failed_rules={result['failed_rules']}"


def _fail(result, *rules):
    assert result["pass"] is False
    for r in rules:
        assert r in result["failed_rules"], f"Expected {r!r} in {result['failed_rules']}"


# ── empty / null policy ───────────────────────────────────────────────────────


def test_empty_policy_always_passes():
    _ok(evaluate_policy({}, {"estimated_fare": 999}))


def test_none_policy_treated_as_empty():
    # callers may pass {} when DB returns None
    _ok(evaluate_policy({}, {}))


# ── Rule 1: max_fare_per_ride ─────────────────────────────────────────────────


def test_max_fare_allows_cheap_ride():
    policy = {"max_fare_per_ride": 50}
    _ok(evaluate_policy(policy, {"estimated_fare": 49.99}))


def test_max_fare_allows_exact_fare():
    policy = {"max_fare_per_ride": 50}
    _ok(evaluate_policy(policy, {"estimated_fare": 50.0}))


def test_max_fare_blocks_expensive_ride():
    policy = {"max_fare_per_ride": 50}
    _fail(evaluate_policy(policy, {"estimated_fare": 50.01}), "max_fare_per_ride")


def test_max_fare_uses_final_fare_at_completion():
    policy = {"max_fare_per_ride": 30}
    _fail(evaluate_policy(policy, {"final_fare": 35}), "max_fare_per_ride")


def test_max_fare_absent_never_fails():
    _ok(evaluate_policy({"allowed_time_windows": None}, {"estimated_fare": 9999}))


# ── Rule 2: time_window ───────────────────────────────────────────────────────


def test_time_window_passes_when_no_windows_set():
    policy = {"allowed_time_windows": []}
    _ok(evaluate_policy(policy, {"pickup_time": "2026-04-14T10:00:00"}))


def test_time_window_passes_within_window():
    # 2026-04-20 is a Monday (weekday index 0)
    policy = {"allowed_time_windows": [{"day": "mon", "start": "08:00", "end": "18:00"}]}
    _ok(evaluate_policy(policy, {"pickup_time": "2026-04-20T12:00:00"}))


def test_time_window_blocks_outside_window():
    # same Monday but too late
    policy = {"allowed_time_windows": [{"day": "mon", "start": "08:00", "end": "12:00"}]}
    _fail(evaluate_policy(policy, {"pickup_time": "2026-04-20T13:00:00"}), "time_window")


def test_time_window_blocks_wrong_day():
    # Monday ride, only Tuesday allowed
    policy = {"allowed_time_windows": [{"day": "tue", "start": "08:00", "end": "18:00"}]}
    _fail(evaluate_policy(policy, {"pickup_time": "2026-04-20T10:00:00"}), "time_window")


def test_time_window_passes_with_matching_day_among_multiple():
    policy = {
        "allowed_time_windows": [
            {"day": "sat", "start": "09:00", "end": "17:00"},
            {"day": "mon", "start": "09:00", "end": "17:00"},
        ]
    }
    _ok(evaluate_policy(policy, {"pickup_time": "2026-04-20T10:00:00"}))


def test_time_window_parse_error_treated_as_pass():
    policy = {"allowed_time_windows": [{"day": "mon", "start": "09:00", "end": "17:00"}]}
    # Bad timestamp string should not raise, should treat as pass
    result = evaluate_policy(policy, {"pickup_time": "not-a-date"})
    assert result["pass"] is True


# ── Rule 3: allowed_payment_source ───────────────────────────────────────────


def test_allowed_source_both_always_passes():
    policy = {"allowed_payment_source": "both"}
    _ok(evaluate_policy(policy, {"allowance": {"amount": 0, "used": 0}}))


def test_allowed_source_allowance_only_passes_with_balance():
    policy = {"allowed_payment_source": "allowance_only"}
    ctx = {"allowance": {"type": "fixed_recurring", "amount": 200, "used": 50}}
    _ok(evaluate_policy(policy, ctx))


def test_allowed_source_allowance_only_blocks_empty_allowance():
    policy = {"allowed_payment_source": "allowance_only"}
    ctx = {"allowance": {"type": "fixed_recurring", "amount": 100, "used": 100}}
    _fail(evaluate_policy(policy, ctx), "allowed_payment_source")


def test_allowed_source_allowance_only_blocks_zero_amount():
    policy = {"allowed_payment_source": "allowance_only"}
    ctx = {"allowance": {"type": "fixed_recurring", "amount": 0, "used": 0}}
    _fail(evaluate_policy(policy, ctx), "allowed_payment_source")


def test_allowed_source_allowance_only_skips_unlimited():
    policy = {"allowed_payment_source": "allowance_only"}
    ctx = {"allowance": {"type": "unlimited", "amount": None, "used": 0}}
    _ok(evaluate_policy(policy, ctx))


def test_allowed_source_master_only_no_check():
    # master_only with empty allowance still passes (no allowance check for master)
    policy = {"allowed_payment_source": "master_only"}
    ctx = {"allowance": {"type": "fixed_recurring", "amount": 0, "used": 0}}
    _ok(evaluate_policy(policy, ctx))


# ── Rule 4: geofence (stub) ───────────────────────────────────────────────────


def test_geofence_stub_always_passes(caplog):
    import logging

    policy = {"allowed_geofence": {"type": "FeatureCollection", "features": []}}
    with caplog.at_level(logging.WARNING, logger="services.corporate_policy_service"):
        result = evaluate_policy(policy, {"estimated_fare": 20})
    _ok(result)
    assert "geofence" in caplog.text.lower()


# ── Multiple failures ─────────────────────────────────────────────────────────


def test_multiple_rules_fail_returns_all():
    policy = {
        "max_fare_per_ride": 10,
        "allowed_payment_source": "allowance_only",
    }
    ctx = {
        "estimated_fare": 50,
        "allowance": {"type": "fixed_recurring", "amount": 0, "used": 0},
    }
    result = evaluate_policy(policy, ctx)
    assert result["pass"] is False
    assert "max_fare_per_ride" in result["failed_rules"]
    assert "allowed_payment_source" in result["failed_rules"]


# ── Policy override ───────────────────────────────────────────────────────────


def test_policy_override_short_circuits_all_rules():
    policy = {
        "max_fare_per_ride": 10,
        "allowed_payment_source": "allowance_only",
    }
    ctx = {
        "estimated_fare": 999,
        "allowance": {"type": "fixed_recurring", "amount": 0, "used": 0},
        "policy_override": True,
    }
    result = evaluate_policy(policy, ctx)
    assert result["pass"] is True
    assert result["failed_rules"] == []
    # Both rules would have failed — they should appear in bypassed_rules
    assert "max_fare_per_ride" in result["bypassed_rules"]
    assert "allowed_payment_source" in result["bypassed_rules"]


def test_policy_override_false_still_enforces_rules():
    policy = {"max_fare_per_ride": 10}
    ctx = {"estimated_fare": 20, "policy_override": False}
    _fail(evaluate_policy(policy, ctx), "max_fare_per_ride")
