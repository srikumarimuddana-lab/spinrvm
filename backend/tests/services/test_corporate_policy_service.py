"""Unit tests for corporate_policy_service.evaluate_policy and
evaluate_policy_for_ride.

evaluate_policy is pure — no DB, no mocks needed. evaluate_policy_for_ride
does its DB imports locally inside the function body (dual-import pattern,
falls back to plain `from db_supabase import ...` under pytest), so those
tests patch the names directly on the `db_supabase` module rather than on
`services.corporate_policy_service` (which never binds them at module
scope).
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from services.corporate_policy_service import evaluate_policy, evaluate_policy_for_ride, require_company_bookable

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


# ── time_window: non-string / tz-aware pickup_time branches ──────────────────


def test_time_window_accepts_datetime_object_directly():
    # pickup_time as a datetime object (not a string) — hits the `else` branch
    # that skips datetime.fromisoformat.
    policy = {"allowed_time_windows": [{"day": "mon", "start": "08:00", "end": "18:00"}]}
    _ok(evaluate_policy(policy, {"pickup_time": datetime(2026, 4, 20, 12, 0, 0)}))


def test_time_window_converts_tz_aware_datetime():
    # tz-aware pickup_time hits pickup_dt.astimezone(tz) instead of localize().
    # 2026-04-20T12:00:00-04:00 (Toronto) is still Monday noon in Toronto time.
    policy = {"allowed_time_windows": [{"day": "mon", "start": "08:00", "end": "18:00"}]}
    aware = datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc)  # 12:00 in UTC-4
    _ok(evaluate_policy(policy, {"pickup_time": aware}))


# ── evaluate_policy_for_ride (async DB-backed wrapper) ────────────────────────


def _patches(policy=None, memberships=None, allowance=None, policy_exc=None, membership_exc=None):
    return (
        patch(
            "db_supabase.get_corporate_policy",
            AsyncMock(side_effect=policy_exc) if policy_exc else AsyncMock(return_value=policy),
        ),
        patch(
            "db_supabase.list_active_memberships_for_user",
            AsyncMock(side_effect=membership_exc) if membership_exc else AsyncMock(return_value=memberships or []),
        ),
        patch("db_supabase.get_member_allowance", AsyncMock(return_value=allowance)),
    )


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_happy_path_fetches_and_evaluates():
    membership = {"id": "m1", "company_id": "c1", "policy_override": False}
    p_policy, p_members, p_allowance = _patches(
        policy={"max_fare_per_ride": 100},
        memberships=[membership],
        allowance={"type": "fixed_recurring", "amount": 200, "used": 0},
    )
    with p_policy, p_members, p_allowance as m_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("50"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
        )
    assert result.passed is True
    assert result.policy == {"max_fare_per_ride": 100}
    assert result.allowance == {"type": "fixed_recurring", "amount": 200, "used": 0}
    m_allowance.assert_awaited_once_with("m1")


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_no_matching_membership_leaves_allowance_empty():
    # A membership exists but for a different company — allowance must stay {}
    # and get_member_allowance must never be called.
    other_membership = {"id": "m2", "company_id": "other-co"}
    p_policy, p_members, p_allowance = _patches(policy={}, memberships=[other_membership])
    with p_policy, p_members, p_allowance as m_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("20"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
        )
    assert result.passed is True
    assert result.allowance == {}
    m_allowance.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_policy_fetch_failure_fails_open():
    # A DB error fetching the policy must not raise — treated as no policy
    # (fail-open), per the docstring's "never blocks every work ride" guarantee.
    p_policy, p_members, p_allowance = _patches(
        policy_exc=RuntimeError("db down"),
        memberships=[],
    )
    with p_policy, p_members, p_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("9999"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
        )
    assert result.passed is True
    assert result.policy == {}


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_membership_lookup_failure_degrades_gracefully():
    # A DB error listing memberships must not raise — allowance enforcement
    # degrades (empty allowance) rather than blocking the ride.
    p_policy, p_members, p_allowance = _patches(
        policy={"allowed_payment_source": "allowance_only"},
        membership_exc=RuntimeError("db down"),
    )
    with p_policy, p_members, p_allowance as m_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("20"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
        )
    # allowance stayed {} → allowance_only rule fails closed (no crash though)
    assert result.passed is False
    assert "allowed_payment_source" in result.failed_rules
    m_allowance.assert_not_awaited()


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_member_policy_override_applies_when_caller_did_not_set_it():
    membership = {"id": "m1", "company_id": "c1", "policy_override": True}
    p_policy, p_members, p_allowance = _patches(
        policy={"max_fare_per_ride": 1},
        memberships=[membership],
        allowance={},
    )
    with p_policy, p_members, p_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("999"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
            policy_override=False,
        )
    # Member-level override flips the effective flag even though the caller
    # passed policy_override=False explicitly.
    assert result.passed is True
    assert "max_fare_per_ride" in result.bypassed_rules


@pytest.mark.asyncio
async def test_evaluate_policy_for_ride_caller_override_wins_when_true():
    p_policy, p_members, p_allowance = _patches(policy={"max_fare_per_ride": 1}, memberships=[])
    with p_policy, p_members, p_allowance:
        result = await evaluate_policy_for_ride(
            corporate_account_id="c1",
            rider_id="rider1",
            estimated_fare=Decimal("999"),
            ride_type="standard",
            pickup_time=datetime(2026, 4, 20, 12, 0, 0),
            policy_override=True,
        )
    assert result.passed is True
    assert "max_fare_per_ride" in result.bypassed_rules


# ── require_company_bookable (shared "is company active" guard) ───────────────
#
# Single source of truth for the self-book path (routes/rides/booking.py) and
# the company-portal guest-booking path (routes/corporate_company_bookings.py)
# — scheduled-rides gap review, Finding #20. Uses the broader "not active
# blocks" definition (so pending_verification is blocked too), matching the
# guest-booking path's prior stance rather than the self-book path's narrower
# prior "suspended/closed only" check — that widening is new behavior for the
# self-book path and is covered explicitly below.


@pytest.mark.asyncio
async def test_require_company_bookable_passes_for_active_company():
    with patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})):
        await require_company_bookable("c1", settings={})  # no exception


@pytest.mark.asyncio
async def test_require_company_bookable_blocks_suspended_company():
    with patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "suspended"})):
        with pytest.raises(HTTPException) as exc_info:
            await require_company_bookable("c1", settings={})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["company_inactive"]


@pytest.mark.asyncio
async def test_require_company_bookable_blocks_pending_verification_company():
    """New coverage: pending_verification is not 'active', so a not-yet-
    verified company must be blocked on the self-book path too — previously
    the self-book path's own inline check only blocked suspended/closed and
    let pending_verification companies through."""
    with patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "pending_verification"})):
        with pytest.raises(HTTPException) as exc_info:
            await require_company_bookable("c1", settings={})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["company_inactive"]


@pytest.mark.asyncio
async def test_require_company_bookable_blocks_missing_company():
    with patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await require_company_bookable("c1", settings={})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["failed_rules"] == ["company_inactive"]


@pytest.mark.asyncio
async def test_require_company_bookable_kill_switch_disabled_skips_db_lookup():
    """Rollback path: corporate_inactive_company_blocks_booking=False must
    fully restore fail-open behavior without a redeploy, and without even
    hitting the DB — the shared kill switch either path can be paused with."""
    with patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "suspended"})) as m_get:
        await require_company_bookable(
            "c1", settings={"corporate_inactive_company_blocks_booking": False}
        )  # no exception
    m_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_company_bookable_fetches_settings_when_not_provided():
    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch("db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
    ):
        await require_company_bookable("c1")  # settings=None -> fetched internally, no exception
