"""Unit tests for utils.unsubscribe_token — signed one-click unsubscribe tokens.

Covers: round-trip sign/verify, tamper detection, bad channel rejection,
malformed token rejection. No DB or network — pure HMAC.
"""

from __future__ import annotations

import pytest

try:
    from utils.unsubscribe_token import (
        UnsubscribeTokenError,
        sign_unsubscribe_token,
        verify_unsubscribe_token,
    )
except ImportError:  # pragma: no cover
    from backend.utils.unsubscribe_token import (  # type: ignore
        UnsubscribeTokenError,
        sign_unsubscribe_token,
        verify_unsubscribe_token,
    )

_USER = "22222222-2222-2222-2222-222222222222"


def test_round_trip():
    token = sign_unsubscribe_token(user_id=_USER, channel="email")
    out = verify_unsubscribe_token(token)
    assert out == {"user_id": _USER, "channel": "email"}


def test_sms_channel_round_trip():
    token = sign_unsubscribe_token(user_id=_USER, channel="sms")
    assert verify_unsubscribe_token(token)["channel"] == "sms"


def test_rejects_unknown_channel_on_sign():
    with pytest.raises(UnsubscribeTokenError):
        sign_unsubscribe_token(user_id=_USER, channel="push")


def test_tampered_signature_rejected():
    token = sign_unsubscribe_token(user_id=_USER, channel="email")
    payload, _sig = token.split(".")
    forged = f"{payload}.AAAAAAAA"
    with pytest.raises(UnsubscribeTokenError):
        verify_unsubscribe_token(forged)


def test_tampered_payload_rejected():
    # Swap the payload for a different user but keep the old signature.
    good = sign_unsubscribe_token(user_id=_USER, channel="email")
    other = sign_unsubscribe_token(user_id="99999999-9999-9999-9999-999999999999", channel="email")
    forged = f"{other.split('.')[0]}.{good.split('.')[1]}"
    with pytest.raises(UnsubscribeTokenError):
        verify_unsubscribe_token(forged)


@pytest.mark.parametrize("bad", ["", "no-dot", "a.b.c", "...."])
def test_malformed_tokens_rejected(bad):
    with pytest.raises(UnsubscribeTokenError):
        verify_unsubscribe_token(bad)
