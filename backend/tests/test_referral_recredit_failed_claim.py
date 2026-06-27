"""recredit_failed_claim: side-aware re-credit of a FAILED referral claim.

The money-critical guarantee: when the referrer was ALREADY credited (only the
referee's credit had failed — the migration-198/199 'referral_bonus' constraint
backlog), a re-credit pays ONLY the referee and NEVER re-pays the referrer.

_credit is mocked, so these tests assert WHICH sides are credited and with which
wallet txn type, not the wallet mechanics (covered elsewhere).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

try:
    from backend.utils import referral_payout
except ImportError:  # pragma: no cover - bare-path test runs
    from utils import referral_payout


def _run(coro):
    return asyncio.run(coro)


def _setup(monkeypatch, row):
    """find_one → the failed row; update_one → truthy (atomic claim wins + final
    write). Returns the patched _credit mock so a test can inspect the calls."""
    monkeypatch.setattr(referral_payout.db_supabase, "find_one", AsyncMock(return_value=row))
    monkeypatch.setattr(referral_payout.db_supabase, "update_one", AsyncMock(return_value={"id": row["id"]}))
    credit = AsyncMock()
    monkeypatch.setattr(referral_payout, "_credit", credit)
    return credit


def test_only_credits_referee_when_referrer_already_paid(monkeypatch):
    # The migration-198/199 backlog shape: referrer's 'referral_reward' landed,
    # referee's 'referral_bonus' was rejected → only the referee is owed.
    row = {
        "id": "c1",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": "2026-06-20T00:00:00+00:00",
        "referee_credited_at": None,
    }
    credit = _setup(monkeypatch, row)

    out = _run(referral_payout.recredit_failed_claim("u_new"))

    assert out["credited"] == ["referee"]
    assert out["status"] == "paid"
    # Exactly one credit, to the REFEREE (the new signup), as a 'referral_bonus'.
    credit.assert_awaited_once()
    args = credit.await_args.args
    assert args[0] == "u_new"  # user credited == referee
    assert args[4] == "referral_bonus"  # referee-side wallet txn type


def test_credits_both_when_neither_paid(monkeypatch):
    # Pure pre-198 shape: referrer's credit failed first, so neither side landed.
    row = {
        "id": "c2",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    credit = _setup(monkeypatch, row)

    out = _run(referral_payout.recredit_failed_claim("u_new"))

    assert out["credited"] == ["referrer", "referee"]
    assert credit.await_count == 2
    types = [c.args[4] for c in credit.await_args_list]
    assert types == ["referral_reward", "referral_bonus"]


def test_driver_referral_credits_referrer_only(monkeypatch):
    # Driver referrals pay the referrer ($10) and give the referee $0 → only the
    # referrer side is ever owed.
    row = {
        "id": "c4",
        "kind": "driver",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "10.00",
        "referee_reward": "0",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    credit = _setup(monkeypatch, row)

    out = _run(referral_payout.recredit_failed_claim("u_new"))

    assert out["credited"] == ["referrer"]
    credit.assert_awaited_once()
    assert credit.await_args.args[4] == "referral_reward"


def test_raises_when_no_failed_row(monkeypatch):
    monkeypatch.setattr(referral_payout.db_supabase, "find_one", AsyncMock(return_value=None))
    with pytest.raises(referral_payout.ReferralClaimNotFound):
        _run(referral_payout.recredit_failed_claim("nope"))


def test_raises_and_does_not_credit_when_claim_lost_to_concurrent(monkeypatch):
    row = {
        "id": "c3",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    monkeypatch.setattr(referral_payout.db_supabase, "find_one", AsyncMock(return_value=row))
    # The atomic failed->processing claim returns None → another caller took it.
    monkeypatch.setattr(referral_payout.db_supabase, "update_one", AsyncMock(return_value=None))
    credit = AsyncMock()
    monkeypatch.setattr(referral_payout, "_credit", credit)

    with pytest.raises(referral_payout.ReferralClaimNotFound):
        _run(referral_payout.recredit_failed_claim("u_new"))
    credit.assert_not_awaited()


def test_credit_failure_returns_claim_to_failed_and_reraises(monkeypatch):
    row = {
        "id": "c5",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": "2026-06-20T00:00:00+00:00",
        "referee_credited_at": None,
    }
    monkeypatch.setattr(referral_payout.db_supabase, "find_one", AsyncMock(return_value=row))
    update = AsyncMock(return_value={"id": "c5"})
    monkeypatch.setattr(referral_payout.db_supabase, "update_one", update)
    monkeypatch.setattr(referral_payout, "_credit", AsyncMock(side_effect=RuntimeError("ledger down")))

    with pytest.raises(RuntimeError):
        _run(referral_payout.recredit_failed_claim("u_new"))

    # Last update_one returns the row to 'failed' (not left 'processing').
    last_update = update.await_args_list[-1].args[2]["$set"]
    assert last_update["status"] == "failed"


def test_null_referrer_with_reward_owed_raises_not_silent_paid(monkeypatch):
    # Finding #1: referrer anonymised (referrer_user_id NULL) but reward still owed
    # → must surface loudly and stay 'failed', never silently mark 'paid' (which
    # would lose the referrer reward forever).
    row = {
        "id": "c6",
        "kind": "rider",
        "referrer_user_id": None,
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "0",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    monkeypatch.setattr(referral_payout.db_supabase, "find_one", AsyncMock(return_value=row))
    update = AsyncMock(return_value={"id": "c6"})
    monkeypatch.setattr(referral_payout.db_supabase, "update_one", update)
    credit = AsyncMock()
    monkeypatch.setattr(referral_payout, "_credit", credit)

    with pytest.raises(RuntimeError):
        _run(referral_payout.recredit_failed_claim("u_new"))

    credit.assert_not_awaited()  # never tried to credit a null user
    statuses = [c.args[2]["$set"].get("status") for c in update.await_args_list if "status" in c.args[2]["$set"]]
    assert "failed" in statuses  # returned to 'failed'
    assert "paid" not in statuses  # NEVER marked terminal 'paid'


def test_claim_refreshes_created_at(monkeypatch):
    # Finding #3: the failed->processing claim must refresh created_at so the loop's
    # stale-'processing' sweep grants a fresh grace window instead of reclaiming a
    # days-old backlog row mid-credit.
    row = {
        "id": "c7",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    _setup(monkeypatch, row)
    update = referral_payout.db_supabase.update_one  # the AsyncMock from _setup

    _run(referral_payout.recredit_failed_claim("u_new"))

    claim_set = update.await_args_list[0].args[2]["$set"]
    assert claim_set["status"] == "processing"
    assert "created_at" in claim_set


def test_credited_at_persisted_before_final_paid(monkeypatch):
    # Findings #2/#3: each credited_at is written immediately after its credit,
    # BEFORE the final status='paid' — so money-moved is durable even if finalize
    # never runs, and a later reset to 'failed' can't re-pay the side.
    row = {
        "id": "c8",
        "kind": "rider",
        "referrer_user_id": "u_ref",
        "referee_user_id": "u_new",
        "referrer_reward": "5.00",
        "referee_reward": "5.00",
        "referrer_credited_at": None,
        "referee_credited_at": None,
    }
    _setup(monkeypatch, row)
    update = referral_payout.db_supabase.update_one

    _run(referral_payout.recredit_failed_claim("u_new"))

    sets = [c.args[2]["$set"] for c in update.await_args_list]
    idx_ref_credit = next(i for i, s in enumerate(sets) if "referrer_credited_at" in s)
    idx_paid = next(i for i, s in enumerate(sets) if s.get("status") == "paid")
    assert idx_ref_credit < idx_paid
