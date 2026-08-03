"""Coverage-closing tests for backend/services/guest_notification_service.py.

Companion to test_guest_sms.py (which pins the PII-safe-logging and SMS-body
contract for the two most common paths). This file closes the remaining
branches: the crash path in _send_guest_sms, _guest_recipient's guard
clauses, _company_name's no-id/exception paths, _ensure_tracking_token's
reuse-existing-token and mint-failure paths, the scheduled-ride SMS body, the
no-phone guard in notify_guest_booking_created, and notify_guest_driver_
arrived / notify_guest_cancelled's full send paths (previously only their
early-return guards were exercised).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

_GNS = "backend.services.guest_notification_service."

_RIDE = {
    "id": "ride_sms_1",
    "rider_id": "guest_u1",
    "corporate_account_id": "company_gb",
    "pickup_address": "123 Showroom Road, Saskatoon",
    "dropoff_address": "456 Home Street, Saskatoon",
    "pickup_otp": "4821",
    "shared_trip_token": "tok_abc123",
    "is_scheduled": False,
}

_GUEST = {"id": "guest_u1", "phone": "+13065550123", "is_guest": True, "first_name": "Pat"}


def _patches(*, guest=_GUEST, sms_result=None, company="Prairie Motors", update_ride=None, send_sms_side_effect=None):
    d = {
        _GNS + "get_app_settings": AsyncMock(
            return_value={
                "twilio_account_sid": "AC1",
                "twilio_auth_token": "t",
                "twilio_from_number": "+15550000000",
            }
        ),
        _GNS + "send_push_notification": AsyncMock(return_value=True),
        _GNS + "db_supabase.get_user_by_id": AsyncMock(return_value=guest),
        _GNS + "db_supabase.get_rows": AsyncMock(return_value=[{"id": "company_gb", "name": company}]),
        _GNS + "db_supabase.update_ride": update_ride or AsyncMock(return_value=None),
    }
    if send_sms_side_effect is not None:
        d[_GNS + "send_sms"] = AsyncMock(side_effect=send_sms_side_effect)
    else:
        d[_GNS + "send_sms"] = AsyncMock(return_value=sms_result or {"success": True})
    return d


def _start(patch_dict):
    patchers, mocks = [], {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


def _stop(patchers):
    for p in patchers:
        p.stop()


# ── _send_guest_sms crash path (57-63 in the source) ────────────────────


@pytest.mark.anyio
async def test_send_guest_sms_crash_is_swallowed_and_logged(caplog):
    """send_sms raising (not just returning success:False) must not raise
    into the caller — these helpers are spawn()ed off the hot path and are
    documented to never raise. The crash is logged with a redacted phone."""
    from backend.services.guest_notification_service import notify_guest_booking_created

    patchers, _ = _start(_patches(send_sms_side_effect=RuntimeError("twilio down")))
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_booking_created(
                dict(_RIDE), dict(_GUEST), customer_has_app=False, tracking_url="https://track.spinr.ca/t"
            )
    finally:
        _stop(patchers)

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "guest SMS crashed" in logged
    assert "3065550123" not in logged, "full phone must never reach logs even on crash"


# ── _guest_recipient guard clauses ───────────────────────────────────────


@pytest.mark.anyio
async def test_guest_recipient_no_rider_id_returns_none():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    ride = {**_RIDE}
    ride.pop("rider_id")
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_driver_arrived(ride)
    finally:
        _stop(patchers)
    # get_user_by_id must never be called — rider_id short-circuits first.
    mocks[_GNS + "db_supabase.get_user_by_id"].assert_not_awaited()
    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_guest_recipient_user_not_found_returns_none():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches(guest=None))
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_guest_recipient_db_error_is_swallowed_and_returns_none():
    """Fixed: `_guest_recipient` now wraps `db_supabase.get_user_by_id` in
    try/except, matching the module docstring's "never raise into caller"
    contract -- every other DB/network call in this file was already
    guarded, this one previously was not."""
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patches = _patches()
    patches[_GNS + "db_supabase.get_user_by_id"] = AsyncMock(side_effect=RuntimeError("db down"))
    patchers, mocks = _start(patches)
    try:
        await notify_guest_driver_arrived(dict(_RIDE))  # must not raise
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_guest_recipient_no_phone_returns_none():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    no_phone_guest = {**_GUEST, "phone": None}
    patchers, mocks = _start(_patches(guest=no_phone_guest))
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()


# ── _company_name branches ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_company_name_no_company_id_defaults():
    from backend.services.guest_notification_service import notify_guest_cancelled

    ride = {**_RIDE}
    ride.pop("corporate_account_id")
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_cancelled(ride)
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "Your company" in body
    mocks[_GNS + "db_supabase.get_rows"].assert_not_awaited()


@pytest.mark.anyio
async def test_company_name_lookup_exception_defaults(caplog):
    from backend.services.guest_notification_service import notify_guest_cancelled

    patch_dict = _patches()
    patch_dict[_GNS + "db_supabase.get_rows"] = AsyncMock(side_effect=RuntimeError("db down"))
    patchers, mocks = _start(patch_dict)
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_cancelled(dict(_RIDE))
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "Your company" in body
    assert "guest SMS: company name lookup failed" in " ".join(r.getMessage() for r in caplog.records)


# ── _ensure_tracking_token: reuse-existing vs mint-failure ──────────────


@pytest.mark.anyio
async def test_driver_assigned_reuses_existing_token_without_minting():
    from backend.services.guest_notification_service import notify_guest_driver_assigned

    driver = {"name": "Dana Driver", "license_plate": "ABC 123"}
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_driver_assigned(dict(_RIDE), driver)  # _RIDE already has shared_trip_token
    finally:
        _stop(patchers)
    # Token already present -> no mint, no update_ride call.
    mocks[_GNS + "db_supabase.update_ride"].assert_not_awaited()
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "tok_abc123" in body


@pytest.mark.anyio
async def test_driver_assigned_token_mint_failure_omits_tracking_link(caplog):
    from backend.services.guest_notification_service import notify_guest_driver_assigned

    ride = {**_RIDE}
    ride.pop("shared_trip_token")
    driver = {"name": "Dana Driver", "license_plate": "ABC 123"}
    patch_dict = _patches()
    patch_dict[_GNS + "db_supabase.update_ride"] = AsyncMock(side_effect=RuntimeError("db down"))
    patchers, mocks = _start(patch_dict)
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_driver_assigned(ride, driver)
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "Track:" not in body
    assert "guest SMS: tracking token mint failed" in " ".join(r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_driver_assigned_guard_when_not_a_guest():
    """notify_guest_driver_assigned must no-op (no token mint, no SMS) when
    the recipient isn't a guest anymore."""
    from backend.services.guest_notification_service import notify_guest_driver_assigned

    patchers, mocks = _start(_patches(guest={**_GUEST, "is_guest": False}))
    try:
        await notify_guest_driver_assigned(dict(_RIDE), {"name": "Dana"})
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()
    mocks[_GNS + "db_supabase.update_ride"].assert_not_awaited()


# ── notify_guest_booking_created: no-phone guard + scheduled body ───────


@pytest.mark.anyio
async def test_booking_no_phone_logs_and_returns(caplog):
    from backend.services.guest_notification_service import notify_guest_booking_created

    guest_no_phone = {**_GUEST, "phone": None}
    patchers, mocks = _start(_patches())
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_booking_created(dict(_RIDE), guest_no_phone, customer_has_app=False, tracking_url=None)
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()
    assert "has no customer phone" in " ".join(r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_booking_scheduled_ride_body_includes_local_time():
    from backend.services.guest_notification_service import notify_guest_booking_created

    ride = {**_RIDE, "is_scheduled": True, "scheduled_time": "2026-08-10T18:30:00+00:00"}
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_booking_created(ride, dict(_GUEST), customer_has_app=False, tracking_url="https://t/x")
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "booked you a ride for" in body
    assert "4821" in body
    # Scheduled body branch never appends the "Track your driver" line.
    assert "Track your driver" not in body


@pytest.mark.anyio
async def test_booking_scheduled_ride_naive_timestamp_assumed_utc():
    """A scheduled_time with no offset (naive) must still format cleanly —
    _local_time assumes UTC rather than raising."""
    from backend.services.guest_notification_service import notify_guest_booking_created

    ride = {**_RIDE, "is_scheduled": True, "scheduled_time": "2026-08-10T18:30:00"}
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_booking_created(ride, dict(_GUEST), customer_has_app=False, tracking_url="https://t/x")
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "booked you a ride for" in body


@pytest.mark.anyio
async def test_booking_scheduled_ride_unparseable_time_falls_back_to_raw():
    """_local_time's except branch: an unparseable scheduled_time string is
    echoed as-is rather than raising into the SMS-composition path."""
    from backend.services.guest_notification_service import notify_guest_booking_created

    ride = {**_RIDE, "is_scheduled": True, "scheduled_time": "not-a-real-timestamp"}
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_booking_created(ride, dict(_GUEST), customer_has_app=False, tracking_url="https://t/x")
    finally:
        _stop(patchers)
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "not-a-real-timestamp" in body


# ── notify_guest_driver_arrived / notify_guest_cancelled: full send path ─


@pytest.mark.anyio
async def test_driver_arrived_sends_sms_with_otp():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches())
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        _stop(patchers)
    sms = mocks[_GNS + "send_sms"]
    sms.assert_awaited_once()
    body = sms.call_args.args[1]
    assert "4821" in body
    assert sms.call_args.args[0] == "+13065550123"


@pytest.mark.anyio
async def test_driver_arrived_guard_when_not_a_guest():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches(guest={**_GUEST, "is_guest": False}))
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_cancelled_sends_sms_with_company_name():
    from backend.services.guest_notification_service import notify_guest_cancelled

    patchers, mocks = _start(_patches(company="Prairie Motors"))
    try:
        await notify_guest_cancelled(dict(_RIDE))
    finally:
        _stop(patchers)
    sms = mocks[_GNS + "send_sms"]
    sms.assert_awaited_once()
    body = sms.call_args.args[1]
    assert "Prairie Motors" in body
    assert "cancelled" in body


@pytest.mark.anyio
async def test_cancelled_guard_when_not_a_guest():
    from backend.services.guest_notification_service import notify_guest_cancelled

    patchers, mocks = _start(_patches(guest={**_GUEST, "is_guest": False}))
    try:
        await notify_guest_cancelled(dict(_RIDE))
    finally:
        _stop(patchers)
    mocks[_GNS + "send_sms"].assert_not_awaited()
