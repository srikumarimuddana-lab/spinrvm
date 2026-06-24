"""Tests for the inbound Twilio SMS webhook (STOP/START marketing consent).

Signature verification is skipped in these tests by returning empty app
settings (no twilio_auth_token → dev bypass), matching the SES topic-blank
pattern. Covers: STOP opts out + suppresses, START opts back in, an unrelated
message is a no-op, and every response is empty TwiML (200).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

_URL = "/api/v1/webhooks/twilio-inbound"
_USER = {"id": "55555555-5555-5555-5555-555555555555"}


def _patches():
    return (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value={})),
        patch("db_supabase.find_one", AsyncMock(return_value=_USER)),
        patch("services.marketing_consent.set_consent", AsyncMock(return_value=None)),
        patch("services.marketing_consent.add_marketing_suppression", AsyncMock(return_value=True)),
    )


def test_stop_opts_out_and_suppresses(test_client: TestClient):
    p_settings, p_find, p_consent, p_suppress = _patches()
    with p_settings, p_find, p_consent as consent, p_suppress as suppress:
        r = test_client.post(_URL, data={"Body": "STOP", "From": "+13065551234"})

    assert r.status_code == 200
    assert "<Response>" in r.text
    # Consent flipped off for SMS, sourced as sms_stop.
    consent.assert_awaited_once()
    args, kwargs = consent.call_args
    assert args[1] == "sms" and args[2] is False
    assert kwargs["source"] == "sms_stop"
    # And the number is added to the SMS marketing suppression list.
    suppress.assert_awaited_once()
    assert suppress.call_args[0][0] == "sms"
    assert suppress.call_args[1]["reason"] == "sms_stop"


def test_start_opts_back_in(test_client: TestClient):
    p_settings, p_find, p_consent, p_suppress = _patches()
    with p_settings, p_find, p_consent as consent, p_suppress as suppress:
        r = test_client.post(_URL, data={"Body": "START", "From": "+13065551234"})

    assert r.status_code == 200
    consent.assert_awaited_once()
    assert consent.call_args[0][2] is True  # opted_in
    suppress.assert_not_awaited()  # opt-in never suppresses


def test_unrelated_message_is_noop(test_client: TestClient):
    p_settings, p_find, p_consent, p_suppress = _patches()
    with p_settings, p_find, p_consent as consent, p_suppress as suppress:
        r = test_client.post(_URL, data={"Body": "when is my ride?", "From": "+13065551234"})

    assert r.status_code == 200
    consent.assert_not_awaited()
    suppress.assert_not_awaited()
