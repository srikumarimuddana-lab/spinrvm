"""Conversation persistence: ownership scoping + PIPEDA-minimal writes.

Pins: foreign conversation ids read as missing, history loads newest-N in
chronological order, message rows carry tool NAMES only (the orchestrator
never hands tool payloads to this layer), first user message becomes the
title, and delete is owner-checked with explicit message cleanup.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import conversations

USER_ID = "rider-1"
CONV = {"id": "conv-1", "user_id": USER_ID, "audience": "rider", "title": None}


def _db(**overrides):
    mocks = {
        "find_one": AsyncMock(return_value=dict(CONV)),
        "get_rows": AsyncMock(return_value=[]),
        "insert_one": AsyncMock(),
        "update_one": AsyncMock(),
        "delete_one": AsyncMock(),
        "delete_many": AsyncMock(),
    }
    mocks.update(overrides)
    return patch.multiple(conversations.db_supabase, **mocks)


class TestGetOrCreate:
    @pytest.mark.anyio
    async def test_existing_conversation_owner_checked(self):
        find_one = AsyncMock(return_value=dict(CONV))
        with _db(find_one=find_one):
            conv = await conversations.get_or_create_conversation(USER_ID, "conv-1")
        assert conv["id"] == "conv-1"
        assert find_one.await_args.args[1] == {"id": "conv-1", "user_id": USER_ID}

    @pytest.mark.anyio
    async def test_foreign_conversation_reads_as_missing(self):
        with _db(find_one=AsyncMock(return_value=None)):
            conv = await conversations.get_or_create_conversation(USER_ID, "someone-elses")
        assert conv is None

    @pytest.mark.anyio
    async def test_creates_fresh_conversation(self):
        insert = AsyncMock()
        with _db(insert_one=insert):
            conv = await conversations.get_or_create_conversation(USER_ID, None, audience="driver")
        assert conv["user_id"] == USER_ID
        assert conv["audience"] == "driver"
        insert.assert_awaited_once()
        assert insert.await_args.args[0] == "ai_conversations"


class TestHistory:
    @pytest.mark.anyio
    async def test_newest_n_in_chronological_order(self):
        # DB returns newest-first (order created_at desc); history must flip it.
        rows = [
            {"role": "assistant", "content": "second reply", "created_at": "t3"},
            {"role": "user", "content": "second question", "created_at": "t2"},
            {"role": "user", "content": "first question", "created_at": "t1"},
        ]
        get_rows = AsyncMock(return_value=rows)
        with _db(get_rows=get_rows):
            history = await conversations.load_history("conv-1", max_messages=3)
        assert [m["content"] for m in history] == [
            "first question",
            "second question",
            "second reply",
        ]
        assert get_rows.await_args.kwargs["limit"] == 3
        # only role+content reach the model context
        assert set(history[0].keys()) == {"role", "content"}


class TestAppend:
    @pytest.mark.anyio
    async def test_assistant_row_carries_names_and_usage_only(self):
        insert = AsyncMock()
        with _db(insert_one=insert):
            await conversations.append_message(
                dict(CONV, title="existing"),
                "assistant",
                "Your driver is 3 minutes away.",
                tool_names=["get_active_ride"],
                usage={"input_tokens": 900, "output_tokens": 60},
                provider="anthropic",
                model="claude-haiku-4-5",
            )
        row = insert.await_args.args[1]
        assert row["tool_names"] == ["get_active_ride"]
        assert row["input_tokens"] == 900
        assert row["provider"] == "anthropic"
        # no field exists for tool arguments/results — assert nothing leaked
        assert "arguments" not in str(row)

    @pytest.mark.anyio
    async def test_first_user_message_sets_title(self):
        update = AsyncMock()
        conv = dict(CONV)
        with _db(update_one=update):
            await conversations.append_message(conv, "user", "x" * 100)
        updates = update.await_args.args[2]
        assert len(updates["title"]) == 60
        assert conv["title"] == updates["title"]

    @pytest.mark.anyio
    async def test_existing_title_not_overwritten(self):
        update = AsyncMock()
        with _db(update_one=update):
            await conversations.append_message(dict(CONV, title="kept"), "user", "another q")
        assert "title" not in update.await_args.args[2]


class TestListAndRead:
    @pytest.mark.anyio
    async def test_list_scoped_and_shaped(self):
        get_rows = AsyncMock(return_value=[{"id": "conv-1", "title": None, "updated_at": "t1", "user_id": USER_ID}])
        with _db(get_rows=get_rows):
            out = await conversations.list_conversations(USER_ID)
        assert get_rows.await_args.args[1] == {"user_id": USER_ID}
        assert out == [{"id": "conv-1", "title": "New conversation", "updated_at": "t1"}]

    @pytest.mark.anyio
    async def test_get_messages_foreign_conv_is_none(self):
        with _db(find_one=AsyncMock(return_value=None)):
            assert await conversations.get_messages("conv-1", USER_ID) is None


class TestDelete:
    @pytest.mark.anyio
    async def test_owner_checked_delete(self):
        delete_one = AsyncMock()
        delete_many = AsyncMock()
        with _db(delete_one=delete_one, delete_many=delete_many):
            ok = await conversations.delete_conversation("conv-1", USER_ID)
        assert ok is True
        delete_many.assert_awaited_once_with("ai_messages", {"conversation_id": "conv-1"})
        delete_one.assert_awaited_once_with("ai_conversations", {"id": "conv-1"})

    @pytest.mark.anyio
    async def test_foreign_delete_refused(self):
        delete_one = AsyncMock()
        with _db(find_one=AsyncMock(return_value=None), delete_one=delete_one):
            ok = await conversations.delete_conversation("conv-1", USER_ID)
        assert ok is False
        delete_one.assert_not_awaited()
