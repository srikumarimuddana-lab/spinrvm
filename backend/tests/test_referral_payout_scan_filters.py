"""Candidate discovery for the referral payout loop (utils/referral_payout._tick).

The tick must not full-table-scan: users are filtered server-side to
referral_code_used IS NOT NULL ($notnull → PostgREST not.is.null, backed by the
migration-241 partial index), and referral_payouts claims are looked up per
candidate chunk through the UNIQUE(referee_user_id) index instead of reading the
whole ever-growing payouts table every tick.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import utils.referral_payout as rp
from repositories._base import _apply_filters


def _run(coro):
    return asyncio.run(coro)


# ── $notnull translator support ──


def test_apply_filters_notnull_translates_to_not_is_null():
    q = MagicMock()
    _apply_filters(q, {"referral_code_used": {"$notnull": True}})
    q.not_.is_.assert_called_once_with("referral_code_used", "null")


def test_apply_filters_notnull_false_mirrors_is_null():
    q = MagicMock()
    _apply_filters(q, {"referral_code_used": {"$notnull": False}})
    q.is_.assert_called_once_with("referral_code_used", "null")
    q.not_.is_.assert_not_called()


# ── _tick candidate discovery ──


def _tick_db(users, payout_rows):
    """A db_supabase mock whose get_rows dispatches on table name."""

    async def _get_rows(table, filters=None, **kwargs):
        if table == "referral_payouts" and (filters or {}).get("status") == "processing":
            return []  # stale-claim sweep
        if table == "users":
            return users
        if table == "referral_payouts":
            wanted = set((filters or {}).get("referee_user_id", {}).get("$in", []))
            return [r for r in payout_rows if r["referee_user_id"] in wanted]
        raise AssertionError(f"unexpected table {table}")

    db = MagicMock()
    db.get_rows = AsyncMock(side_effect=_get_rows)
    db.update_one = AsyncMock()
    return db


def test_tick_filters_users_serverside_and_skips_claimed_referees():
    users = [
        {"id": "u_new", "referral_code_used": "RIDE1", "referred_by": "ref_1", "referral_applied_at": None},
        {"id": "u_claimed", "referral_code_used": "RIDE2", "referred_by": "ref_2", "referral_applied_at": None},
        {"id": "u_blank_code", "referral_code_used": "", "referred_by": None, "referral_applied_at": None},
    ]
    db = _tick_db(users, payout_rows=[{"referee_user_id": "u_claimed"}])
    process = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_process_one", process):
        _run(rp._tick())

    # The users query filtered server-side — no unfiltered scan.
    users_call = next(c for c in db.get_rows.await_args_list if c.args[0] == "users")
    assert users_call.args[1] == {"referral_code_used": {"$notnull": True}}

    # Claims were looked up by $in over the candidates, not scanned wholesale.
    claim_calls = [
        c
        for c in db.get_rows.await_args_list
        if c.args[0] == "referral_payouts" and "$in" in (c.args[1] or {}).get("referee_user_id", {})
    ]
    assert claim_calls, "expected an $in claim lookup instead of a full payouts scan"
    for c in claim_calls:
        assert (c.args[1] or {}) != {}, "unfiltered referral_payouts scan is forbidden"

    # Only the unclaimed, non-blank-code referee was processed.
    process.assert_awaited_once()
    assert process.await_args.args[0]["id"] == "u_new"
    assert process.await_args.args[1] == "RIDE1"


def test_tick_chunks_the_claim_lookup(monkeypatch):
    monkeypatch.setattr(rp, "_CLAIM_LOOKUP_CHUNK", 2)
    users = [
        {"id": f"u{i}", "referral_code_used": "RIDE1", "referred_by": "ref", "referral_applied_at": None}
        for i in range(5)
    ]
    db = _tick_db(users, payout_rows=[{"referee_user_id": f"u{i}"} for i in range(5)])
    process = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_process_one", process):
        _run(rp._tick())

    claim_calls = [
        c
        for c in db.get_rows.await_args_list
        if c.args[0] == "referral_payouts" and "$in" in (c.args[1] or {}).get("referee_user_id", {})
    ]
    assert len(claim_calls) == 3  # ceil(5 / 2)
    seen = [id_ for c in claim_calls for id_ in c.args[1]["referee_user_id"]["$in"]]
    assert seen == [f"u{i}" for i in range(5)]  # every candidate covered exactly once
    process.assert_not_awaited()  # all were already claimed


def test_tick_with_no_referred_users_issues_no_claim_lookup():
    db = _tick_db(users=[], payout_rows=[])
    process = AsyncMock()

    with patch.object(rp, "db_supabase", db), patch.object(rp, "_process_one", process):
        _run(rp._tick())

    claim_calls = [
        c
        for c in db.get_rows.await_args_list
        if c.args[0] == "referral_payouts" and (c.args[1] or {}).get("status") != "processing"
    ]
    assert claim_calls == []
    process.assert_not_awaited()
