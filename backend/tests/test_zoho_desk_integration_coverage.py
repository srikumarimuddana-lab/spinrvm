"""
Coverage top-up for services/zoho_desk_integration.py.

A1c Sub-tier C — test-only change, no application code modified. Written by
reading the source only; not run locally (see task constraints — the full
suite runs once, at the very end, by someone else).

Targets the specific gap left after test_zoho_desk.py (74% / 33 missing
stmts): the untested `create_ticket_for_complaint` / `create_ticket_for_flag`
bodies (and the `_link_ticket` contact-user-fetch branch they exercise), the
`_split_name` empty-string edge, the `_link_ticket` / lost-and-found /
dispute error handlers (both `ZohoDeskError` and bare `Exception`), the
`close_linked_records` empty-input early return + already-closed skip
branches + reverse-close exception handler, and the `create_support_ticket`
email-backfill + transcript-append branches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.zoho_desk_integration as integ
from services.zoho_desk_service import ZohoDeskError


def _db(find_one=None, get_rows=None, update_one=None):
    db = MagicMock()
    db.find_one = find_one or AsyncMock(return_value=None)
    db.get_rows = get_rows or AsyncMock(return_value=[])
    db.update_one = update_one or AsyncMock(return_value=None)
    return db


# --------------------------------------------------------------------------
# _split_name
# --------------------------------------------------------------------------


def test_split_name_empty_string_returns_empty_and_none():
    # Line 38: no parts at all -> ("", None), not the "single-token" shape.
    assert integ._split_name("") == ("", None)
    assert integ._split_name("   ") == ("", None)


def test_split_name_single_token_has_no_last_name():
    assert integ._split_name("Cher") == ("Cher", None)


# --------------------------------------------------------------------------
# create_ticket_for_complaint / create_ticket_for_flag (previously wholly
# uncovered function bodies) + the _link_ticket contact-fetch branch (line 60)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complaint_ticket_against_rider_fetches_contact_and_links(monkeypatch):
    db = _db(
        find_one=AsyncMock(
            side_effect=[
                {"id": "default", "enabled": True},  # config
                {"id": "rider1", "name": "Pat Rider", "email": "pat@x.ca", "phone": "+1306"},  # contact
            ]
        )
    )
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-complaint"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    complaint = {
        "id": "cx1",
        "against_type": "rider",
        "against_id": "rider1",
        "category": "rude",
        "description": "was rude",
        "ride_id": "r9",
    }
    await integ.create_ticket_for_complaint(complaint, {"ride_code": "SPN-9"})

    created.assert_awaited_once()
    kwargs = created.call_args.kwargs
    assert kwargs["category"] == "Complaint"
    assert kwargs["email"] == "pat@x.ca"
    assert kwargs["first_name"] == "Pat"
    assert kwargs["last_name"] == "Rider"
    assert "Against: rider" in kwargs["description"]
    assert "SPN-9" in kwargs["description"]
    db.update_one.assert_awaited_once_with("complaints", {"id": "cx1"}, {"zoho_ticket_id": "zt-complaint"})


@pytest.mark.anyio
async def test_complaint_ticket_against_driver_has_no_contact(monkeypatch):
    # against != "rider" -> contact_user_id is None -> _link_ticket never
    # issues the second find_one for a contact record.
    find_one = AsyncMock(return_value={"id": "default", "enabled": True})
    db = _db(find_one=find_one)
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-complaint2"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_complaint(
        {"id": "cx2", "against_type": "driver", "against_id": "drv1", "category": "unsafe"}, None
    )

    created.assert_awaited_once()
    assert created.call_args.kwargs["email"] is None
    # Only the config lookup happened, no contact-user lookup.
    assert find_one.await_count == 1


@pytest.mark.anyio
async def test_flag_ticket_against_rider_fetches_contact_and_links(monkeypatch):
    db = _db(
        find_one=AsyncMock(
            side_effect=[
                {"id": "default", "enabled": True},  # config
                {"id": "rider2", "name": "Alex Q", "email": "alex@x.ca"},  # contact
            ]
        )
    )
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-flag"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    flag = {
        "id": "fx1",
        "target_type": "rider",
        "target_id": "rider2",
        "reason": "no-show",
        "description": "did not show",
        "ride_id": "r5",
    }
    await integ.create_ticket_for_flag(flag, {"ride_code": "SPN-5"})

    created.assert_awaited_once()
    kwargs = created.call_args.kwargs
    assert kwargs["category"] == "Flag"
    assert kwargs["email"] == "alex@x.ca"
    assert "Target: rider" in kwargs["description"]
    db.update_one.assert_awaited_once_with("flags", {"id": "fx1"}, {"zoho_ticket_id": "zt-flag"})


@pytest.mark.anyio
async def test_flag_ticket_against_driver_has_no_contact(monkeypatch):
    find_one = AsyncMock(return_value={"id": "default", "enabled": True})
    db = _db(find_one=find_one)
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-flag2"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_flag({"id": "fx2", "target_type": "driver", "target_id": "d1"}, None)

    assert find_one.await_count == 1
    assert created.call_args.kwargs["email"] is None


# --------------------------------------------------------------------------
# _link_ticket error handlers (lines 80-83), exercised via create_ticket_for_complaint
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_link_ticket_zoho_desk_error_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(
        integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("no department configured", status=503))
    )

    # Must not raise -- best-effort.
    await integ.create_ticket_for_complaint({"id": "cx3", "against_type": "driver"}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_link_ticket_generic_exception_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    # Must not raise -- best-effort, unlike create_support_ticket.
    await integ.create_ticket_for_complaint({"id": "cx4", "against_type": "driver"}, None)
    db.update_one.assert_not_awaited()


# --------------------------------------------------------------------------
# close_linked_records: empty input, already-closed skip branches, and the
# per-table reverse-close exception handler
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_linked_records_noop_on_empty_ids(monkeypatch):
    get_rows = AsyncMock(return_value=[])
    db = _db(get_rows=get_rows)
    monkeypatch.setattr(integ, "db_supabase", db)

    # Falsy entries are filtered out of the id list too (`if i`), so a list
    # of only falsy values also hits the early-return branch.
    await integ.close_linked_records([])
    await integ.close_linked_records(None)
    await integ.close_linked_records([None, "", 0])

    get_rows.assert_not_awaited()


@pytest.mark.anyio
async def test_close_linked_records_skips_already_closed_rows(monkeypatch):
    async def _get_rows(table, *a, **k):
        if table == "lost_and_found":
            # Already resolved -> status-column branch `continue`.
            return [{"id": "lf1", "status": "resolved", "zoho_ticket_id": "zt1"}]
        if table == "flags":
            # Already inactive -> is_active branch `continue`.
            return [{"id": "f1", "is_active": False, "zoho_ticket_id": "zt1"}]
        return []

    get_rows = AsyncMock(side_effect=_get_rows)
    update_one = AsyncMock()
    db = _db(get_rows=get_rows, update_one=update_one)
    monkeypatch.setattr(integ, "db_supabase", db)

    await integ.close_linked_records(["zt1"])

    # All five linked tables get queried, but no updates happen because both
    # non-empty rows returned were already in their "closed" state.
    assert get_rows.await_count == len(integ._LINKED_TABLES)
    update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_close_linked_records_continues_after_per_table_exception(monkeypatch):
    async def _get_rows(table, *a, **k):
        if table == "lost_and_found":
            raise RuntimeError("db unavailable")
        if table == "disputes":
            return [{"id": "d1", "status": "open", "zoho_ticket_id": "zt2"}]
        return []

    get_rows = AsyncMock(side_effect=_get_rows)
    update_one = AsyncMock()
    db = _db(get_rows=get_rows, update_one=update_one)
    monkeypatch.setattr(integ, "db_supabase", db)

    # Must not raise: the first table's failure is caught and logged, and the
    # loop still proceeds to the remaining tables.
    await integ.close_linked_records(["zt2"])

    assert get_rows.await_count == len(integ._LINKED_TABLES)
    update_one.assert_awaited_once()
    assert update_one.call_args.args[0] == "disputes"
    assert update_one.call_args.args[2]["status"] == "resolved"


# --------------------------------------------------------------------------
# create_ticket_for_lost_and_found error handlers (lines 235-239)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lost_and_found_zoho_desk_error_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("missing scope", status=503)))

    await integ.create_ticket_for_lost_and_found({"id": "c9"}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_lost_and_found_generic_exception_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    await integ.create_ticket_for_lost_and_found({"id": "c10"}, None)
    db.update_one.assert_not_awaited()


# --------------------------------------------------------------------------
# create_ticket_for_dispute error handlers (lines 283-286)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispute_zoho_desk_error_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=ZohoDeskError("rate limited", status=503)))

    await integ.create_ticket_for_dispute({"id": "dx1"}, None)
    db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_dispute_generic_exception_is_swallowed(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    monkeypatch.setattr(integ.zoho, "create_ticket", AsyncMock(side_effect=RuntimeError("boom")))

    await integ.create_ticket_for_dispute({"id": "dx2"}, None)
    db.update_one.assert_not_awaited()


# --------------------------------------------------------------------------
# create_support_ticket: email-backfill (lines 301-304) + transcript append
# (line 312)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_support_ticket_backfills_email_from_db_when_missing(monkeypatch):
    find_one = AsyncMock(
        side_effect=[
            {"id": "default", "enabled": True},  # config
            {"id": "u1", "email": "fetched@x.ca", "first_name": "Fetched", "last_name": "User", "phone": "+1"},
        ]
    )
    db = _db(find_one=find_one)
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-support", "ticketNumber": "9"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    # Caller passes a user dict with an id but no email -- the None values on
    # the caller's dict must not clobber the fetched record's real values
    # (merge order: fetched first, then only-non-None caller overrides).
    result = await integ.create_support_ticket(user={"id": "u1", "email": None, "name": None}, message="Need help")

    assert result["ticketNumber"] == "9"
    kwargs = created.call_args.kwargs
    assert kwargs["email"] == "fetched@x.ca"
    assert kwargs["first_name"] == "Fetched"
    assert kwargs["last_name"] == "User"
    assert find_one.await_count == 2


@pytest.mark.anyio
async def test_support_ticket_appends_transcript_to_description(monkeypatch):
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-support2"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(
        user={"id": "u2", "email": "u2@x.ca"},
        message="Trip fare looks wrong",
        transcript="bot: hi\nuser: my fare is wrong",
    )

    description = created.call_args.kwargs["description"]
    assert description.startswith("Trip fare looks wrong")
    assert "--- Chat transcript ---" in description
    assert "user: my fare is wrong" in description


@pytest.mark.anyio
async def test_support_ticket_empty_message_uses_placeholder_and_default_subject(monkeypatch):
    # Fixed (2026-08-03): when `message` is empty/whitespace-only, `subject`
    # falls through to the "Support request" default (unchanged). `description`
    # previously ALSO fell through to the literal "(no message)" placeholder
    # even when a transcript was supplied, so a blank-message escalation with
    # a real transcript produced a ticket body misleadingly prefixed with
    # "(no message)". Now the placeholder is only used when there is truly
    # nothing to show (no message AND no transcript) — see the
    # `test_support_ticket_empty_message_and_no_transcript_uses_placeholder`
    # sibling below for that case.
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-support3"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(user={"id": "u3", "email": "u3@x.ca"}, message="   ", transcript="user: hello?")

    kwargs = created.call_args.kwargs
    assert kwargs["subject"] == "Support — Support request"
    assert kwargs["description"] == "\n\n--- Chat transcript ---\nuser: hello?"


@pytest.mark.anyio
async def test_support_ticket_empty_message_and_no_transcript_uses_placeholder(monkeypatch):
    """No message AND no transcript -> the "(no message)" placeholder is
    still correct here, since there's genuinely nothing else to show."""
    db = _db(find_one=AsyncMock(return_value={"id": "default", "enabled": True}))
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt-support4"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_support_ticket(user={"id": "u4", "email": "u4@x.ca"}, message="   ", transcript=None)

    kwargs = created.call_args.kwargs
    assert kwargs["description"] == "(no message)"
