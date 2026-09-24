"""T11-5b1 (X8, default off): issue_refresh_token(raw=...) and classify_committed_replay."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from utils import refresh_tokens as rt
from utils.error_handling import DatabaseError, DuplicateRecordError

PARENT = "p" * 64
PROPOSED = "A" * 32 + "b_-9" * 8


def _future():
    return (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()


def _parent(**overrides):
    row = {
        "id": "parent-1",
        "user_id": "u1",
        "audience": "driver",
        "token_version": 3,
        "revoked_at": datetime.now(timezone.utc).isoformat(),
        "replaced_by": "succ-1",
    }
    row.update(overrides)
    return row


def _successor(**overrides):
    row = {
        "id": "succ-1",
        "user_id": "u1",
        "audience": "driver",
        "token_version": 3,
        "revoked_at": None,
        "expires_at": _future(),
        "token_hash": rt._hash_refresh_token(PROPOSED),
    }
    row.update(overrides)
    return row


def _finder(parent, successor, user=None):
    user = user if user is not None else {"id": "u1", "token_version": 3}

    async def find_one(table, filters):
        if table == "users":
            return user
        if "token_hash" in filters:
            return parent
        return successor

    return AsyncMock(side_effect=find_one)


@pytest.mark.parametrize(
    ("value", "ok"),
    [(PROPOSED, True), ("A" * 63, False), ("A" * 65, False), ("A" * 63 + "=", False), (None, False)],
)
def test_proposed_token_shape(value, ok):
    assert rt.is_valid_proposed_refresh_token(value) is ok


@pytest.mark.asyncio
async def test_issue_uses_proposed_raw():
    insert = AsyncMock(return_value={"id": "row-1"})
    with patch.object(rt.db, "insert_one", insert):
        raw, row_id, _ = await rt.issue_refresh_token("u1", audience="driver", raw=PROPOSED)
    assert raw == PROPOSED and row_id == "row-1"
    assert insert.await_args.args[1]["token_hash"] == rt._hash_refresh_token(PROPOSED)


@pytest.mark.asyncio
async def test_issue_rejects_malformed_proposal():
    with patch.object(rt.db, "insert_one", AsyncMock()) as insert, pytest.raises(ValueError):
        await rt.issue_refresh_token("u1", raw="short")
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_unique_conflict_retries_once_with_server_token():
    insert = AsyncMock(side_effect=[DuplicateRecordError("dup"), {"id": "row-2"}])
    with patch.object(rt.db, "insert_one", insert):
        raw, row_id, _ = await rt.issue_refresh_token("u1", raw=PROPOSED)
    assert raw != PROPOSED and rt.is_valid_proposed_refresh_token(raw)
    assert row_id == "row-2"
    assert insert.await_args_list[1].args[1]["token_hash"] == rt._hash_refresh_token(raw)


@pytest.mark.asyncio
async def test_issue_non_unique_error_propagates():
    with (
        patch.object(rt.db, "insert_one", AsyncMock(side_effect=RuntimeError("down"))),
        pytest.raises(RuntimeError),
    ):
        await rt.issue_refresh_token("u1", raw=PROPOSED)


@pytest.mark.asyncio
async def test_issue_never_logs_token_material():
    insert = AsyncMock(side_effect=[DuplicateRecordError("dup"), {"id": "row-2"}])
    messages: list[str] = []
    sink_id = rt.logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        with patch.object(rt.db, "insert_one", insert):
            raw, _, _ = await rt.issue_refresh_token("u1", raw=PROPOSED)
    finally:
        rt.logger.remove(sink_id)
    text = "".join(messages)
    for secret in (PROPOSED, raw, rt._hash_refresh_token(PROPOSED), rt._hash_refresh_token(raw)):
        assert secret not in text


@pytest.mark.asyncio
async def test_classify_recover():
    with patch.object(rt.db, "find_one", _finder(_parent(), _successor())):
        verdict, succ = await rt.classify_committed_replay(PARENT, PROPOSED)
    assert verdict == "recover" and succ["id"] == "succ-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent",
    [None, _parent(revoked_at=None), _parent(replaced_by=None)],
)
async def test_classify_no_match_for_live_or_unchained_parent(parent):
    with patch.object(rt.db, "find_one", _finder(parent, _successor())):
        assert await rt.classify_committed_replay(PARENT, PROPOSED) == ("no_match", None)


@pytest.mark.asyncio
async def test_classify_no_match_when_hash_differs():
    succ = _successor(token_hash=rt._hash_refresh_token("Z" * 64))
    with patch.object(rt.db, "find_one", _finder(_parent(), succ)):
        assert await rt.classify_committed_replay(PARENT, PROPOSED) == ("no_match", None)


@pytest.mark.asyncio
async def test_classify_malformed_proposal_is_no_match_without_db():
    find = AsyncMock()
    with patch.object(rt.db, "find_one", find):
        assert await rt.classify_committed_replay(PARENT, "bad") == ("no_match", None)
    find.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "succ",
    [
        _successor(user_id="u2"),
        _successor(audience="admin"),
        _successor(revoked_at="2026-09-24T00:00:00+00:00"),
        _successor(expires_at="2020-01-01T00:00:00+00:00"),
        _successor(token_version=4),
    ],
)
async def test_classify_dead(succ):
    with patch.object(rt.db, "find_one", _finder(_parent(), succ)):
        assert await rt.classify_committed_replay(PARENT, PROPOSED) == ("dead", None)


@pytest.mark.asyncio
async def test_classify_dead_on_generation_mismatch():
    finder = _finder(_parent(), _successor(), user={"id": "u1", "token_version": 9})
    with patch.object(rt.db, "find_one", finder):
        assert await rt.classify_committed_replay(PARENT, PROPOSED) == ("dead", None)


@pytest.mark.asyncio
async def test_classify_db_error_raises_503():
    with (
        patch.object(rt.db, "find_one", AsyncMock(side_effect=RuntimeError("down"))),
        pytest.raises(DatabaseError) as caught,
    ):
        await rt.classify_committed_replay(PARENT, PROPOSED)
    assert caught.value.status_code == 503
