"""C136 T5: dedupe_shared_push_tokens planning + dry-run safety (no DB)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BACKEND, "scripts"))

import dedupe_shared_push_tokens as mod  # noqa: E402

TOK = "device-token-SECRET-xyz"


def _push(id_, uid, tok, ts):
    return {"id": id_, "user_id": uid, "token": tok, "updated_at": ts}


def test_newest_push_tokens_row_wins_and_other_owner_is_fully_detached():
    push = [
        _push("p1", "A", TOK, "2026-09-20T10:00:00+00:00"),
        _push("p2", "B", TOK, "2026-09-24T10:00:00+00:00"),
    ]
    users = [
        {"id": "A", "fcm_token": TOK, "fcm_token_rider": None, "fcm_token_driver": TOK},
        {"id": "B", "fcm_token": TOK, "fcm_token_rider": TOK, "fcm_token_driver": None},
    ]
    plan = mod.build_plan(push, users)
    assert len(plan) == 1
    p = plan[0]
    assert p.keeper == "B"
    assert p.push_token_rows_to_delete == {"A": ["p1"]}
    assert p.user_columns_to_clear == {"A": ["fcm_token", "fcm_token_driver"]}
    # keeper untouched
    assert "B" not in p.user_columns_to_clear


def test_dual_role_single_user_is_not_shared():
    push = [_push("p1", "A", TOK, "2026-09-24T10:00:00+00:00")]
    users = [{"id": "A", "fcm_token": TOK, "fcm_token_rider": TOK, "fcm_token_driver": TOK}]
    assert mod.build_plan(push, users) == []


def test_token_only_on_users_columns_is_ambiguous_and_never_written():
    users = [
        {"id": "A", "fcm_token_rider": TOK},
        {"id": "B", "fcm_token_driver": TOK},
    ]
    plan = mod.build_plan([], users)
    assert len(plan) == 1 and plan[0].ambiguous
    db = MagicMock(delete_many=AsyncMock(), update_one=AsyncMock())
    asyncio.run(mod.apply_plan(db, plan))
    db.delete_many.assert_not_called()
    db.update_one.assert_not_called()


def test_summary_counts():
    push = [
        _push("p1", "A", TOK, "2026-09-20T10:00:00+00:00"),
        _push("p2", "B", TOK, "2026-09-24T10:00:00+00:00"),
    ]
    users = [{"id": "A", "fcm_token_driver": TOK}, {"id": "B", "fcm_token_rider": TOK}]
    s = mod.summarize(mod.build_plan(push, users))
    assert s == {
        "shared_tokens": 1,
        "ambiguous_tokens": 0,
        "users_to_detach": 1,
        "push_token_rows_to_delete": 1,
        "user_columns_to_clear": 1,
    }


def test_apply_writes_are_scoped_to_loser_and_token():
    push = [
        _push("p1", "A", TOK, "2026-09-20T10:00:00+00:00"),
        _push("p2", "B", TOK, "2026-09-24T10:00:00+00:00"),
    ]
    users = [{"id": "A", "fcm_token_driver": TOK}]
    db = MagicMock(delete_many=AsyncMock(), update_one=AsyncMock())
    res = asyncio.run(mod.apply_plan(db, mod.build_plan(push, users)))
    db.delete_many.assert_awaited_once_with("push_tokens", {"id": "p1", "user_id": "A", "token": TOK})
    db.update_one.assert_awaited_once_with("users", {"id": "A", "fcm_token_driver": TOK}, {"fcm_token_driver": None})
    assert res == {"push_token_rows_deleted": 1, "user_columns_cleared": 1, "failed": 0}


def test_default_run_is_dry_and_never_logs_token(monkeypatch, caplog):
    push = [
        _push("p1", "A", TOK, "2026-09-20T10:00:00+00:00"),
        _push("p2", "B", TOK, "2026-09-24T10:00:00+00:00"),
    ]
    fake_db = MagicMock(delete_many=AsyncMock(), update_one=AsyncMock())

    async def fake_get_rows(table, filters, **kw):
        if kw.get("offset", 0):
            return []
        if table == "push_tokens":
            return push
        return [{"id": "A", "fcm_token": None, "fcm_token_rider": None, "fcm_token_driver": TOK}]

    fake_db.get_rows = AsyncMock(side_effect=fake_get_rows)
    monkeypatch.setitem(sys.modules, "db_supabase", fake_db)
    with caplog.at_level(logging.INFO, logger="dedupe_shared_push_tokens"):
        assert mod.main([]) == 0
    fake_db.delete_many.assert_not_called()
    fake_db.update_one.assert_not_called()
    assert TOK not in caplog.text
    assert "DRY RUN" in caplog.text
    # every read has an explicit ORDER BY (get_rows has none by default)
    assert all(c.kwargs.get("order") == "id" for c in fake_db.get_rows.await_args_list)


def test_apply_without_signoff_refuses(monkeypatch):
    fake_db = MagicMock(get_rows=AsyncMock(return_value=[]))
    monkeypatch.setitem(sys.modules, "db_supabase", fake_db)
    assert mod.main(["--apply"]) == 2
    fake_db.get_rows.assert_not_called()
