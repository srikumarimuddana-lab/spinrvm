"""A displaced phone must never refresh into the replacement phone's generation."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import refresh_tokens as tokens
from backend.utils.error_handling import DatabaseError


@pytest.mark.anyio
@pytest.mark.parametrize("revoked", [False, True])
async def test_old_generation_cannot_refresh_or_revoke_new_login(revoked):
    row = {
        "id": "old-refresh",
        "user_id": "user",
        "audience": "driver",
        "token_version": 2,
        "expires_at": "2099-01-01T00:00:00Z",
        "revoked_at": "2026-01-01T00:00:00Z" if revoked else None,
        "replaced_by": "old-successor" if revoked else None,
    }
    with (
        patch.object(tokens.db, "find_one", AsyncMock(side_effect=[row, {"token_version": 3}])),
        patch.object(tokens, "_handle_refresh_token_reuse", AsyncMock()) as cascade,
        patch.object(tokens, "_reuse_already_handled", AsyncMock(return_value=False)),
    ):
        assert await tokens.lookup_refresh_token("old-secret") is None
    cascade.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("bound,current,allowed", [(None, 0, True), (None, 1, False), (3, 3, True)])
async def test_legacy_and_bound_generation_validation(bound, current, allowed):
    row = {"user_id": "user", "audience": "rider", "token_version": bound, "expires_at": "2099-01-01T00:00:00Z"}
    with patch.object(tokens.db, "find_one", AsyncMock(side_effect=[row, {"token_version": current}])):
        result = await tokens.lookup_refresh_token("refresh-secret")
    assert (result is row) is allowed


@pytest.mark.anyio
async def test_issue_preserves_callers_generation_without_reading_new_login():
    with (
        patch.object(tokens.db, "insert_one", AsyncMock(return_value={"id": "next"})) as insert,
        patch.object(tokens.db, "update_one", AsyncMock()),
        patch.object(tokens.db, "find_one", AsyncMock()) as read,
    ):
        await tokens.issue_refresh_token("user", audience="driver", replaces="old", token_version=2)
    assert insert.call_args.args[1]["token_version"] == 2
    read.assert_not_awaited()


@pytest.mark.anyio
async def test_generation_lookup_database_failure_is_retryable():
    row = {"user_id": "user", "audience": "driver", "token_version": 2}
    with patch.object(tokens.db, "find_one", AsyncMock(side_effect=[row, RuntimeError("db down")])):
        with pytest.raises(DatabaseError) as caught:
            await tokens.lookup_refresh_token("refresh-secret")
    assert caught.value.status_code == 503
