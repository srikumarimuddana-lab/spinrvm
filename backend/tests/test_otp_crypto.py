"""Direct unit tests for ``backend/utils/crypto.py``.

Covers the OTP-hashing helpers used by the auth flow:

  - ``hash_otp(code)`` returns a deterministic KEYED HMAC-SHA256 hex digest
    (peppered with the server secret so the 4-digit space can't be
    rainbow-tabled from a leaked otp_records table).
  - ``verify_otp_hash(stored_hash, input_otp)`` compares hashes via
    ``hmac.compare_digest`` so verification is constant-time, accepting the
    legacy unsalted digest during the deploy-transition TTL window.

Why this matters: OTPs are the second factor for rider/driver login.
A timing-side-channel in verification would let an attacker probe valid
codes one byte at a time before the 5-failures-per-hour lockout fires.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

try:
    from backend.utils.crypto import hash_otp, verify_otp_hash
except ImportError:
    from utils.crypto import hash_otp, verify_otp_hash  # type: ignore


class TestHashOtpDeterministic:
    """``hash_otp`` must be a KEYED HMAC-SHA256 of the OTP bytes.

    A plain digest over a 4-digit OTP has only 10,000 possible values — a
    precomputed table maps every hash back to its code. Keying with the server
    pepper defeats that offline recovery from a leaked otp_records table.
    """

    def test_hash_otp_is_keyed_hmac_not_plain_sha256(self):
        import hmac as _hmac

        from backend.utils.crypto import _otp_pepper

        code = "123456"
        # Peppered: keyed HMAC, NOT a plain digest an attacker could rainbow-table.
        assert hash_otp(code) == _hmac.new(_otp_pepper(), code.encode(), hashlib.sha256).hexdigest()
        assert hash_otp(code) != hashlib.sha256(code.encode()).hexdigest()

    def test_hash_otp_is_deterministic_across_calls(self):
        # Two calls with the same input must produce byte-identical output —
        # otherwise verification (which re-hashes the user input) breaks.
        assert hash_otp("987654") == hash_otp("987654")

    def test_hash_otp_differs_for_different_inputs(self):
        assert hash_otp("000000") != hash_otp("000001")

    def test_hash_otp_returns_64_hex_chars(self):
        # SHA-256 hex digest is always 64 lowercase hex characters.
        out = hash_otp("424242")
        assert len(out) == 64
        assert all(c in "0123456789abcdef" for c in out)


class TestVerifyOtpHash:
    """``verify_otp_hash`` returns True only for the matching OTP."""

    def test_verify_correct_otp_returns_true(self):
        stored = hash_otp("314159")
        assert verify_otp_hash(stored, "314159") is True

    def test_verify_incorrect_otp_returns_false(self):
        stored = hash_otp("314159")
        assert verify_otp_hash(stored, "271828") is False

    def test_verify_with_empty_stored_hash_returns_false(self):
        # An empty stored hash must never accidentally match any input.
        assert verify_otp_hash("", "123456") is False

    def test_verify_with_garbage_stored_hash_returns_false(self):
        # Non-hex / wrong-length stored values must compare unequal.
        assert verify_otp_hash("not-a-hash", "123456") is False


class TestVerifyOtpHashTimingSafe:
    """Verification MUST go through ``hmac.compare_digest``.

    Plain ``==`` short-circuits on the first byte mismatch, leaking the
    length of the matching prefix via response time. ``hmac.compare_digest``
    is constant-time over equal-length inputs.
    """

    def test_verify_calls_hmac_compare_digest(self):
        stored = hash_otp("555444")
        # Patch in the crypto module's namespace, where ``hmac`` is bound.
        with patch("backend.utils.crypto.hmac.compare_digest", return_value=True) as cd:
            result = verify_otp_hash(stored, "555444")
        assert result is True
        assert cd.called, "verify_otp_hash must use hmac.compare_digest, not =="
        # Both arguments must be the digest strings (stored hash + freshly
        # computed hash of the input), not the raw OTP.
        args, _ = cd.call_args
        assert len(args) == 2
        assert args[0] == stored
        # args[1] is the freshly-computed KEYED hash of the input, not raw sha256.
        assert args[1] == hash_otp("555444")

    def test_verify_returns_compare_digest_result(self):
        # If compare_digest returns False, verify must return False even when
        # the inputs would naively look equal — proves we trust the helper.
        stored = hash_otp("111222")
        with patch("backend.utils.crypto.hmac.compare_digest", return_value=False):
            assert verify_otp_hash(stored, "111222") is False


class TestEdgeCases:
    """Boundary inputs the auth code might plausibly pass through."""

    def test_hash_empty_string(self):
        # The helper must not raise on empty input (defense in depth — the auth
        # route rejects empty OTPs before this). Keyed HMAC of "" is stable and
        # is NOT the plain sha256 of "".
        import hmac as _hmac

        from backend.utils.crypto import _otp_pepper

        assert hash_otp("") == _hmac.new(_otp_pepper(), b"", hashlib.sha256).hexdigest()

    def test_verify_empty_otp_against_empty_hash_of_empty(self):
        # Hashing "" then verifying "" yields True — because both sides are
        # the SHA-256 of "". The auth layer is responsible for rejecting
        # empty submissions; the crypto helper is intentionally agnostic.
        stored = hash_otp("")
        assert verify_otp_hash(stored, "") is True

    def test_hash_very_long_otp(self):
        # SHA-256 has no input-length ceiling we can hit in practice — a
        # 10 KB string must still produce a 64-char digest.
        long_code = "9" * 10_000
        out = hash_otp(long_code)
        assert len(out) == 64
        assert hash_otp(long_code) == out  # still deterministic

    def test_hash_unicode_otp(self):
        # ``.encode()`` defaults to UTF-8; non-ASCII characters must be
        # handled without error so a future feature (e.g. emoji-based
        # confirmation) doesn't crash this layer.
        out = hash_otp("123éè")
        assert len(out) == 64

    def test_hash_non_string_raises_attributeerror(self):
        # ``code.encode()`` requires a string; passing an int must surface
        # as a clear AttributeError rather than silently coerce. This pins
        # the contract — callers must stringify before hashing.
        with pytest.raises(AttributeError):
            hash_otp(123456)  # type: ignore[arg-type]

    def test_verify_non_string_input_otp_raises(self):
        # Same contract on the verify side — non-string ``input_otp``
        # is a programmer error, not a "False" return.
        stored = hash_otp("000000")
        with pytest.raises(AttributeError):
            verify_otp_hash(stored, 0)  # type: ignore[arg-type]


class TestLegacyTransitionFallback:
    """During the ~5-min TTL window straddling a deploy, codes hashed with the
    old unsalted SHA-256 must still verify so in-flight OTPs don't all fail."""

    def test_verify_accepts_legacy_sha256_hash(self):
        legacy = hashlib.sha256(b"246810").hexdigest()
        assert verify_otp_hash(legacy, "246810") is True

    def test_verify_accepts_new_keyed_hash(self):
        assert verify_otp_hash(hash_otp("135790"), "135790") is True

    def test_verify_rejects_wrong_code_against_both(self):
        assert verify_otp_hash(hash_otp("111111"), "222222") is False
        assert verify_otp_hash(hashlib.sha256(b"111111").hexdigest(), "222222") is False
