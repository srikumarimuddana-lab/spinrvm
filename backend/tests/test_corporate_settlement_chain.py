"""M5.5 settlement chain: allowance → section wallet → master (Q9/Q10/Q13).

Direct unit tests of services.payment_service.settle_corporate — the full
fallback matrix plus compensation and per-leg refund reversal.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import services.payment_service as ps

_RIDE = {"corporate_account_id": "c1", "corporate_member_id": "m1", "rider_id": "u1"}
_MEMBER = {"id": "m1", "company_id": "c1", "status": "active", "user_id": "u1", "section_id": "s1"}
_MEMBER_NO_SECTION = {**_MEMBER, "section_id": None}
_WALLET = {"id": "w-master", "balance": 1000, "soft_negative_floor": -50}
_SECTION_W = {"id": "w-sec", "section_id": "s1", "balance": 30}


def _mocks(
    member=_MEMBER,
    allowance=None,
    section_wallet=_SECTION_W,
    policy=None,
):
    if allowance is None:
        allowance = {"id": "a1", "type": "fixed_recurring", "amount": 20, "used": 0}
    return {
        "get_corporate_member_by_id": patch.object(
            ps.db_supabase, "get_corporate_member_by_id", AsyncMock(return_value=member)
        ),
        "get_member_allowance": patch.object(ps.db_supabase, "get_member_allowance", AsyncMock(return_value=allowance)),
        "get_corporate_wallet_by_company": patch.object(
            ps.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_WALLET)
        ),
        "get_section_wallet": patch.object(
            ps.db_supabase, "get_section_wallet", AsyncMock(return_value=section_wallet)
        ),
        "get_corporate_policy": patch.object(
            ps.db_supabase, "get_corporate_policy", AsyncMock(return_value=policy or {})
        ),
        "insert_one": patch.object(ps.db_supabase, "insert_one", AsyncMock(return_value={})),
        "update_ride": patch.object(ps.db_supabase, "update_ride", AsyncMock(return_value={})),
        "apply_rollback": patch.object(
            ps.corporate_allowance_service, "apply_rollback", AsyncMock(return_value={"transaction_id": "t1"})
        ),
        "apply_grant": patch.object(
            ps.corporate_allowance_service, "apply_grant", AsyncMock(return_value={"transaction_id": "tg"})
        ),
        "apply_adjustment": patch.object(
            ps.corporate_wallet_service, "apply_adjustment", AsyncMock(return_value={"transaction_id": "t2"})
        ),
        "apply_section_ride_debit": patch.object(
            ps.corporate_wallet_service,
            "apply_section_ride_debit",
            AsyncMock(return_value={"transaction_id": "t3"}),
        ),
        "refund_legs": patch.object(
            ps.corporate_wallet_service, "refund_corporate_ride_legs", AsyncMock(return_value={})
        ),
        "evaluate_policy": patch.object(ps, "evaluate_policy", lambda *_a, **_k: {"pass": True, "failed_rules": []}),
    }


async def _run(total="50.00", **mock_overrides):
    ms = _mocks(**mock_overrides)
    started = {k: m.start() for k, m in ms.items()}
    try:
        result = await ps.settle_corporate(_RIDE, "r1", Decimal(total), Decimal("0"))
        return result, started
    finally:
        for m in ms.values():
            m.stop()


@pytest.mark.asyncio
async def test_allowance_covers_all_no_spillover():
    result, m = await _run(total="15.00")
    assert result.success
    m["apply_rollback"].assert_called_once()
    m["apply_section_ride_debit"].assert_not_called()
    m["apply_adjustment"].assert_not_called()


@pytest.mark.asyncio
async def test_section_covers_spill_no_master():
    # allowance 20, total 45 → spill 25... section balance 30 covers it.
    result, m = await _run(total="45.00")
    assert result.success
    kwargs = m["apply_section_ride_debit"].call_args.kwargs
    assert Decimal(str(kwargs["amount"])) == Decimal("25.00")
    assert kwargs["section_id"] == "s1"
    m["apply_adjustment"].assert_not_called()
    rps = m["insert_one"].call_args_list[0].args[1]
    assert Decimal(str(rps["section_debit_amount"])) == Decimal("25.00")
    assert rps["section_id"] == "s1"


@pytest.mark.asyncio
async def test_full_chain_allowance_section_master_with_credit_floor():
    # allowance 20 + section 30 + master 50 = 100.
    result, m = await _run(total="100.00")
    assert result.success
    adj = m["apply_adjustment"].call_args.kwargs
    assert Decimal(str(adj["amount"])) == Decimal("-50.00")
    # Q10: master extends into the company's credit floor at settlement.
    assert Decimal(str(adj["floor"])) == Decimal("-50")
    rps = m["insert_one"].call_args_list[0].args[1]
    assert Decimal(str(rps["allowance_debit_amount"])) == Decimal("20.00")
    assert Decimal(str(rps["section_debit_amount"])) == Decimal("30.00")
    assert Decimal(str(rps["master_fallback_amount"])) == Decimal("50.00")


@pytest.mark.asyncio
async def test_no_section_membership_falls_straight_to_master():
    result, m = await _run(total="50.00", member=_MEMBER_NO_SECTION)
    assert result.success
    m["get_section_wallet"].assert_not_called()
    m["apply_section_ride_debit"].assert_not_called()
    assert Decimal(str(m["apply_adjustment"].call_args.kwargs["amount"])) == Decimal("-30.00")


@pytest.mark.asyncio
async def test_concurrent_section_drain_reroutes_to_master():
    # Q9 race: the RPC floor rejects the section debit → remainder re-routes
    # to master instead of failing the settlement.
    ms = _mocks()
    started = {k: m.start() for k, m in ms.items()}
    try:
        started["apply_section_ride_debit"].side_effect = RuntimeError("wallet_below_floor: new=-1 floor=0")
        result = await ps.settle_corporate(_RIDE, "r1", Decimal("100.00"), Decimal("0"))
        assert result.success
        # master picks up section's 30 + its own 50.
        assert Decimal(str(started["apply_adjustment"].call_args.kwargs["amount"])) == Decimal("-80.00")
        rps = started["insert_one"].call_args_list[0].args[1]
        assert Decimal(str(rps["section_debit_amount"])) == Decimal("0.00")
        assert rps["section_id"] is None
    finally:
        for m in ms.values():
            m.stop()


@pytest.mark.asyncio
async def test_master_failure_compensates_section_and_allowance():
    ms = _mocks()
    started = {k: m.start() for k, m in ms.items()}
    try:
        started["apply_adjustment"].side_effect = RuntimeError("db down")
        result = await ps.settle_corporate(_RIDE, "r1", Decimal("100.00"), Decimal("0"))
        assert not result.success
        assert result.status_code == 503
        started["refund_legs"].assert_called_once()  # section compensated
        started["apply_grant"].assert_called_once()  # allowance compensated
        started["update_ride"].assert_called_with("r1", {"payment_status": "pending"})
    finally:
        for m in ms.values():
            m.stop()


@pytest.mark.asyncio
async def test_allowance_only_policy_flags_section_spillover():
    result, m = await _run(
        total="45.00",
        policy={"allowed_payment_source": "allowance_only"},
    )
    assert result.success  # settles, but flagged
    tables = [c.args[0] for c in m["insert_one"].call_args_list]
    assert "corporate_policy_evaluations" in tables


# ── Q13: per-leg refund reversal ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refund_reverses_each_leg_to_its_wallet():
    import services.corporate_wallet_service as ws

    rps = {
        "allowance_debit_amount": 20,
        "section_debit_amount": 30,
        "section_id": "s1",
        "master_fallback_amount": 50,
        "member_id": "m1",
    }
    apply_calls = []

    async def _fake_apply(**kwargs):
        apply_calls.append(kwargs)
        return {"transaction_id": f"t{len(apply_calls)}"}

    with (
        patch("db_supabase.get_master_wallet", AsyncMock(return_value={"id": "w-master"})),
        patch("db_supabase.get_section_wallet", AsyncMock(return_value={"id": "w-sec"})),
        patch("db_supabase.get_member_allowance", AsyncMock(return_value={"id": "a1"})),
        patch("services.corporate_allowance_service.apply_grant", AsyncMock(return_value={"ok": 1})) as m_grant,
        patch.object(ws, "_apply", _fake_apply),
    ):
        legs = await ws.refund_corporate_ride_legs(
            ride_id="r1", payment_source=rps, company_id="c1", actor_user_id="u1"
        )

    m_grant.assert_called_once()  # allowance leg → allowance grant
    assert Decimal(str(m_grant.call_args.kwargs["amount"])) == Decimal("20")
    # section + master legs refunded to their own wallets
    scopes = [(c["scope"], str(c["delta"])) for c in apply_calls]
    assert ("section:s1", "30") in scopes
    assert ("master", "50") in scopes
    assert legs["section"] and legs["master"]


@pytest.mark.asyncio
async def test_section_nonfloor_failure_compensates_allowance_503():
    # Audit-flagged branch: a NON-floor section failure must compensate the
    # allowance, mark pending, and 503 — not proceed to master.
    ms = _mocks()
    started = {k: m.start() for k, m in ms.items()}
    try:
        started["apply_section_ride_debit"].side_effect = RuntimeError("connection reset")
        result = await ps.settle_corporate(_RIDE, "r1", Decimal("100.00"), Decimal("0"))
        assert not result.success and result.status_code == 503
        started["apply_grant"].assert_called_once()  # allowance compensated
        started["apply_adjustment"].assert_not_called()  # master never engaged
        started["update_ride"].assert_called_with("r1", {"payment_status": "pending"})
    finally:
        for m in ms.values():
            m.stop()


@pytest.mark.asyncio
async def test_compensation_failure_is_loud_but_still_503():
    # Audit-flagged branch: if compensation itself dies, the settlement still
    # fails safe (pending + 503) and the error is logged for manual fix —
    # with the per-leg idempotency keys, a later retry cannot double-debit.
    ms = _mocks()
    started = {k: m.start() for k, m in ms.items()}
    try:
        started["apply_adjustment"].side_effect = RuntimeError("db down")
        started["refund_legs"].side_effect = RuntimeError("also down")
        started["apply_grant"].side_effect = RuntimeError("everything down")
        result = await ps.settle_corporate(_RIDE, "r1", Decimal("100.00"), Decimal("0"))
        assert not result.success and result.status_code == 503
        started["update_ride"].assert_called_with("r1", {"payment_status": "pending"})
    finally:
        for m in ms.values():
            m.stop()


@pytest.mark.asyncio
async def test_per_leg_idempotency_keys_passed():
    # Audit blocker fix: every wallet leg carries a deterministic synthetic
    # key so a settlement retry is a DB-level no-op.
    result, m = await _run(total="100.00")
    assert result.success
    assert m["apply_adjustment"].call_args.kwargs["idempotency_key"] == "ride:r1:master"
    # apply_section_ride_debit derives its own key internally — verify at the
    # service layer instead:
    import inspect

    import services.corporate_wallet_service as ws

    src = inspect.getsource(ws.apply_section_ride_debit)
    assert 'f"ride:{ride_id}:section"' in src
