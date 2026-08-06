"""utils/refresh_tokens.py — mint / lookup / revoke lifecycle.

test_refresh_token_reuse_detection.py pins the theft-cascade behaviour
of _handle_refresh_token_reuse. This file covers the surrounding
lifecycle that cascade depends on but that had no direct coverage:
issue_refresh_token (minting + rotation chaining), lookup_refresh_token's
remaining branches (empty input, DB error, expiry parsing), revoke_refresh_token,
revoke_all_for_user, and the _parse_iso_dt / _is_benign_rotation_replay
helpers' edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import utils.refresh_tokens  # noqa: F401 — bind submodule before patch() resolves it

# ─────────────────────────────────────────────────────────────────────────────
# _parse_iso_dt
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_iso_dt_none_returns_none():
    from utils.refresh_tokens import _parse_iso_dt

    assert _parse_iso_dt(None) is None
    assert _parse_iso_dt("") is None


def test_parse_iso_dt_naive_datetime_gets_utc_tzinfo():
    from utils.refresh_tokens import _parse_iso_dt

    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = _parse_iso_dt(naive)
    assert result.tzinfo == timezone.utc


def test_parse_iso_dt_aware_datetime_passthrough():
    from utils.refresh_tokens import _parse_iso_dt

    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_iso_dt(aware) is aware


def test_parse_iso_dt_bad_string_returns_none():
    from utils.refresh_tokens import _parse_iso_dt

    assert _parse_iso_dt("not-a-timestamp") is None


def test_parse_iso_dt_unknown_type_returns_none():
    from utils.refresh_tokens import _parse_iso_dt

    # e.g. a Postgres timestamp arriving as some other object shape.
    assert _parse_iso_dt(12345) is None


# ─────────────────────────────────────────────────────────────────────────────
# _is_benign_rotation_replay — revoked_at unparseable
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_benign_rotation_replay_false_when_revoked_at_unparseable():
    # async since the chain-depth condition (_successor_is_live) needs a DB read.
    from utils.refresh_tokens import _is_benign_rotation_replay

    row = {"replaced_by": "rtk-2", "revoked_at": "not-a-timestamp"}
    assert await _is_benign_rotation_replay(row) is False


@pytest.mark.asyncio
async def test_is_benign_rotation_replay_false_without_replaced_by():
    """Short-circuits before any DB read — a token revoked without a replacement
    (explicit logout, prior cascade) is never a rotation race."""
    from utils.refresh_tokens import _is_benign_rotation_replay

    find_one = AsyncMock()
    with patch("utils.refresh_tokens.db.find_one", find_one):
        row = {"replaced_by": None, "revoked_at": datetime.now(timezone.utc).isoformat()}
        assert await _is_benign_rotation_replay(row) is False
    find_one.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# issue_refresh_token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_refresh_token_mints_and_hashes():
    inserted = {}

    async def _insert_one(table, doc):
        inserted["table"] = table
        inserted["doc"] = doc
        return {"id": "rtk-new-001"}

    with patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)):
        from utils.refresh_tokens import issue_refresh_token

        raw, row_id, expires_at = await issue_refresh_token("user-1", audience="rider")

    assert inserted["table"] == "refresh_tokens"
    # Plaintext must never be stored — only its sha256 hash.
    assert inserted["doc"]["token_hash"] != raw
    assert len(inserted["doc"]["token_hash"]) == 64  # hex sha256
    assert inserted["doc"]["audience"] == "rider"
    assert row_id == "rtk-new-001"
    assert expires_at > datetime.now(timezone.utc)
    # Raw token has real entropy — not empty, not the hash.
    assert raw and len(raw) > 32


@pytest.mark.asyncio
async def test_issue_refresh_token_truncates_long_user_agent_and_ip():
    captured = {}

    async def _insert_one(table, doc):
        captured.update(doc)
        return {"id": "rtk-1"}

    with patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)):
        from utils.refresh_tokens import issue_refresh_token

        await issue_refresh_token(
            "user-1",
            user_agent="X" * 1000,
            ip="Y" * 1000,
        )

    assert len(captured["user_agent"]) == 512
    assert len(captured["ip"]) == 64


@pytest.mark.asyncio
async def test_issue_refresh_token_empty_user_agent_and_ip_become_none():
    captured = {}

    async def _insert_one(table, doc):
        captured.update(doc)
        return {"id": "rtk-1"}

    with patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)):
        from utils.refresh_tokens import issue_refresh_token

        await issue_refresh_token("user-1", user_agent="", ip="")

    assert captured["user_agent"] is None
    assert captured["ip"] is None


@pytest.mark.asyncio
async def test_issue_refresh_token_with_replaces_chains_the_old_row():
    update_calls = []

    async def _insert_one(table, doc):
        return {"id": "rtk-new-002"}

    async def _update_one(table, query, payload):
        update_calls.append((table, query, payload))
        return True

    with (
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_update_one)),
    ):
        from utils.refresh_tokens import issue_refresh_token

        _, row_id, _ = await issue_refresh_token("user-1", replaces="rtk-old-001")

    assert row_id == "rtk-new-002"
    assert len(update_calls) == 1
    table, query, payload = update_calls[0]
    assert table == "refresh_tokens"
    assert query == {"id": "rtk-old-001"}
    assert payload["$set"]["replaced_by"] == "rtk-new-002"
    assert payload["$set"]["revoked_at"]


@pytest.mark.asyncio
async def test_issue_refresh_token_without_replaces_never_calls_update():
    update_mock = AsyncMock(return_value=True)

    with (
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(return_value={"id": "rtk-1"})),
        patch("utils.refresh_tokens.db.update_one", update_mock),
    ):
        from utils.refresh_tokens import issue_refresh_token

        await issue_refresh_token("user-1")

    update_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# lookup_refresh_token — remaining branches
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_refresh_token_empty_raw_returns_none_without_db_call():
    find_mock = AsyncMock()

    with patch("utils.refresh_tokens.db.find_one", find_mock):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("")

    assert result is None
    find_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_refresh_token_db_error_returns_none():
    with patch(
        "utils.refresh_tokens.db.find_one",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("some-raw-token")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_refresh_token_not_found_returns_none():
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=None)):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("unknown-raw")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_refresh_token_unparseable_expiry_returns_none():
    row = {
        "id": "rtk-1",
        "revoked_at": None,
        "expires_at": "not-a-timestamp",
    }
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("raw")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_refresh_token_naive_expiry_treated_as_utc_and_valid():
    future_naive = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
    row = {
        "id": "rtk-1",
        "revoked_at": None,
        "expires_at": future_naive,
    }
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("raw")

    assert result == row


@pytest.mark.asyncio
async def test_lookup_refresh_token_expired_returns_none():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    row = {"id": "rtk-1", "revoked_at": None, "expires_at": past}
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("raw")

    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# revoke_refresh_token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_refresh_token_empty_raw_returns_false():
    find_mock = AsyncMock()
    with patch("utils.refresh_tokens.db.find_one", find_mock):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("") is False
    find_mock.assert_not_called()


@pytest.mark.asyncio
async def test_revoke_refresh_token_lookup_error_returns_false():
    with patch(
        "utils.refresh_tokens.db.find_one",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("raw") is False


@pytest.mark.asyncio
async def test_revoke_refresh_token_not_found_returns_false():
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=None)):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("raw") is False


@pytest.mark.asyncio
async def test_revoke_refresh_token_already_revoked_returns_false():
    row = {"id": "rtk-1", "revoked_at": "2026-01-01T00:00:00+00:00"}
    with patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("raw") is False


@pytest.mark.asyncio
async def test_revoke_refresh_token_success_returns_true():
    row = {"id": "rtk-1", "revoked_at": None}
    update_mock = AsyncMock(return_value=True)
    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)),
        patch("utils.refresh_tokens.db.update_one", update_mock),
    ):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("raw") is True
    update_mock.assert_awaited_once()
    _, query, payload = update_mock.await_args.args
    assert query == {"id": "rtk-1"}
    assert payload["$set"]["revoked_at"]


@pytest.mark.asyncio
async def test_revoke_refresh_token_update_error_returns_false():
    row = {"id": "rtk-1", "revoked_at": None}
    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)),
        patch(
            "utils.refresh_tokens.db.update_one",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        from utils.refresh_tokens import revoke_refresh_token

        assert await revoke_refresh_token("raw") is False


# ─────────────────────────────────────────────────────────────────────────────
# revoke_all_for_user
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_all_for_user_scan_error_returns_zero():
    with patch(
        "utils.refresh_tokens.db.get_rows",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        from utils.refresh_tokens import revoke_all_for_user

        assert await revoke_all_for_user("user-1") == 0


@pytest.mark.asyncio
async def test_revoke_all_for_user_revokes_only_active_rows():
    rows = [
        {"id": "rtk-1", "revoked_at": None},
        {"id": "rtk-2", "revoked_at": "2026-01-01T00:00:00+00:00"},  # already revoked
        {"id": "rtk-3", "revoked_at": None},
    ]
    update_calls = []

    async def _update_one(table, query, payload):
        update_calls.append((table, query, payload))
        return True

    with (
        patch("utils.refresh_tokens.db.get_rows", AsyncMock(return_value=rows)),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_update_one)),
    ):
        from utils.refresh_tokens import revoke_all_for_user

        count = await revoke_all_for_user("user-1")

    assert count == 2
    revoked_ids = {q["id"] for _, q, _ in update_calls}
    assert revoked_ids == {"rtk-1", "rtk-3"}


@pytest.mark.asyncio
async def test_revoke_all_for_user_no_rows_returns_zero():
    with patch("utils.refresh_tokens.db.get_rows", AsyncMock(return_value=[])):
        from utils.refresh_tokens import revoke_all_for_user

        assert await revoke_all_for_user("user-1") == 0
