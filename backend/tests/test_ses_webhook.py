"""Tests for the SES bounce/complaint webhook (routes/webhooks.py) and the
SNS signature verifier (utils/sns_verify.py).

Covers:
- is_trusted_sns_url host/scheme allowlist (SSRF guard)
- verify_sns_signature: valid roundtrip, tampered signature, untrusted cert URL
- POST /api/v1/webhooks/ses: 403 on bad signature, 400 on bad JSON,
  subscription confirmation, hard-bounce suppression, complaint suppression,
  idempotent re-delivery, transient bounce ignored
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

try:
    from utils.error_handling import DatabaseError, DuplicateRecordError
except ImportError:
    from backend.utils.error_handling import DatabaseError, DuplicateRecordError  # type: ignore[no-redef]

try:
    from utils.sns_verify import _fetch_cert, _string_to_sign, is_trusted_sns_url, verify_sns_signature
except ImportError:
    from backend.utils.sns_verify import (  # type: ignore[no-redef]
        _fetch_cert,
        _string_to_sign,
        is_trusted_sns_url,
        verify_sns_signature,
    )

_CERT_URL = "https://sns.ca-central-1.amazonaws.com/SimpleNotificationService-abc.pem"


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def _make_cert_and_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.utcnow() - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


def _signed_payload(key, payload: dict) -> dict:
    payload = dict(payload)
    payload.setdefault("SigningCertURL", _CERT_URL)
    payload.setdefault("SignatureVersion", "1")
    sig = key.sign(_string_to_sign(payload), padding.PKCS1v15(), hashes.SHA1())
    payload["Signature"] = base64.b64encode(sig).decode()
    return payload


def _bounce_message(addr: str, bounce_type: str = "Permanent") -> str:
    return json.dumps(
        {
            "notificationType": "Bounce",
            "mail": {"messageId": "ses-msg-1"},
            "bounce": {
                "bounceType": bounce_type,
                "bounceSubType": "General",
                "bouncedRecipients": [{"emailAddress": addr}],
            },
        }
    )


def _complaint_message(addr: str) -> str:
    return json.dumps(
        {
            "notificationType": "Complaint",
            "mail": {"messageId": "ses-msg-2"},
            "complaint": {
                "complaintFeedbackType": "abuse",
                "complainedRecipients": [{"emailAddress": addr}],
            },
        }
    )


# ---------------------------------------------------------------------------
# is_trusted_sns_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://sns.ca-central-1.amazonaws.com/x.pem", True),
        ("https://sns.us-east-1.amazonaws.com/x.pem", True),
        ("http://sns.ca-central-1.amazonaws.com/x.pem", False),  # not https
        ("https://evil.com/x.pem", False),  # wrong host
        ("https://sns.ca-central-1.amazonaws.com.evil.com/x.pem", False),  # suffix trick
        ("https://notsns.amazonaws.com/x.pem", False),  # not an sns. host
        ("", False),
    ],
)
def test_is_trusted_sns_url(url, expected):
    assert is_trusted_sns_url(url) is expected


# ---------------------------------------------------------------------------
# verify_sns_signature
# ---------------------------------------------------------------------------


def test_verify_signature_valid_roundtrip():
    key, pem = _make_cert_and_key()
    payload = _signed_payload(
        key,
        {
            "Type": "Notification",
            "MessageId": "m1",
            "TopicArn": "arn:aws:sns:ca-central-1:1:ses",
            "Message": _bounce_message("a@b.com"),
            "Timestamp": "2026-06-15T00:00:00.000Z",
        },
    )
    assert verify_sns_signature(payload, fetch=lambda _u: pem) is True


def test_verify_signature_rejects_tampered_message():
    key, pem = _make_cert_and_key()
    payload = _signed_payload(
        key,
        {
            "Type": "Notification",
            "MessageId": "m1",
            "TopicArn": "arn:aws:sns:ca-central-1:1:ses",
            "Message": _bounce_message("a@b.com"),
            "Timestamp": "2026-06-15T00:00:00.000Z",
        },
    )
    payload["Message"] = _bounce_message("attacker@evil.com")  # tamper after signing
    assert verify_sns_signature(payload, fetch=lambda _u: pem) is False


def test_verify_signature_rejects_untrusted_cert_url():
    key, pem = _make_cert_and_key()
    payload = _signed_payload(
        key,
        {
            "Type": "Notification",
            "MessageId": "m1",
            "TopicArn": "arn:aws:sns:ca-central-1:1:ses",
            "Message": _bounce_message("a@b.com"),
            "Timestamp": "2026-06-15T00:00:00.000Z",
            "SigningCertURL": "https://evil.com/cert.pem",
        },
    )
    # fetch must never be called for an untrusted URL
    called = {"n": 0}

    def _fetch(_u):
        called["n"] += 1
        return pem

    assert verify_sns_signature(payload, fetch=_fetch) is False
    assert called["n"] == 0


def test_verify_signature_accepts_lowercase_cert_url_key():
    key, pem = _make_cert_and_key()
    payload = {
        "Type": "Notification",
        "MessageId": "m1",
        "TopicArn": "arn:aws:sns:ca-central-1:1:ses",
        "Message": _bounce_message("a@b.com"),
        "Timestamp": "2026-06-15T00:00:00.000Z",
        "SigningCertUrl": _CERT_URL,  # non-canonical lowercase-url key
        "SignatureVersion": "1",
    }
    sig = key.sign(_string_to_sign(payload), padding.PKCS1v15(), hashes.SHA1())
    payload["Signature"] = base64.b64encode(sig).decode()
    assert verify_sns_signature(payload, fetch=lambda _u: pem) is True


def test_fetch_cert_is_cached():
    _fetch_cert.cache_clear()
    url = "https://sns.ca-central-1.amazonaws.com/unique-cache-test.pem"
    resp = MagicMock(content=b"PEMDATA")
    with patch("httpx.get", return_value=resp) as mock_get:
        a = _fetch_cert(url)
        b = _fetch_cert(url)
    assert a == b == b"PEMDATA"
    mock_get.assert_called_once()  # second call served from cache
    _fetch_cert.cache_clear()


# ---------------------------------------------------------------------------
# POST /api/v1/webhooks/ses
# ---------------------------------------------------------------------------

_SES_URL = "/api/v1/webhooks/ses"


def test_webhook_rejects_invalid_signature(test_client: TestClient):
    with patch("utils.sns_verify.verify_sns_signature", return_value=False):
        r = test_client.post(_SES_URL, json={"Type": "Notification", "Message": "{}"})
    assert r.status_code == 403


def test_webhook_rejects_bad_json(test_client: TestClient):
    r = test_client.post(_SES_URL, content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_webhook_confirms_subscription(test_client: TestClient):
    confirm_client = MagicMock()
    confirm_client.__aenter__ = AsyncMock(return_value=confirm_client)
    confirm_client.__aexit__ = AsyncMock(return_value=False)
    confirm_client.get = AsyncMock(return_value=MagicMock(status_code=200))

    payload = {
        "Type": "SubscriptionConfirmation",
        "TopicArn": "arn:aws:sns:ca-central-1:1:ses",
        "SubscribeURL": "https://sns.ca-central-1.amazonaws.com/?Action=ConfirmSubscription",
    }
    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("httpx.AsyncClient", return_value=confirm_client),
    ):
        r = test_client.post(_SES_URL, json=payload)

    assert r.status_code == 200
    assert r.json()["confirmed"] is True
    confirm_client.get.assert_awaited_once()


def test_webhook_hard_bounce_suppresses(test_client: TestClient):
    inserts: list = []
    payload = {"Type": "Notification", "Message": _bounce_message("Bad@Example.com")}

    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        r = test_client.post(_SES_URL, json=payload)

    assert r.status_code == 200
    assert r.json()["suppressed"] == 1
    assert inserts and inserts[0][0] == "email_suppressions"
    row = inserts[0][1]
    assert row["email"] == "bad@example.com"  # normalized
    assert row["reason"] == "bounce"
    assert row["message_id"] == "ses-msg-1"


def test_webhook_complaint_suppresses(test_client: TestClient):
    inserts: list = []
    payload = {"Type": "Notification", "Message": _complaint_message("c@example.com")}

    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        r = test_client.post(_SES_URL, json=payload)

    assert r.status_code == 200
    assert r.json()["suppressed"] == 1
    assert inserts[0][1]["reason"] == "complaint"


def test_webhook_idempotent_when_already_suppressed(test_client: TestClient):
    inserts: list = []
    payload = {"Type": "Notification", "Message": _bounce_message("dup@example.com")}

    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(return_value={"email": "dup@example.com"})),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        r = test_client.post(_SES_URL, json=payload)

    assert r.status_code == 200
    # Already transactionally suppressed → no duplicate email_suppressions insert.
    assert [i for i in inserts if i[0] == "email_suppressions"] == []


def test_webhook_transient_bounce_not_suppressed(test_client: TestClient):
    """A transient bounce never blocks TRANSACTIONAL mail (it may recover) but
    DOES block MARKETING (product rule: one bounce = no marketing ever)."""
    inserts: list = []
    payload = {"Type": "Notification", "Message": _bounce_message("t@example.com", bounce_type="Transient")}

    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=lambda t, d: inserts.append((t, d)))),
    ):
        r = test_client.post(_SES_URL, json=payload)

    assert r.status_code == 200
    # Transactional: untouched.
    assert r.json()["suppressed"] == 0
    assert [i for i in inserts if i[0] == "email_suppressions"] == []
    # Marketing: suppressed on this single transient bounce.
    assert r.json()["marketing_suppressed"] == 1
    mkt = [i for i in inserts if i[0] == "marketing_suppressions"]
    assert mkt and mkt[0][1]["target"] == "t@example.com" and mkt[0][1]["reason"] == "bounce"


def test_webhook_rejects_unexpected_topic(test_client: TestClient):
    payload = {
        "Type": "Notification",
        "TopicArn": "arn:aws:sns:ca-central-1:1:ATTACKER",
        "Message": _bounce_message("x@example.com"),
    }
    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value={"aws_ses_sns_topic_arn": "arn:expected"})),
    ):
        r = test_client.post(_SES_URL, json=payload)
    assert r.status_code == 403


def test_webhook_accepts_matching_topic(test_client: TestClient):
    payload = {
        "Type": "Notification",
        "TopicArn": "arn:expected",
        "Message": _bounce_message("ok@example.com"),
    }
    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value={"aws_ses_sns_topic_arn": "arn:expected"})),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
    ):
        r = test_client.post(_SES_URL, json=payload)
    assert r.status_code == 200
    assert r.json()["suppressed"] == 1


def test_webhook_duplicate_insert_race_is_ok(test_client: TestClient):
    payload = {"Type": "Notification", "Message": _bounce_message("race@example.com")}
    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(side_effect=DuplicateRecordError())),
    ):
        r = test_client.post(_SES_URL, json=payload)
    # Lost the insert race to a concurrent redelivery → still a success.
    assert r.status_code == 200
    assert r.json()["suppressed"] == 1


def test_webhook_db_error_returns_503(test_client: TestClient):
    payload = {"Type": "Notification", "Message": _bounce_message("err@example.com")}
    with (
        patch("utils.sns_verify.verify_sns_signature", return_value=True),
        patch("db_supabase.find_one", AsyncMock(side_effect=DatabaseError(details={"original": "boom"}))),
    ):
        r = test_client.post(_SES_URL, json=payload)
    # DB unavailable → 503 so SNS retries rather than dropping the bounce.
    assert r.status_code == 503
