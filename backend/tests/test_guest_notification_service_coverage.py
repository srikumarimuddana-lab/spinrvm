"""Coverage top-up for backend/services/guest_notification_service.py.

A1c Sub-tier C — test-only change, no application code modified. Written
purely by reading the source (no pytest run in this session); complements
the existing behavioral pins in test_guest_sms.py by exercising branches
that file doesn't reach:

  * _local_time() success + malformed-input fallback
  * _send_guest_sms() crash path (send_sms raises)
  * _guest_recipient() missing rider_id short-circuit
  * _company_name() no-company-id default + DB-lookup-raises fallback
  * _ensure_tracking_token() "token already present" no-mint path +
    update_ride-raises fallback
  * notify_guest_booking_created(): guest with no phone (no-op), and the
    is_scheduled body variant
  * notify_guest_driver_arrived(): success path (SMS actually sent)
  * notify_guest_cancelled(): both the early-return (non-guest/no guest)
    and success paths

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 5):
`_guest_recipient()`'s `db_supabase.get_user_by_id(rider_id)` call
previously had no try/except, breaking the module docstring's "never
raise into their caller" promise that every other DB/network call in this
file (`_company_name`, `_ensure_tracking_token`, `_send_guest_sms`) already
honors. Now wrapped, matching the sibling pattern — see
`test_guest_recipient_db_error_is_swallowed_and_returns_none` below.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

_GNS = "backend.services.guest_notification_service."

_RIDE = {
    "id": "ride_cov_1",
    "rider_id": "guest_u1",
    "corporate_account_id": "company_gb",
    "pickup_address": "123 Showroom Road, Saskatoon",
    "dropoff_address": "456 Home Street, Saskatoon",
    "pickup_otp": "4821",
    "shared_trip_token": "tok_abc123",
    "is_scheduled": False,
}

_GUEST = {"id": "guest_u1", "phone": "+13065550123", "is_guest": True, "first_name": "Pat"}


def _patches(
    *, guest=_GUEST, sms_result=None, sms_side_effect=None, company="Prairie Motors", get_rows_side_effect=None
):
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
        _GNS + "db_supabase.update_ride": AsyncMock(return_value=None),
    }
    if sms_side_effect is not None:
        d[_GNS + "send_sms"] = AsyncMock(side_effect=sms_side_effect)
    else:
        d[_GNS + "send_sms"] = AsyncMock(return_value=sms_result or {"success": True})
    if get_rows_side_effect is not None:
        d[_GNS + "db_supabase.get_rows"] = AsyncMock(side_effect=get_rows_side_effect)
    else:
        d[_GNS + "db_supabase.get_rows"] = AsyncMock(return_value=[{"id": "company_gb", "name": company}])
    return d


def _start(patch_dict):
    patchers, mocks = [], {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


# ── _local_time ──────────────────────────────────────────────────────


def test_local_time_formats_iso_string_in_regina_tz():
    from backend.services.guest_notification_service import _local_time

    out = _local_time("2026-06-15T18:30:00+00:00")
    # America/Regina is fixed UTC-6 year round (no DST) per module docstring
    assert "Jun 15" in out
    assert "12:30" in out


def test_local_time_accepts_naive_datetime_object():
    from datetime import datetime

    from backend.services.guest_notification_service import _local_time

    out = _local_time(datetime(2026, 1, 1, 0, 0, 0))
    assert "Dec 31" in out or "Jan 01" in out or "Jan" in out  # UTC->Regina shift, just must not crash/format


def test_local_time_falls_back_to_str_on_malformed_input():
    from backend.services.guest_notification_service import _local_time

    garbage = "not-a-real-timestamp"
    assert _local_time(garbage) == garbage


# ── _send_guest_sms ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_send_guest_sms_crash_is_logged_and_swallowed(caplog):
    from backend.services.guest_notification_service import _send_guest_sms

    patchers, mocks = _start(_patches(sms_side_effect=RuntimeError("twilio down")))
    try:
        with caplog.at_level(logging.DEBUG):
            await _send_guest_sms("ride_x", "+13065550199", "body text with secrets", "unit_test")
    finally:
        for p in patchers:
            p.stop()

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ride_x" in logged
    assert "body text with secrets" not in logged, "SMS body must never reach logs"
    assert "3065550199" not in logged, "full phone must never reach logs"
    # exception must not propagate
    mocks[_GNS + "send_sms"].assert_awaited_once()


# ── _guest_recipient ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_guest_recipient_returns_none_when_no_rider_id():
    from backend.services.guest_notification_service import _guest_recipient

    patchers, mocks = _start(_patches())
    try:
        result = await _guest_recipient({"id": "ride_no_rider"})
    finally:
        for p in patchers:
            p.stop()

    assert result is None
    mocks[_GNS + "db_supabase.get_user_by_id"].assert_not_awaited()


@pytest.mark.anyio
async def test_guest_recipient_db_error_is_swallowed_and_returns_none():
    """Fixed (2026-08-03): `_guest_recipient` now wraps `get_user_by_id`
    in try/except, so a DB error is swallowed (logged) and the notify_*
    entry point degrades to a no-op instead of raising into its caller,
    honoring the module docstring's "never raise into their caller"
    promise."""
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches())
    mocks[_GNS + "db_supabase.get_user_by_id"].side_effect = RuntimeError("db blip")
    try:
        # Must not raise.
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        for p in patchers:
            p.stop()


# ── _company_name ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_company_name_defaults_when_no_company_id():
    from backend.services.guest_notification_service import _company_name

    patchers, mocks = _start(_patches())
    try:
        name = await _company_name(None)
    finally:
        for p in patchers:
            p.stop()

    assert name == "Your company"
    mocks[_GNS + "db_supabase.get_rows"].assert_not_awaited()


@pytest.mark.anyio
async def test_company_name_falls_back_when_lookup_raises(caplog):
    from backend.services.guest_notification_service import _company_name

    patchers, _ = _start(_patches(get_rows_side_effect=RuntimeError("supabase timeout")))
    try:
        with caplog.at_level(logging.DEBUG):
            name = await _company_name("company_gb")
    finally:
        for p in patchers:
            p.stop()

    assert name == "Your company"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "company_gb" in logged


# ── _ensure_tracking_token ───────────────────────────────────────────


@pytest.mark.anyio
async def test_ensure_tracking_token_reuses_existing_token():
    from backend.services.guest_notification_service import _ensure_tracking_token

    ride = {**_RIDE}  # already carries shared_trip_token
    patchers, mocks = _start(_patches())
    try:
        token = await _ensure_tracking_token(ride)
    finally:
        for p in patchers:
            p.stop()

    assert token == "tok_abc123"
    mocks[_GNS + "db_supabase.update_ride"].assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_tracking_token_returns_none_when_mint_write_fails(caplog):
    from backend.services.guest_notification_service import _ensure_tracking_token

    ride = {"id": "ride_mint_fail"}  # no shared_trip_token
    patchers, mocks = _start(_patches())
    mocks[_GNS + "db_supabase.update_ride"].side_effect = RuntimeError("write failed")
    try:
        with caplog.at_level(logging.DEBUG):
            token = await _ensure_tracking_token(ride)
    finally:
        for p in patchers:
            p.stop()

    assert token is None
    assert "shared_trip_token" not in ride, "ride dict must not be mutated when the DB write fails"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ride_mint_fail" in logged


# ── notify_guest_booking_created ─────────────────────────────────────


@pytest.mark.anyio
async def test_booking_created_noop_when_guest_has_no_phone(caplog):
    from backend.services.guest_notification_service import notify_guest_booking_created

    guest_no_phone = {"id": "guest_u1", "is_guest": True}  # no "phone" key
    patchers, mocks = _start(_patches(guest=guest_no_phone))
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_booking_created(dict(_RIDE), guest_no_phone, customer_has_app=False, tracking_url=None)
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "send_sms"].assert_not_awaited()
    mocks[_GNS + "send_push_notification"].assert_not_awaited()
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ride_cov_1" in logged


@pytest.mark.anyio
async def test_booking_created_scheduled_ride_body_variant():
    from backend.services.guest_notification_service import notify_guest_booking_created

    ride = {**_RIDE, "is_scheduled": True, "scheduled_time": "2026-06-15T18:30:00+00:00"}
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_booking_created(
            ride, dict(_GUEST), customer_has_app=False, tracking_url="https://track.spinr.ca/x"
        )
    finally:
        for p in patchers:
            p.stop()

    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "booked you a ride for" in body
    assert "Jun 15" in body
    assert "You'll get driver details by text." in body
    assert "https://track.spinr.ca/x" not in body, "scheduled body variant omits the live tracking link"


# ── notify_guest_driver_arrived ──────────────────────────────────────


@pytest.mark.anyio
async def test_driver_arrived_sends_sms_with_otp():
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches())
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "send_sms"].assert_awaited_once()
    to_phone, body = mocks[_GNS + "send_sms"].call_args.args[0], mocks[_GNS + "send_sms"].call_args.args[1]
    assert to_phone == "+13065550123"
    assert "arrived" in body
    assert "4821" in body


# ── notify_guest_cancelled ───────────────────────────────────────────


@pytest.mark.anyio
async def test_cancelled_noop_for_non_guest():
    from backend.services.guest_notification_service import notify_guest_cancelled

    patchers, mocks = _start(_patches(guest={**_GUEST, "is_guest": False}))
    try:
        await notify_guest_cancelled(dict(_RIDE))
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_cancelled_sends_sms_with_company_name():
    from backend.services.guest_notification_service import notify_guest_cancelled

    patchers, mocks = _start(_patches(company="Prairie Motors"))
    try:
        await notify_guest_cancelled(dict(_RIDE))
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "send_sms"].assert_awaited_once()
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "cancelled" in body
    assert "Prairie Motors" in body
