"""Coverage gap on backend/core/config.py's Settings fail-fast validation.

Root CLAUDE.md: "backend/core/config.py — pydantic-settings `Settings`;
fails fast in production on weak secrets." test_p1_auth_hardening.py already
covers the JWT_SECRET-length, Firebase-app-id, and SUPABASE_REGION branches
of `_guard_production_secrets` — this file is scoped to the REMAINING gap:
the placeholder-value checks (JWT_SECRET == the doc-example string,
ADMIN_PASSWORD in the known-weak set), the missing-SUPABASE_URL /
SUPABASE_SERVICE_ROLE_KEY guards, `_hash_admin_password`, the
`review_login_map` / `_validate_review_accounts` App-Store-reviewer-login
parser, and the small `SECRET_KEY` / `debug` properties. Deliberately a
separate file rather than extending test_p1_auth_hardening.py, mirroring how
test_subscriptions_coverage.py was kept separate from
test_spinr_pass_subscription.py earlier in this sprint.

Test-only: no application code in core/config.py is modified by this file.
"""

from __future__ import annotations

import logging
import os

import bcrypt
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Same reload-avoidance note as test_p1_auth_hardening.py: import the Settings
# CLASS and construct a fresh instance per test. Do NOT importlib.reload()
# core.config — that replaces the module-level `settings` singleton under
# sys.modules["backend.core.config"] while leaving the bare "core.config"
# entry (used by most of the app under the dual-import pattern) pointing at
# the OLD singleton, which has broken JWT signing for later tests before.
_PROD_BASE = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    "JWT_SECRET": "a" * 32,
    "ADMIN_PASSWORD": "StrongPass123!ExtraLong",  # >=20 chars — see TestAdminPasswordLengthGuard
    "FIREBASE_DRIVER_APP_ID": "driver-app-id",
    "FIREBASE_RIDER_APP_ID": "rider-app-id",
    "SUPABASE_REGION": "ca-central-1",
    "ENV": "production",
}

_DEV_BASE = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    "JWT_SECRET": "dev-secret",
    "ADMIN_PASSWORD": "anything",
    "ENV": "development",
}


def _make_settings(base: dict, **overrides):
    """Instantiate a fresh Settings() with the given env vars, then restore
    the environment. `overrides` values of None DELETE the key from the base
    dict (used to simulate a genuinely unset var) rather than setting it."""
    merged = dict(base)
    to_unset = []
    for k, v in overrides.items():
        if v is None:
            merged.pop(k, None)
            to_unset.append(k)
        else:
            merged[k] = v

    # Clear any pre-existing values for keys we're about to unset so a
    # leaked env var from the outer shell/.env doesn't mask the "missing" case.
    prior = {}
    for k in set(merged) | set(to_unset):
        prior[k] = os.environ.get(k)
    try:
        for k in to_unset:
            os.environ.pop(k, None)
        for k, v in merged.items():
            os.environ[k] = v

        from backend.core.config import Settings

        return Settings()
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# _guard_production_secrets — placeholder-value + missing-Supabase-creds gap
# ---------------------------------------------------------------------------


class TestGuardProductionSecretsPlaceholders:
    def test_jwt_secret_doc_placeholder_raises(self):
        """The exact placeholder string from onboarding docs must be rejected
        even though it happens to be < 32 chars would already fail length —
        use a padded variant to isolate the placeholder-value check itself."""
        with pytest.raises(Exception, match="known-weak placeholder"):
            _make_settings(_PROD_BASE, JWT_SECRET="your-strong-secret-key")

    @pytest.mark.parametrize("weak_pw", ["admin123", "password", "changeme"])
    def test_weak_admin_password_raises(self, weak_pw):
        with pytest.raises(Exception, match="ADMIN_PASSWORD"):
            _make_settings(_PROD_BASE, ADMIN_PASSWORD=weak_pw)

    def test_strong_admin_password_passes(self):
        strong_pw = "Correct-Horse-42!-Battery"  # >=20 chars — see TestAdminPasswordLengthGuard
        _make_settings(_PROD_BASE, ADMIN_PASSWORD=strong_pw)  # must not raise

    def test_missing_supabase_url_raises(self):
        with pytest.raises(Exception, match="SUPABASE_URL"):
            _make_settings(_PROD_BASE, SUPABASE_URL=None)

    def test_missing_supabase_service_role_key_raises(self):
        with pytest.raises(Exception, match="SUPABASE_SERVICE_ROLE_KEY"):
            _make_settings(_PROD_BASE, SUPABASE_SERVICE_ROLE_KEY=None)

    def test_all_valid_production_settings_pass(self):
        """Sanity: the full production baseline used by every test above must
        itself pass with no overrides (guards against a fixture typo making
        every 'raises' test pass for the wrong reason)."""
        s = _make_settings(_PROD_BASE)
        assert s.ENV.lower() == "production"


class TestAdminPasswordLengthGuard:
    """`_guard_production_secrets` also length-checks ADMIN_PASSWORD
    (>=20 chars), matching JWT_SECRET's existing minimum-length check and
    core/middleware.py's separate guard on the same field."""

    def test_short_admin_password_rejected(self):
        with pytest.raises(Exception, match="ADMIN_PASSWORD must be at least 20 characters"):
            _make_settings(_PROD_BASE, ADMIN_PASSWORD="x")

    def test_admin_password_exactly_19_chars_rejected(self):
        with pytest.raises(Exception, match="ADMIN_PASSWORD must be at least 20 characters"):
            _make_settings(_PROD_BASE, ADMIN_PASSWORD="a" * 19)

    def test_admin_password_exactly_20_chars_accepted(self):
        s = _make_settings(_PROD_BASE, ADMIN_PASSWORD="a" * 20)
        assert s.ADMIN_PASSWORD == "a" * 20


# ---------------------------------------------------------------------------
# _hash_admin_password
# ---------------------------------------------------------------------------


class TestHashAdminPassword:
    def test_admin_password_hashed_at_construction(self):
        plaintext_pw = "plaintext-pw-123"
        s = _make_settings(_DEV_BASE, ADMIN_PASSWORD=plaintext_pw)
        assert s.admin_password_hash.startswith("$2b$")
        assert bcrypt.checkpw(plaintext_pw.encode(), s.admin_password_hash.encode())

    def test_wrong_password_does_not_verify(self):
        plaintext_pw = "plaintext-pw-123"
        s = _make_settings(_DEV_BASE, ADMIN_PASSWORD=plaintext_pw)
        assert not bcrypt.checkpw(b"totally-different", s.admin_password_hash.encode())

    def test_empty_admin_password_leaves_hash_empty(self):
        """Guarded by `if self.ADMIN_PASSWORD and not self.admin_password_hash`
        — an empty ADMIN_PASSWORD must not attempt to hash an empty string."""
        s = _make_settings(_DEV_BASE, ADMIN_PASSWORD="")
        assert s.admin_password_hash == ""


# ---------------------------------------------------------------------------
# review_login_map — App Store / Play reviewer OTP allowlist parser
# ---------------------------------------------------------------------------


class TestReviewLoginMap:
    def test_unset_returns_empty_dict(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS=None)
        assert s.review_login_map() == {}

    def test_blank_string_returns_empty_dict(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="   ")
        assert s.review_login_map() == {}

    def test_single_valid_pair_parsed(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="+13065550100:4821")
        assert s.review_login_map() == {"+13065550100": "4821"}

    def test_multiple_valid_pairs_parsed(self):
        s = _make_settings(
            _DEV_BASE,
            REVIEW_LOGIN_ACCOUNTS="+13065550100:4821,+13065550101:1234",
        )
        assert s.review_login_map() == {"+13065550100": "4821", "+13065550101": "1234"}

    def test_entry_missing_colon_skipped(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="+13065550100-4821")
        assert s.review_login_map() == {}

    def test_otp_wrong_length_skipped(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="+13065550100:482")
        assert s.review_login_map() == {}

    def test_otp_non_numeric_skipped(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="+13065550100:abcd")
        assert s.review_login_map() == {}

    def test_valid_entry_survives_alongside_malformed_one(self):
        s = _make_settings(
            _DEV_BASE,
            REVIEW_LOGIN_ACCOUNTS="+13065550100:4821,+13065550102:bad",
        )
        assert s.review_login_map() == {"+13065550100": "4821"}

    def test_whitespace_around_pair_is_trimmed(self):
        s = _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS=" +13065550100 : 4821 ")
        assert s.review_login_map() == {"+13065550100": "4821"}


# ---------------------------------------------------------------------------
# _validate_review_accounts — startup-time surfacing, never raises
# ---------------------------------------------------------------------------


class TestValidateReviewAccounts:
    def test_unset_is_silent(self, caplog):
        with caplog.at_level(logging.INFO, logger="core.config"):
            _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS=None)
        assert "REVIEW_LOGIN_ACCOUNTS" not in caplog.text

    def test_malformed_no_colon_logs_error_but_does_not_raise(self, caplog):
        with caplog.at_level(logging.ERROR, logger="core.config"):
            _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="not-a-valid-entry")
        assert "malformed entry" in caplog.text

    def test_bad_otp_length_logs_error_with_last4_only(self, caplog):
        """PIPEDA-adjacent: only the last 4 digits of the phone may appear in
        the log line, never the full number or the (invalid) OTP itself."""
        with caplog.at_level(logging.ERROR, logger="core.config"):
            _make_settings(_DEV_BASE, REVIEW_LOGIN_ACCOUNTS="+13065550199:12")
        assert "...0199" in caplog.text
        assert "+13065550199" not in caplog.text
        assert "12" not in caplog.text.split("...0199")[0]

    def test_valid_entries_log_info_count(self, caplog):
        with caplog.at_level(logging.INFO, logger="core.config"):
            _make_settings(
                _DEV_BASE,
                REVIEW_LOGIN_ACCOUNTS="+13065550100:4821,+13065550101:1234",
            )
        assert "2 reviewer login account(s) active" in caplog.text

    def test_never_raises_even_with_multiple_malformed_entries(self):
        # Must not raise — a typo in this optional reviewer secret must not
        # take down the API (see docstring in core/config.py).
        _make_settings(
            _DEV_BASE,
            REVIEW_LOGIN_ACCOUNTS="garbage,,+1306:toolong,+1306555:12",
        )


# ---------------------------------------------------------------------------
# Small properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_secret_key_mirrors_jwt_secret(self):
        s = _make_settings(_DEV_BASE, JWT_SECRET="mirror-me-please")
        assert s.SECRET_KEY == "mirror-me-please"

    def test_debug_true_in_development(self):
        s = _make_settings(_DEV_BASE, ENV="development")
        assert s.debug is True

    def test_debug_false_in_production(self):
        s = _make_settings(_PROD_BASE)
        assert s.debug is False

    def test_debug_false_in_staging(self):
        s = _make_settings(_DEV_BASE, ENV="staging")
        assert s.debug is False


# ---------------------------------------------------------------------------
# _is_valid_review_otp (module-level helper, exercised directly)
# ---------------------------------------------------------------------------


class TestIsValidReviewOtp:
    def test_valid_four_digit_code(self):
        from backend.core.config import _is_valid_review_otp

        assert _is_valid_review_otp("4821") is True

    def test_wrong_length_rejected(self):
        from backend.core.config import _is_valid_review_otp

        assert _is_valid_review_otp("482") is False
        assert _is_valid_review_otp("48210") is False

    def test_non_digit_rejected(self):
        from backend.core.config import _is_valid_review_otp

        assert _is_valid_review_otp("482a") is False

    def test_non_ascii_digit_rejected(self):
        """`.isdigit()` alone accepts unicode digits (e.g. superscripts,
        full-width digits); the `.isascii()` guard in the implementation
        exists specifically to reject those so a code that looks numeric but
        isn't ASCII can't slip past the ^\\d{4}$ schema regex on verify-otp."""
        from backend.core.config import _is_valid_review_otp

        assert _is_valid_review_otp("¹²³⁴") is False  # superscript 1234
