"""
Tests for backend/utils/password.py — bcrypt hashing + legacy SHA256 migration.

All tests are pure-function tests with zero mocking. The password utility
doesn't touch the database or any external service.
"""

import hashlib

import bcrypt
import pytest

from backend.utils.password import (
    _constant_time_equal,
    hash_password,
    verify_password,
)


class TestHashPassword:
    """Tests for hash_password()."""

    def test_returns_bcrypt_string(self):
        result = hash_password("mypassword123")
        assert result.startswith("$2")
        assert len(result) == 60  # bcrypt hashes are always 60 chars

    def test_different_inputs_different_hashes(self):
        h1 = hash_password("password_one")
        h2 = hash_password("password_two")
        assert h1 != h2

    def test_same_input_different_hashes_due_to_salt(self):
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # bcrypt uses random salt each time

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            hash_password("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            hash_password(None)  # type: ignore

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            hash_password(123)  # type: ignore


class TestVerifyPassword:
    """Tests for verify_password()."""

    # ── Fresh bcrypt hashes ──────────────────────────────────────

    def test_bcrypt_correct_password(self):
        hashed = hash_password("correct_horse_battery_staple")
        ok, needs_upgrade = verify_password("correct_horse_battery_staple", hashed)
        assert ok is True
        assert needs_upgrade is False

    def test_bcrypt_wrong_password(self):
        hashed = hash_password("correct_password")
        ok, needs_upgrade = verify_password("wrong_password", hashed)
        assert ok is False
        assert needs_upgrade is False

    # ── Legacy SHA256 hashes ─────────────────────────────────────

    def test_legacy_sha256_correct_password(self):
        legacy_hash = hashlib.sha256(b"legacy_pass_123").hexdigest()
        ok, needs_upgrade = verify_password("legacy_pass_123", legacy_hash)
        assert ok is True
        assert needs_upgrade is True  # always request upgrade from SHA256

    def test_legacy_sha256_wrong_password(self):
        legacy_hash = hashlib.sha256(b"real_password").hexdigest()
        ok, needs_upgrade = verify_password("wrong_password", legacy_hash)
        assert ok is False
        assert needs_upgrade is False

    # ── Edge cases ───────────────────────────────────────────────

    def test_empty_password(self):
        ok, needs_upgrade = verify_password("", hash_password("notempty"))
        assert ok is False
        assert needs_upgrade is False

    def test_empty_stored_hash(self):
        ok, needs_upgrade = verify_password("something", "")
        assert ok is False
        assert needs_upgrade is False

    def test_both_empty(self):
        ok, needs_upgrade = verify_password("", "")
        assert ok is False
        assert needs_upgrade is False

    def test_unknown_hash_format(self):
        ok, needs_upgrade = verify_password("anything", "not-a-valid-hash-format")
        assert ok is False
        assert needs_upgrade is False

    def test_outdated_bcrypt_cost_factor_triggers_upgrade(self):
        """A bcrypt hash with cost factor < 12 should request upgrade."""
        # Generate with cost=10 (below the module's target of 12)
        low_cost_hash = bcrypt.hashpw(
            b"test_password",
            bcrypt.gensalt(rounds=10),
        ).decode("utf-8")
        ok, needs_upgrade = verify_password("test_password", low_cost_hash)
        assert ok is True
        assert needs_upgrade is True  # cost 10 < target 12


class TestConstantTimeEqual:
    """Tests for _constant_time_equal()."""

    def test_equal_strings(self):
        assert _constant_time_equal("abc123", "abc123") is True

    def test_unequal_strings(self):
        assert _constant_time_equal("abc123", "abc456") is False

    def test_different_lengths(self):
        assert _constant_time_equal("short", "much_longer_string") is False

    def test_empty_strings(self):
        assert _constant_time_equal("", "") is True

    def test_hex_digests(self):
        h1 = hashlib.sha256(b"same").hexdigest()
        h2 = hashlib.sha256(b"same").hexdigest()
        assert _constant_time_equal(h1, h2) is True

    def test_hex_digests_different(self):
        h1 = hashlib.sha256(b"one").hexdigest()
        h2 = hashlib.sha256(b"two").hexdigest()
        assert _constant_time_equal(h1, h2) is False
