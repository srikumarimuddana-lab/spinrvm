"""Unit tests for utils.sos_contact_notice.send_opt_out_notice (PIA finding
R-002, subtask 3 of 4).

Covers:
- Suppressed phone -> notice skipped, returns False, send_sms never called.
- Not suppressed -> composes the notice SMS and sends it via send_sms with
  the same twilio_sid/twilio_token/twilio_from sourcing safety.py's SOS SMS
  uses; returns True on send_sms success.
- send_sms reporting success=False -> returns False (logged, not raised).
- Any exception anywhere in the flow (is_suppressed, get_app_settings,
  send_sms) is caught -> returns False, never raises. This is the load-
  bearing guarantee: a failure here must never block or fail the caller
  (add_emergency_contact)'s response.

Patching mirrors test_sos_contact_consent.py's style: this module's
dual-import fallback path (`import services.sos_contact_consent as ...` /
`import settings_loader` / `import sms_service`) means the patch targets are
the bare module-qualified names as imported into
`utils.sos_contact_notice`'s own namespace.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from utils import sos_contact_notice as notice_mod
except ImportError:  # pragma: no cover
    from backend.utils import sos_contact_notice as notice_mod  # type: ignore

pytestmark = pytest.mark.anyio

_PHONE = "+13065551234"


def _patches(**overrides):
    defaults = {
        "utils.sos_contact_notice.sos_contact_consent.is_suppressed": AsyncMock(return_value=False),
        "utils.sos_contact_notice.get_app_settings": AsyncMock(
            return_value={
                "twilio_account_sid": "sid",
                "twilio_auth_token": "tok",
                "twilio_from_number": "+15005550006",
            }
        ),
        "utils.sos_contact_notice.send_sms": AsyncMock(return_value={"success": True, "sid": "SM123"}),
    }
    defaults.update(overrides)
    return [patch(target, value) for target, value in defaults.items()]


def _start(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


async def test_sends_notice_when_not_suppressed():
    patches = _start(_patches())
    try:
        ok = await notice_mod.send_opt_out_notice(_PHONE, "Sam")
        assert ok is True
        send_sms = notice_mod.send_sms
        send_sms.assert_awaited_once()
        phone_arg, body_arg = send_sms.await_args.args
        assert phone_arg == _PHONE
        assert "Sam" in body_arg
        assert "STOP" in body_arg
        assert send_sms.await_args.kwargs == {
            "twilio_sid": "sid",
            "twilio_token": "tok",
            "twilio_from": "+15005550006",
        }
    finally:
        _stop(patches)


async def test_skips_send_when_already_suppressed():
    send_sms = AsyncMock()
    patches = _start(
        _patches(
            **{
                "utils.sos_contact_notice.sos_contact_consent.is_suppressed": AsyncMock(return_value=True),
                "utils.sos_contact_notice.send_sms": send_sms,
            }
        )
    )
    try:
        ok = await notice_mod.send_opt_out_notice(_PHONE, "Sam")
        assert ok is False
        send_sms.assert_not_awaited()
    finally:
        _stop(patches)


async def test_send_sms_reported_failure_returns_false_not_raise():
    patches = _start(
        _patches(
            **{
                "utils.sos_contact_notice.send_sms": AsyncMock(
                    return_value={"success": False, "error": "type=TwilioRestException code=21211"}
                )
            }
        )
    )
    try:
        ok = await notice_mod.send_opt_out_notice(_PHONE, "Sam")
        assert ok is False
    finally:
        _stop(patches)


async def test_is_suppressed_exception_never_raises_and_returns_false():
    patches = _start(
        _patches(
            **{
                "utils.sos_contact_notice.sos_contact_consent.is_suppressed": AsyncMock(
                    side_effect=RuntimeError("db down")
                )
            }
        )
    )
    try:
        # Must never raise -- this is a best-effort notice, the load-bearing
        # guarantee of the whole module.
        ok = await notice_mod.send_opt_out_notice(_PHONE, "Sam")
        assert ok is False
    finally:
        _stop(patches)


async def test_send_sms_exception_never_raises_and_returns_false():
    patches = _start(
        _patches(**{"utils.sos_contact_notice.send_sms": AsyncMock(side_effect=RuntimeError("twilio down"))})
    )
    try:
        ok = await notice_mod.send_opt_out_notice(_PHONE, "Sam")
        assert ok is False
    finally:
        _stop(patches)


async def test_blank_first_name_falls_back_to_generic_phrase():
    patches = _start(_patches())
    try:
        ok = await notice_mod.send_opt_out_notice(_PHONE, "")
        assert ok is True
        body_arg = notice_mod.send_sms.await_args.args[1]
        assert "A Spinr user" in body_arg
    finally:
        _stop(patches)
