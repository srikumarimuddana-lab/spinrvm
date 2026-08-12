"""
Coverage-gap tests for services/zoho_desk_integration.py (A1c Sub-tier C,
Batch 9 pick).

test_zoho_desk.py already covers the happy paths for Lost & Found, disputes,
support escalation, safety, and reverse-close. This file closes the
remaining gaps found via `--cov-report=term-missing`:

  - `_split_name("")` -> ("", None)
  - `create_ticket_for_complaint` / `create_ticket_for_flag` (never exercised
    at all before this file — the misleadingly-named
    `test_complaint_and_safety_autocreate` in test_zoho_desk.py only calls
    `create_ticket_for_safety`)
  - `_link_ticket`'s already-linked idempotent skip, ZohoDeskError swallow,
    and generic-Exception swallow (best-effort contract from the module
    docstring: "never raise into the caller's request flow")
  - `close_linked_records`'s already-closed-record skip branches (both the
    is_active flag path and the status-column path) and its per-table
    exception swallow
  - `create_ticket_for_lost_and_found` / `create_ticket_for_dispute`'s
    ZohoDeskError + generic-Exception swallow paths, and the
    already-linked / disabled idempotent skips on the dispute helper
  - `create_support_ticket`'s missing-email user re-fetch merge and the
    transcript-appended-to-description branch
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.zoho_desk_integration as integ
from services.zoho_desk_service import ZohoDeskError


def _db(**overrides):
    db = MagicMock()
    db.find_one = AsyncMock(return_value=None)
    db.update_one = AsyncMock()
    db.get_rows = AsyncMock(return_value=[])
    for k, v in overrides.items():
        setattr(db, k, v)
    return db


def test_split_name_empty_returns_blank_and_none():
    assert integ._split_name("") == ("", None)
    assert integ._split_name("   ") == ("", None)


def test_split_name_single_word_has_no_last_name():
    assert integ._split_name("Cher") == ("Cher", None)


# ---------------------------------------------------------------------------
# create_ticket_for_complaint / create_ticket_for_flag — never exercised
# elsewhere; both delegate to _link_ticket.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complaint_autocreate_happy_against_rider(monkeypatch):
    db = _db(
        find_one=AsyncMock(
            side_effect=[
                {"id": "default", "enabled": True},
                {"id": "u1", "name": "Rider One", "email": "r1@x.ca"},
            ]
        )
    )
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "ztC1"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_complaint(
        {"id": "c1", "against_type": "rider", "against_id": "u1", "category": "rude", "description": "x"},
        {"ride_code": "SPN-5"},
    )
    created.assert_awaited_once()
    assert created.call_args.kwargs["category"] == "Complaint"
    assert created.call_args.kwargs["email"] == "r1@x.ca"
    db.update_one.assert_awaited_once_with("complaints", {"id": "c1"}, {"zoho_ticket_id": "ztC1"})


@pytest.mark.anyio
async def test_complaint_autocreate_against_driver_has_no_contact(monkeypatch):
    # against == "driver" -> contact_user_id stays None; user lookup skipped.
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "ztC2"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_complaint({"id": "c2", "against_type": "driver", "category": "unsafe"}, None)
    created.assert_awaited_once()
    assert created.call_args.kwargs["email"] is None
    # only the config lookup happened, never a "users" lookup
    db.find_one.assert_awaited_once_with("zoho_desk_config", {"id": "default"})


@pytest.mark.anyio
async def test_flag_autocreate_happy_against_rider(monkeypatch):
    db = _db(
        find_one=AsyncMock(
            side_effect=[
                {"id": "default", "enabled": True},
                {"id": "u2", "first_name": "Jo", "last_name": "Blow", "email": "jo@x.ca"},
            ]
        )
    )
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "ztF1"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_flag(
        {"id": "f1", "target_type": "rider", "target_id": "u2", "reason": "no-show"}, {"ride_code": "SPN-6"}
    )
    created.assert_awaited_once()
    assert created.call_args.kwargs["category"] == "Flag"
    assert created.call_args.kwargs["first_name"] == "Jo"
    db.update_one.assert_awaited_once_with("flags", {"id": "f1"}, {"zoho_ticket_id": "ztF1"})


# ---------------------------------------------------------------------------
# _link_ticket best-effort contract: idempotent skip + both exception paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_autocreate_skips_when_already_linked(monkeypatch):
    db = _db()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock()
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_flag({"id": "f9", "zoho_ticket_id": "already"}, None)
    created.assert_not_awaited()
    # never even checked config -- idempotent short-circuit is first
    db.find_one.assert_not_awaited()


@pytest.mark.anyio
async def test_link_ticket_swallows_zoho_desk_error(monkeypatch, caplog):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("no department", status=503)))

    # Must not raise -- this is the "Zoho outage must not break reporting"
    # guarantee from the module docstring.
    await integ.create_ticket_for_complaint({"id": "c3", "against_type": "driver"}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_link_ticket_swallows_generic_exception(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    await integ.create_ticket_for_complaint({"id": "c4", "against_type": "driver"}, None)
    db.update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# close_linked_records: already-closed skips (both branch shapes) + the
# per-table exception swallow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_linked_records_skips_already_inactive_flag(monkeypatch):
    async def _get_rows(table, *a, **k):
        if table == "flags":
            return [{"id": "f1", "is_active": False, "zoho_ticket_id": "zt1"}]
        return []

    db = _db(get_rows=AsyncMock(side_effect=_get_rows))
    monkeypatch.setattr(integ, "db_supabase", db)

    await integ.close_linked_records(["zt1"])
    # already inactive -> no update issued for it
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_close_linked_records_skips_already_closed_status(monkeypatch):
    async def _get_rows(table, *a, **k):
        if table == "disputes":
            return [{"id": "d1", "status": "resolved", "zoho_ticket_id": "zt1"}]
        return []

    db = _db(get_rows=AsyncMock(side_effect=_get_rows))
    monkeypatch.setattr(integ, "db_supabase", db)

    await integ.close_linked_records(["zt1"])
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_close_linked_records_noops_on_empty_id_list(monkeypatch):
    # Neither None nor [] should ever call get_rows.
    db = _db()
    monkeypatch.setattr(integ, "db_supabase", db)
    await integ.close_linked_records([])
    await integ.close_linked_records(None)
    db.get_rows.assert_not_awaited()


@pytest.mark.anyio
async def test_close_linked_records_one_table_failure_does_not_block_others(monkeypatch):
    calls = {"n": 0}

    async def _get_rows(table, *a, **k):
        calls["n"] += 1
        if table == "lost_and_found":
            raise RuntimeError("db down")
        if table == "flags":
            return [{"id": "f2", "is_active": True, "zoho_ticket_id": "zt1"}]
        return []

    db = _db(get_rows=AsyncMock(side_effect=_get_rows))
    monkeypatch.setattr(integ, "db_supabase", db)

    # Must not raise even though the first table in _LINKED_TABLES blows up.
    await integ.close_linked_records(["zt1"])
    # every table was still attempted (loop kept going past the exception)
    assert calls["n"] == len(integ._LINKED_TABLES)
    # the un-broken "flags" table still got its update
    db.update_one.assert_awaited_once_with("flags", {"id": "f2"}, {"is_active": False})


# ---------------------------------------------------------------------------
# create_ticket_for_lost_and_found: exception swallow paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lost_and_found_swallows_zoho_desk_error(monkeypatch):
    db = _db(find_one=AsyncMock(side_effect=[{"id": "default", "enabled": True}, {"id": "u1", "name": "A B"}]))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("nope", status=503)))

    await integ.create_ticket_for_lost_and_found({"id": "c5", "rider_user_id": "u1"}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_lost_and_found_swallows_generic_exception(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    await integ.create_ticket_for_lost_and_found({"id": "c6"}, None)
    db.update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# create_ticket_for_dispute: idempotent skips + both exception paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispute_autocreate_skips_when_already_linked(monkeypatch):
    db = _db()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock()
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_dispute({"id": "d9", "zoho_ticket_id": "already"}, None)
    created.assert_not_awaited()
    db.find_one.assert_not_awaited()


@pytest.mark.anyio
async def test_dispute_autocreate_skips_when_disabled(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": False}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock()
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_dispute({"id": "d10"}, None)
    created.assert_not_awaited()


@pytest.mark.anyio
async def test_dispute_autocreate_swallows_zoho_desk_error(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("no dept", status=503)))

    await integ.create_ticket_for_dispute({"id": "d11", "user_id": None}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_dispute_autocreate_swallows_generic_exception(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    await integ.create_ticket_for_dispute({"id": "d12", "user_id": None}, None)
    db.update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# create_support_ticket: missing-email re-fetch merge + transcript branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_support_ticket_refetches_user_when_email_missing(monkeypatch):
    db = _db(
        find_one=AsyncMock(
            side_effect=[
                {"id": "default", "enabled": True},
                {"id": "u1", "email": "fetched@x.ca", "phone": "+1000", "name": "Fetched User"},
            ]
        )
    )
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-sup", "ticketNumber": "1"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    # caller passed a user dict with an id but no email -> triggers the
    # "fetched = find_one(...); user = {**fetched, **non-None overrides}" merge
    await integ.create_support_ticket(user={"id": "u1", "email": None}, message="need help")

    created.assert_awaited_once()
    assert created.call_args.kwargs["email"] == "fetched@x.ca"
    assert created.call_args.kwargs["phone"] == "+1000"


@pytest.mark.anyio
async def test_support_ticket_appends_transcript_to_description(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-sup2"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(
        user={"id": "u2", "email": "u2@x.ca"}, message="Help!", transcript="bot: hi\nuser: help"
    )
    desc = created.call_args.kwargs["description"]
    assert desc.startswith("Help!")
    assert "--- Chat transcript ---" in desc
    assert "bot: hi" in desc


@pytest.mark.anyio
async def test_support_ticket_blank_message_uses_placeholder_and_default_subject(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-sup3"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(user={"id": "u3", "email": "u3@x.ca"}, message="   ")
    assert created.call_args.kwargs["description"] == "(no message)"
    assert created.call_args.kwargs["subject"] == "Support — Support request"


async def test_support_ticket_blank_message_with_transcript_leads_with_transcript(monkeypatch):
    """Fixed: `description` previously fell through to the literal
    "(no message)" placeholder even when a transcript was supplied, so a
    blank-message escalation with a real transcript produced a ticket body
    misleadingly prefixed with "(no message)". Now the placeholder is only
    used when there is truly nothing to show (no message AND no
    transcript) -- see test_support_ticket_blank_message_uses_placeholder_
    and_default_subject above for that case."""
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-sup4"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(user={"id": "u4", "email": "u4@x.ca"}, message="   ", transcript="user: hello?")
    assert created.call_args.kwargs["description"] == "\n\n--- Chat transcript ---\nuser: hello?"
