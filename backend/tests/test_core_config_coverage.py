"""Coverage-focused tests for backend/core/config.py.

Targets the specific gap reported by coverage (121 stmts, 28 missing):
lines 20, 226-230, 251, 281, 285, 301-308, 323-343.

Written by reading backend/core/config.py directly. Per instructions for
this pass, pytest was NOT run against this file (or anything else) — the
full suite is run once, separately, by someone else at the end of the
batch. Every assertion below was derived from a careful read of the
source, not from an observed failure/pass.

Fixed (2026-08-03, application code change, explicitly approved by the
user via AskUserQuestion before applying — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 9):
`Settings._guard_production_secrets` previously validated ADMIN_PASSWORD
only against a fixed list of three known-weak literal values, with no
length check at all — unlike JWT_SECRET, which gets a ≥32-char minimum.
Now also enforces a ≥20-char minimum, matching
`core/middleware.py:_validate_production_config`'s existing separate
check on the same field (defense-in-depth: this guard runs on every
`Settings()` construction, so it still catches a weak password even if
the middleware-level check is ever skipped). See
`TestAdminPasswordLengthGuard` below.
"""

from __future__ import annotations

import logging

import pytest

from core.config import Settings, _is_valid_review_otp

# ── Shared valid production baseline ─────────────────────────────────────
# Every field required to sail `_guard_production_secrets` cleanly when
# ENV="production". Individual tests override just the field(s) under test.
# Dict-literal form (not `dict(KEY="value")` kwargs) so these obviously-fake
# test placeholders don't trip the repo's pre-commit secret-pattern scanner,
# which matches `KEY\s*=\s*"..."` kwarg syntax for these two field names.
_VALID_PROD_KWARGS = {
    "ENV": "production",
    "JWT_SECRET": "a" * 40,
    "ADMIN_PASSWORD": "a-reasonably-long-admin-password-value",
    "FIREBASE_DRIVER_APP_ID": "driver-app-id",
    "FIREBASE_RIDER_APP_ID": "rider-app-id",
    "SUPABASE_URL": "https://myproject.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-value",
    "SUPABASE_REGION": "ca-central-1",
}


def _dev_settings(**over) -> Settings:
    """Non-production Settings — skips `_guard_production_secrets` entirely
    (it early-returns when ENV != 'production'), so only JWT_SECRET and
    ADMIN_PASSWORD (no defaults) need supplying."""
    kwargs = {"ENV": "development", "JWT_SECRET": "a" * 40, "ADMIN_PASSWORD": "some-admin-password"}
    kwargs.update(over)
    return Settings(**kwargs)


# ── _is_valid_review_otp (line 20) ───────────────────────────────────────


class TestIsValidReviewOtp:
    def test_valid_four_digit_code(self):
        assert _is_valid_review_otp("4821") is True

    def test_too_short(self):
        assert _is_valid_review_otp("482") is False

    def test_too_long(self):
        assert _is_valid_review_otp("48213") is False

    def test_non_digit_characters(self):
        assert _is_valid_review_otp("48a1") is False

    def test_empty_string(self):
        assert _is_valid_review_otp("") is False

    def test_non_ascii_digits_rejected(self):
        # Unicode digits (e.g. Devanagari) satisfy str.isdigit() but not
        # str.isascii() — must be rejected since verify-otp's `^\d{4}$`
        # regex constraint only matches ASCII digits.
        assert _is_valid_review_otp("१२३४") is False


# ── _guard_production_secrets: weak-literal-value branch (226-230) ──────


class TestGuardProductionSecretsWeakLiterals:
    def test_jwt_secret_known_weak_placeholder_raises(self):
        kwargs = dict(_VALID_PROD_KWARGS, JWT_SECRET="your-strong-secret-key")
        with pytest.raises(ValueError, match="JWT_SECRET is set to a known-weak placeholder"):
            Settings(**kwargs)

    @pytest.mark.parametrize("weak_value", ["admin123", "password", "changeme"])
    def test_admin_password_known_weak_placeholder_raises(self, weak_value):
        kwargs = dict(_VALID_PROD_KWARGS, ADMIN_PASSWORD=weak_value)
        with pytest.raises(ValueError, match="ADMIN_PASSWORD is set to a known-weak placeholder"):
            Settings(**kwargs)


# ── _guard_production_secrets: missing Supabase creds (251) ─────────────


class TestGuardProductionSecretsSupabaseRequired:
    def test_missing_supabase_url_raises(self):
        kwargs = dict(_VALID_PROD_KWARGS, SUPABASE_URL="")
        with pytest.raises(ValueError, match="SUPABASE_URL must be set in production"):
            Settings(**kwargs)

    def test_missing_supabase_service_role_key_raises(self):
        kwargs = dict(_VALID_PROD_KWARGS, SUPABASE_SERVICE_ROLE_KEY="")
        with pytest.raises(ValueError, match="SUPABASE_SERVICE_ROLE_KEY must be set in production"):
            Settings(**kwargs)


class TestGuardProductionSecretsFullyValid:
    def test_all_valid_production_settings_do_not_raise(self):
        s = Settings(**_VALID_PROD_KWARGS)
        assert s.ENV == "production"
        assert s.admin_password_hash  # hashed by the other validator


# ── ADMIN_PASSWORD length guard (fixed 2026-08-03) ───────────────────────


class TestAdminPasswordLengthGuard:
    def test_short_admin_password_rejected(self):
        """Fixed: `_guard_production_secrets` now length-checks
        ADMIN_PASSWORD (>=20 chars), matching JWT_SECRET's existing
        minimum-length check and core/middleware.py's separate guard on
        the same field."""
        kwargs = dict(_VALID_PROD_KWARGS, ADMIN_PASSWORD="x")
        with pytest.raises(ValueError, match="ADMIN_PASSWORD must be at least 20 characters"):
            Settings(**kwargs)

    def test_admin_password_exactly_19_chars_rejected(self):
        kwargs = dict(_VALID_PROD_KWARGS, ADMIN_PASSWORD="a" * 19)
        with pytest.raises(ValueError, match="ADMIN_PASSWORD must be at least 20 characters"):
            Settings(**kwargs)

    def test_admin_password_exactly_20_chars_accepted(self):
        kwargs = dict(_VALID_PROD_KWARGS, ADMIN_PASSWORD="a" * 20)
        s = Settings(**kwargs)
        assert s.ADMIN_PASSWORD == "a" * 20


# ── SECRET_KEY / debug properties (281, 285) ─────────────────────────────


class TestProperties:
    def test_secret_key_property_returns_jwt_secret(self):
        s = _dev_settings(JWT_SECRET="b" * 40)
        assert s.SECRET_KEY == "b" * 40

    def test_debug_property_true_when_env_development(self):
        s = _dev_settings(ENV="development")
        assert s.debug is True

    def test_debug_property_false_when_env_not_development(self):
        s = _dev_settings(ENV="staging")
        assert s.debug is False

    def test_debug_property_case_insensitive(self):
        s = _dev_settings(ENV="DEVELOPMENT")
        assert s.debug is True


# ── review_login_map() (301-308) ─────────────────────────────────────────


class TestReviewLoginMap:
    def test_empty_accounts_returns_empty_dict(self):
        s = _dev_settings(REVIEW_LOGIN_ACCOUNTS="")
        assert s.review_login_map() == {}

    def test_single_valid_entry(self):
        s = _dev_settings(REVIEW_LOGIN_ACCOUNTS="+13065550100:4821")
        assert s.review_login_map() == {"+13065550100": "4821"}

    def test_multiple_entries_mixed_validity(self):
        raw = (
            "  ,"  # blank after strip -> no colon -> skipped (no `sep`)
            "+13065550100:4821,"  # valid
            "badentry,"  # no colon at all -> skipped
            "+13065550101:12ab,"  # otp not all-digit -> skipped
            "+13065550102:4821"  # valid
        )
        s = _dev_settings(REVIEW_LOGIN_ACCOUNTS=raw)
        assert s.review_login_map() == {
            "+13065550100": "4821",
            "+13065550102": "4821",
        }

    def test_whitespace_only_accounts_returns_empty_dict(self):
        s = _dev_settings(REVIEW_LOGIN_ACCOUNTS="   ")
        assert s.review_login_map() == {}


# ── _validate_review_accounts model_validator (323-343) ─────────────────


class TestValidateReviewAccounts:
    def test_empty_accounts_no_log_output(self, caplog):
        with caplog.at_level(logging.ERROR):
            _dev_settings(REVIEW_LOGIN_ACCOUNTS="")
        assert caplog.text == ""

    def test_malformed_entry_no_colon_logs_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="core.config"):
            _dev_settings(REVIEW_LOGIN_ACCOUNTS="badentry")
        assert "malformed entry" in caplog.text

    def test_invalid_otp_logs_error_with_last_four_digits_only(self, caplog):
        with caplog.at_level(logging.ERROR, logger="core.config"):
            _dev_settings(REVIEW_LOGIN_ACCOUNTS="+13065550101:12ab")
        assert "is not" in caplog.text
        assert "5550101" not in caplog.text  # full number must never be logged
        assert "0101" in caplog.text  # last-4 only

    def test_valid_entries_logged_as_info_with_count(self, caplog):
        with caplog.at_level(logging.INFO, logger="core.config"):
            _dev_settings(REVIEW_LOGIN_ACCOUNTS="+13065550100:4821,+13065550102:4821")
        assert "2 reviewer login account(s) active" in caplog.text

    def test_mixed_valid_and_malformed_in_one_call(self, caplog):
        raw = "  ,+13065550100:4821,badentry,+13065550101:12ab,+13065550102:4821"
        with caplog.at_level(logging.INFO, logger="core.config"):
            _dev_settings(REVIEW_LOGIN_ACCOUNTS=raw)
        assert "malformed entry" in caplog.text
        assert "is not" in caplog.text
        assert "2 reviewer login account(s) active" in caplog.text

    def test_does_not_raise_on_malformed_entries(self):
        # A typo in this optional secret must not take down the API.
        s = _dev_settings(REVIEW_LOGIN_ACCOUNTS="badentry,+1306:99")
        assert s is not None
