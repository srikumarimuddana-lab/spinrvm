"""
Cryptographic helpers for Spinr backend.
All functions here are pure (no I/O) and use the standard library only.
"""
import hashlib


def hash_otp(code: str) -> str:
    """
    Return the SHA-256 hex digest of an OTP code.

    OTPs are stored hashed so that read access to the otp_records table
    does not expose valid codes.  Verification is done by hashing the
    submitted code and comparing digests.

    SHA-256 is appropriate here because:
    - OTPs are short-lived (5 min) and high-entropy relative to their length
    - The search space is bounded by the lockout (5 failures → 24 h block)
    - bcrypt / argon2 would add unnecessary latency to the verify-OTP path
    """
    return hashlib.sha256(code.encode()).hexdigest()
