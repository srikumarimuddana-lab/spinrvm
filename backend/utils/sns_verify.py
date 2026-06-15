"""Amazon SNS message signature verification.

SNS delivers to our HTTPS webhook unauthenticated, so before acting on a
message (confirming a subscription, or adding addresses to the email
suppression list) we must prove it genuinely came from AWS. We verify the
RSA signature against the X.509 certificate AWS publishes, *after* confirming
the certificate URL is an `sns.<region>.amazonaws.com` HTTPS host (SSRF guard
— never fetch an attacker-supplied URL).

Without this, anyone who finds the webhook URL could suppress arbitrary
addresses (a denial-of-email attack) or forge confirmations.
"""

import base64
import functools
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=16)
def _fetch_cert(cert_url: str) -> bytes:
    """Fetch (and cache) the SNS signing certificate PEM.

    AWS reuses the same per-region SigningCertURL for weeks, so caching turns
    a burst of bounce notifications into a single outbound fetch instead of one
    per message. Caller must have already host-validated ``cert_url``.
    """
    import httpx

    return httpx.get(cert_url, timeout=5.0).content


# Fields included in the SNS signature, in this order, keyed by message Type.
# (See "Verifying the signatures of Amazon SNS messages" in the AWS docs.)
_SIGNED_KEYS = {
    "Notification": ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
    "SubscriptionConfirmation": [
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ],
    "UnsubscribeConfirmation": [
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ],
}


def is_trusted_sns_url(url: str) -> bool:
    """True only for an https URL on an `sns.*.amazonaws.com` host.

    Used to gate both the SigningCertURL fetch and the SubscribeURL confirm,
    so we never issue a request to an attacker-controlled host.
    """
    try:
        p = urlparse(url or "")
    except Exception:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and host.startswith("sns.") and host.endswith(".amazonaws.com")


def _string_to_sign(payload: dict) -> bytes:
    keys = _SIGNED_KEYS.get(payload.get("Type", ""))
    if not keys:
        raise ValueError(f"Unsupported SNS Type: {payload.get('Type')!r}")
    parts: list[str] = []
    for k in keys:
        if k == "Subject" and "Subject" not in payload:
            continue  # Subject is optional — omit the pair entirely when absent
        if k not in payload:
            raise ValueError(f"SNS message missing signed field {k!r}")
        parts.append(k)
        parts.append(str(payload[k]))
    return ("\n".join(parts) + "\n").encode("utf-8")


def verify_sns_signature(payload: dict, *, fetch=None) -> bool:
    """Return True iff ``payload`` carries a valid AWS SNS signature.

    Args:
        payload: the parsed SNS JSON body.
        fetch: optional ``callable(url) -> pem_bytes`` for the signing
            certificate, injected in tests. Defaults to an httpx GET against
            the (host-validated) SigningCertURL.
    """
    try:
        cert_url = payload.get("SigningCertURL") or ""
        if not cert_url and payload.get("SigningCertUrl"):
            # AWS's canonical field is SigningCertURL; accept the lowercase-url
            # variant defensively but surface it so a malformed payload is visible.
            logger.warning("[SNS] payload used non-canonical SigningCertUrl key")
            cert_url = payload.get("SigningCertUrl") or ""
        if not is_trusted_sns_url(cert_url):
            logger.error("[SNS] untrusted SigningCertURL host — rejecting message")
            return False

        signature = base64.b64decode(payload["Signature"])
        message = _string_to_sign(payload)
        version = str(payload.get("SignatureVersion", "1"))

        pem = _fetch_cert(cert_url) if fetch is None else fetch(cert_url)

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.x509 import load_pem_x509_certificate

        cert = load_pem_x509_certificate(pem)
        # SignatureVersion 1 uses SHA1withRSA, version 2 uses SHA256withRSA.
        # SHA1 here is dictated by the SNS protocol, not a security choice.
        algo = hashes.SHA256() if version == "2" else hashes.SHA1()  # noqa: S303
        cert.public_key().verify(signature, message, padding.PKCS1v15(), algo)
        return True
    except Exception:
        logger.error("[SNS] signature verification failed", exc_info=True)
        return False
