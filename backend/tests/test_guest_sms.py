"""Guest notification lifecycle — SMS content and PII-safe logging.

The SMS body goes TO the data subject (their trip, their phone) so it may
carry addresses and the pickup OTP; the LOGS must never carry the body, a
full phone, a name, or an address (PIPEDA). These tests pin both sides.

Run:
    pytest backend/tests/test_guest_sms.py -v
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


def _patches(*, guest=_GUEST, sms_result=None, company="Prairie Motors"):
    return {
        _GNS + "get_app_settings": AsyncMock(
            return_value={
                "twilio_account_sid": "AC1",
                "twilio_auth_token": "t",
                "twilio_from_number": "+15550000000",
            }
        ),
        _GNS + "send_sms": AsyncMock(return_value=sms_result or {"success": True}),
        _GNS + "send_push_notification": AsyncMock(return_value=True),
        _GNS + "db_supabase.get_user_by_id": AsyncMock(return_value=guest),
        _GNS + "db_supabase.get_rows": AsyncMock(return_value=[{"id": "company_gb", "name": company}]),
        _GNS + "db_supabase.update_ride": AsyncMock(return_value=None),
    }


def _start(patch_dict):
    patchers, mocks = [], {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


@pytest.mark.anyio
async def test_booking_sms_carries_otp_tracking_and_company():
    from backend.services.guest_notification_service import notify_guest_booking_created

    patchers, mocks = _start(_patches())
    try:
        await notify_guest_booking_created(
            dict(_RIDE), dict(_GUEST), customer_has_app=False, tracking_url="https://track.spinr.ca/tok_abc123"
        )
    finally:
        for p in patchers:
            p.stop()

    sms = mocks[_GNS + "send_sms"]
    sms.assert_awaited_once()
    to_phone, body = sms.call_args.args[0], sms.call_args.args[1]
    assert to_phone == "+13065550123"
    assert "Prairie Motors" in body, "customer must see WHO booked for them"
    assert "4821" in body, "pickup OTP must reach the customer"
    assert "https://track.spinr.ca/tok_abc123" in body
    assert "123 Showroom Road" in body
    mocks[_GNS + "send_push_notification"].assert_not_awaited()


@pytest.mark.anyio
async def test_app_holder_gets_push_not_sms():
    from backend.services.guest_notification_service import notify_guest_booking_created

    app_user = {**_GUEST, "is_guest": False}
    patchers, mocks = _start(_patches(guest=app_user))
    try:
        await notify_guest_booking_created(dict(_RIDE), app_user, customer_has_app=True, tracking_url=None)
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "send_sms"].assert_not_awaited()
    push = mocks[_GNS + "send_push_notification"]
    push.assert_awaited_once()
    assert push.call_args.kwargs["data"]["type"] == "corporate_ride_booked"


@pytest.mark.anyio
async def test_driver_assigned_sms_has_vehicle_and_mints_token_when_absent():
    from backend.services.guest_notification_service import notify_guest_driver_assigned

    ride = {**_RIDE}
    ride.pop("shared_trip_token")  # scheduled rides mint at accept
    driver = {
        "name": "Dana Driver",
        "vehicle_color": "Blue",
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "license_plate": "ABC 123",
    }
    patchers, mocks = _start(_patches())
    try:
        await notify_guest_driver_assigned(ride, driver)
    finally:
        for p in patchers:
            p.stop()

    mocks[_GNS + "db_supabase.update_ride"].assert_awaited_once()
    body = mocks[_GNS + "send_sms"].call_args.args[1]
    assert "Dana" in body and "Dana Driver" not in body, "first name only in guest SMS"
    assert "Blue Toyota Camry" in body
    assert "ABC 123" in body
    assert "4821" in body
    assert "https://track.spinr.ca/" in body


@pytest.mark.anyio
async def test_claimed_account_stops_sms():
    """The customer installed the app mid-ride (is_guest flipped False):
    transition SMS stops — pushes cover them."""
    from backend.services.guest_notification_service import notify_guest_driver_arrived

    patchers, mocks = _start(_patches(guest={**_GUEST, "is_guest": False}))
    try:
        await notify_guest_driver_arrived(dict(_RIDE))
    finally:
        for p in patchers:
            p.stop()
    mocks[_GNS + "send_sms"].assert_not_awaited()


@pytest.mark.anyio
async def test_logs_stay_pii_clean_on_sms_failure(caplog):
    """A failed send logs the redacted phone + sanitized error only — never
    the body (addresses/OTP), the full phone, or the customer's name."""
    from backend.services.guest_notification_service import notify_guest_booking_created

    patchers, _ = _start(_patches(sms_result={"success": False, "error": "TwilioRestException code=21211 status=400"}))
    try:
        with caplog.at_level(logging.DEBUG):
            await notify_guest_booking_created(
                dict(_RIDE), dict(_GUEST), customer_has_app=False, tracking_url="https://track.spinr.ca/t"
            )
    finally:
        for p in patchers:
            p.stop()

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "3065550123" not in logged, "full phone must never reach logs"
    assert "Showroom" not in logged and "Home Street" not in logged, "addresses must never reach logs"
    assert "4821" not in logged, "the pickup OTP must never reach logs"
    assert "Pat" not in logged, "customer name must never reach logs"
    assert "ride_sms_1" in logged, "ride id (non-PII) is the correlation key"
