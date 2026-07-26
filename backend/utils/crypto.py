"""
Cryptographic helpers for Spinr backend.
Pure functions (no network I/O); the only dependency is the server pepper,
read lazily from settings.
"""

import hashlib
import hmac


def _otp_pepper() -> bytes:
    """Server-side pepper for OTP hashing.

    A plain SHA-256 over a 4-digit OTP has only 10,000 possible digests — a
    precomputed table maps every hash back to its code instantly, so a leaked
    otp_records table / DB backup / read-replica yields live account-takeover
    codes with zero work. Keying the hash with a server secret the attacker
    does not have defeats that offline recovery entirely.

    Prefers a dedicated OTP_PEPPER when set; otherwise derives from JWT_SECRET,
    which production already guarantees is present and strong (≥32 chars,
    enforced at startup), so no new required config is introduced.
    """
    try:
        from core.config import settings
    except ImportError:  # pragma: no cover — package-relative fallback
        from ..core.config import settings  # type: ignore
    pepper = (getattr(settings, "OTP_PEPPER", "") or getattr(settings, "JWT_SECRET", "") or "").encode()
    return pepper


def hash_otp(code: str) -> str:
    """Return the keyed HMAC-SHA256 of an OTP code (hex).

    OTPs are stored hashed so read access to the otp_records table does not
    expose valid codes; the pepper additionally defeats offline rainbow-table
    recovery over the tiny 4-digit space. bcrypt/argon2 are unnecessary here —
    the keyed HMAC already removes the offline attack and adds no latency to the
    verify-OTP hot path.
    """
    return hmac.new(_otp_pepper(), code.encode(), hashlib.sha256).hexdigest()


def _legacy_sha256(code: str) -> str:
    """Pre-pepper unsalted digest. Retained only so codes issued in the ~5-min
    TTL window straddling a deploy still verify; new codes use hash_otp()."""
    return hashlib.sha256(code.encode()).hexdigest()


def verify_otp_hash(stored_hash: str, input_otp: str) -> bool:
    """Constant-time OTP verification. Accepts the new keyed hash and, as a
    transition fallback, the legacy unsalted SHA-256 for in-flight codes."""
    if hmac.compare_digest(stored_hash, hash_otp(input_otp)):
        return True
    return hmac.compare_digest(stored_hash, _legacy_sha256(input_otp))
