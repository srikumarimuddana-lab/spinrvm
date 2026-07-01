"""Tests for the Help Desk AI-suggest route's thread hydration.

The /conversations list Zoho returns only carries a truncated ``summary`` per
email thread; the route must fetch each recent thread's full body so the AI
drafts from the whole mail chain, not snippets. A per-thread fetch failure must
degrade to the summary rather than failing the whole suggestion.
"""

from __future__ import annotations

import pytest

import routes.admin.support_tickets as st
from services.zoho_desk_service import ZohoDeskError

pytestmark = pytest.mark.anyio


async def test_hydrate_replaces_summary_with_full_body(monkeypatch):
    async def _get_thread(ticket_id, thread_id):
        return {"id": thread_id, "content": f"FULL BODY {thread_id}"}

    monkeypatch.setattr(st.zoho, "get_thread", _get_thread)

    convs = [
        {"id": "t1", "type": "thread", "direction": "in", "summary": "snippet 1"},
        {"id": "c1", "type": "comment", "content": "internal note"},
        {"id": "t2", "type": "thread", "direction": "out", "summary": "snippet 2"},
    ]
    out = await st._hydrate_thread_bodies("ticket-1", convs)

    assert out[0]["content"] == "FULL BODY t1"
    assert out[2]["content"] == "FULL BODY t2"
    # Comments are not re-fetched; their content is left untouched.
    assert out[1]["content"] == "internal note"


async def test_hydrate_falls_back_to_summary_on_fetch_error(monkeypatch):
    async def _boom(ticket_id, thread_id):
        raise ZohoDeskError("Zoho Desk returned 404.", status=502)

    monkeypatch.setattr(st.zoho, "get_thread", _boom)

    convs = [{"id": "t1", "type": "thread", "direction": "in", "summary": "snippet"}]
    out = await st._hydrate_thread_bodies("ticket-1", convs)

    # No full content added; the summary entry is preserved so the draft still runs.
    assert out[0].get("content") is None
    assert out[0]["summary"] == "snippet"


async def test_hydrate_only_keeps_recent_window(monkeypatch):
    calls: list[str] = []

    async def _get_thread(ticket_id, thread_id):
        calls.append(thread_id)
        return {"content": f"body {thread_id}"}

    monkeypatch.setattr(st.zoho, "get_thread", _get_thread)

    convs = [
        {"id": f"t{i}", "type": "thread", "direction": "in", "summary": f"s{i}"}
        for i in range(st._AI_MAX_THREADS + 5)
    ]
    out = await st._hydrate_thread_bodies("ticket-1", convs)

    # Only the most recent window is hydrated (no Zoho call for older threads).
    assert len(out) == st._AI_MAX_THREADS
    assert len(calls) == st._AI_MAX_THREADS
