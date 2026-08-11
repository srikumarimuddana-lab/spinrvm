"""Rider account emails: welcome, email-change security notice, deletion.

The email-change case is the one that matters most. Changing the address on an
account sent nothing to either side, so an attacker who took over an account
could relocate it silently — the real owner's only remaining contact point got
no warning at all.
"""

from unittest.mock import AsyncMock, patch

import pytest

import routes.users as users_route
import utils.rider_emails as re_mod

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_CREATED = "2026-01-01T00:00:00+00:00"
_NEW_PROFILE = {"id": "u1", "phone": "+13065550100", "profile_complete": False, "created_at": _CREATED}
_COMPLETE_PROFILE = {
    "id": "u1",
    "phone": "+13065550100",
    "profile_complete": True,
    "email": "old@example.com",
    "created_at": _CREATED,
}


def _record_and_close(coro):
    coro.close()
    return None


def _request(email="new@example.com"):
    from schemas import CreateProfileRequest

    return CreateProfileRequest(first_name="Sam", last_name="Rider", email=email, gender="Other")


async def _create_profile(current_user, request_email="new@example.com"):
    """Drive POST /users/profile with the DB and both senders stubbed."""
    welcome = AsyncMock(return_value=True)
    changed = AsyncMock(return_value=True)
    updated = {**current_user, "email": request_email, "profile_complete": True, "first_name": "Sam"}

    with (
        patch.object(users_route.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(users_route.db_supabase, "update_one", AsyncMock()),
        patch.object(users_route.db_supabase, "get_user_by_id", AsyncMock(return_value=updated)),
        patch.object(users_route, "send_welcome_email", welcome),
        patch.object(users_route, "send_email_changed_notice", changed),
        # The senders are AsyncMocks, which record the call when invoked —
        # before any await — so closing the coroutine keeps the assertion
        # honest without leaving an un-awaited coroutine behind.
        patch.object(users_route, "spawn", _record_and_close),
    ):
        await users_route.create_profile(_request(request_email), current_user=current_user)
    return welcome, changed


# --- Welcome (R4) ----------------------------------------------------------


async def test_first_time_setup_sends_a_welcome():
    welcome, changed = await _create_profile(_NEW_PROFILE)
    welcome.assert_called_once()
    changed.assert_not_called()


async def test_welcome_is_not_resent_on_a_later_profile_edit():
    # profile_complete already True — this is an edit, not a signup.
    welcome, _ = await _create_profile(_COMPLETE_PROFILE, request_email="old@example.com")
    welcome.assert_not_called()


# --- Email change (R7) -----------------------------------------------------


async def test_changing_the_email_notifies_the_old_address():
    _, changed = await _create_profile(_COMPLETE_PROFILE, request_email="new@example.com")
    changed.assert_called_once()
    assert changed.call_args.args[1] == "old@example.com"


async def test_unchanged_email_on_an_edit_sends_nothing():
    welcome, changed = await _create_profile(_COMPLETE_PROFILE, request_email="old@example.com")
    welcome.assert_not_called()
    changed.assert_not_called()


async def test_first_time_setup_does_not_also_fire_the_change_notice():
    # old_email is empty at signup; a "your address was changed" notice there
    # would be nonsense.
    welcome, changed = await _create_profile(_NEW_PROFILE)
    welcome.assert_called_once()
    changed.assert_not_called()


# --- Copy and routing ------------------------------------------------------


async def _capture(coro_fn):
    """Run a rider_emails sender with the policy layer stubbed; return kwargs."""
    send = AsyncMock(return_value=True)
    with (
        patch.object(re_mod, "send_lifecycle_email", send),
        patch.object(re_mod, "resolve_recipient", AsyncMock(return_value=_COMPLETE_PROFILE)),
    ):
        await coro_fn()
    return send.await_args.kwargs


async def test_change_notice_is_addressed_to_the_old_address_not_the_new_one():
    kwargs = await _capture(
        lambda: re_mod.send_email_changed_notice(
            {"id": "u1", "first_name": "Sam", "email": "new@example.com"}, "old@example.com"
        )
    )
    assert kwargs["to_override"] == "old@example.com"


async def test_change_notice_tells_the_reader_what_to_do_if_it_was_not_them():
    kwargs = await _capture(
        lambda: re_mod.send_email_changed_notice({"id": "u1", "email": "new@example.com"}, "old@example.com")
    )
    body = kwargs["rendered"].text
    assert "did not" in body and "support@spinr.ca" in body


async def test_change_notice_skips_when_there_was_no_old_address():
    send = AsyncMock(return_value=True)
    with patch.object(re_mod, "send_lifecycle_email", send):
        result = await re_mod.send_email_changed_notice({"id": "u1"}, "")
    assert result is False
    send.assert_not_awaited()


async def test_welcome_copy_states_the_zero_commission_model_and_tax_lines():
    kwargs = await _capture(lambda: re_mod.send_welcome_email({"id": "u1", "first_name": "Sam"}))
    body = kwargs["rendered"].text
    assert "0% commission" in body
    assert "GST" in body and "PST" in body


async def test_deletion_notice_states_the_retention_carve_out():
    # PIPEDA erasure here is satisfied by a lawful-retention carve-out, so the
    # rider must be told what is kept and for how long — not just "deleted".
    kwargs = await _capture(
        lambda: re_mod.send_account_deletion_notice({"id": "u1", "first_name": "Sam"}, "2033-08-08T00:00:00Z")
    )
    body = kwargs["rendered"].text
    assert "seven" in body
    assert "2033-08-08" in body
    assert "reactivate" in body


async def test_account_emails_are_all_transactional():
    from utils.email_notifications import EmailClass

    for fn in (
        lambda: re_mod.send_welcome_email({"id": "u1"}),
        lambda: re_mod.send_account_deletion_notice({"id": "u1"}, "2033-01-01"),
        lambda: re_mod.send_email_changed_notice({"id": "u1"}, "old@example.com"),
    ):
        kwargs = await _capture(fn)
        assert kwargs["email_class"] is EmailClass.TRANSACTIONAL


async def test_account_emails_are_branded():
    kwargs = await _capture(lambda: re_mod.send_welcome_email({"id": "u1"}))
    assert "/api/v1/branding/spinr-logo.png" in kwargs["rendered"].html


async def test_sender_failure_never_propagates():
    with patch.object(re_mod, "send_lifecycle_email", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await re_mod.send_welcome_email({"id": "u1"}) is False


# --- New-device sign-in notice (ACTION_ITEMS.md N15/R8) --------------------


async def test_new_device_notice_is_sent_to_the_current_address():
    kwargs = await _capture(lambda: re_mod.send_new_device_notice({"id": "u1", "email": "current@example.com"}))
    assert kwargs["to_override"] is None  # goes to the resolved recipient's own address, not overridden


async def test_new_device_notice_tells_the_reader_what_to_do_if_it_was_not_them():
    kwargs = await _capture(lambda: re_mod.send_new_device_notice({"id": "u1"}))
    body = kwargs["rendered"].text
    assert "haven't seen before" in body
    assert "contact" in body.lower()


async def test_new_device_notice_is_transactional():
    from utils.email_notifications import EmailClass

    kwargs = await _capture(lambda: re_mod.send_new_device_notice({"id": "u1"}))
    assert kwargs["email_class"] is EmailClass.TRANSACTIONAL


async def test_new_device_notice_failure_never_propagates():
    with patch.object(re_mod, "send_lifecycle_email", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await re_mod.send_new_device_notice({"id": "u1"}) is False
