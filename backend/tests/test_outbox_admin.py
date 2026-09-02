"""Operator CLI for transactional-outbox dead letters. No live Supabase."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _dead_row(**extra):
    row = {
        "id": "ob-dead-1",
        "topic": "ride_receipt.v1",
        "dedupe_key": "auto:ride-1",
        "payload": {"ride_id": "ride-1", "email": "secret@example.com"},
        "status": "dead_lettered",
        "attempt_count": 8,
        "max_attempts": 8,
        "last_error_code": "provider_unavailable",
        "redrive_count": 0,
        "dead_lettered_at": "2026-09-01T12:00:00+00:00",
        "created_at": "2026-09-01T11:00:00+00:00",
        "updated_at": "2026-09-01T12:00:00+00:00",
        "last_error": "SES 500 body including 555-0100",
    }
    row.update(extra)
    return row


def test_public_view_drops_pii_and_exception_text():
    from scripts.outbox_admin import public_view

    view = public_view(_dead_row())
    dumped = json.dumps(view)
    assert "secret@example.com" not in dumped
    assert "555-0100" not in dumped
    assert "SES 500" not in dumped
    assert "email" not in view
    assert view["ride_id"] == "ride-1"
    assert view["id"] == "ob-dead-1"
    assert view["topic"] == "ride_receipt.v1"
    assert view["last_error_code"] == "provider_unavailable"


async def test_list_dead_returns_safe_projections():
    from scripts.outbox_admin import cmd_list_dead

    with patch("services.outbox.list_dead_letters", AsyncMock(return_value=[_dead_row()])):
        rows = await cmd_list_dead()
    assert len(rows) == 1
    assert rows[0]["ride_id"] == "ride-1"
    assert "email" not in rows[0]


async def test_show_missing_returns_none():
    from scripts.outbox_admin import cmd_show

    with patch("services.outbox.get_message", AsyncMock(return_value=None)):
        assert await cmd_show("missing") is None


async def test_redrive_requires_dead_lettered_ok_from_rpc():
    from scripts.outbox_admin import cmd_redrive

    with patch("services.outbox.redrive", AsyncMock(return_value=False)) as redrive:
        assert await cmd_redrive("ob-1", "ops-42") is False
    redrive.assert_awaited_once_with("ob-1", "ops-42")

    with patch("services.outbox.redrive", AsyncMock(return_value=True)):
        assert await cmd_redrive("ob-1", "ops-42") is True


def test_cli_redrive_requires_actor_id():
    from scripts.outbox_admin import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["redrive", "ob-1"])
    args = parser.parse_args(["redrive", "ob-1", "--actor-id", "ops-42"])
    assert args.actor_id == "ops-42"
    assert args.id == "ob-1"
