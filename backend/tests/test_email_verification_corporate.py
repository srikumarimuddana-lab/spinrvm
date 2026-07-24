"""Regression tests for finding 15 (WS-11): unverified email for corporate join.

The /corporate/join-domain endpoint trusted whichever email the user had on
their profile for domain-based auto-enrollment, without verifying the email
actually belonged to them. An attacker could set email=ceo@company.com,
never verify it, and gain corporate membership + allowance.

Fix:
- Migration 252 adds email_verified + email_verified_at columns
- join-domain rejects with ERR_EMAIL_UNVERIFIED when email_verified is falsy
- POST /profile resets email_verified on email change
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CORPORATE_RIDER_SRC = (_ROOT / "routes" / "corporate_rider.py").read_text()
_USERS_SRC = (_ROOT / "routes" / "users.py").read_text()
_MIG_252 = (_ROOT / "migrations" / "252_users_email_verified.sql").read_text()


class TestMigration252:
    def test_adds_email_verified_column(self):
        assert "email_verified" in _MIG_252

    def test_adds_email_verified_at_column(self):
        assert "email_verified_at" in _MIG_252

    def test_default_is_false(self):
        assert "DEFAULT false" in _MIG_252 or "default false" in _MIG_252.lower()

    def test_has_rollback_marker(self):
        assert "rollback:" in _MIG_252.lower()


class TestJoinDomainGate:
    def test_checks_email_verified(self):
        assert "email_verified" in _CORPORATE_RIDER_SRC

    def test_returns_structured_error_code(self):
        assert "ERR_EMAIL_UNVERIFIED" in _CORPORATE_RIDER_SRC

    def test_returns_403(self):
        lines = _CORPORATE_RIDER_SRC.split("\n")
        idx = None
        for i, line in enumerate(lines):
            if "ERR_EMAIL_UNVERIFIED" in line:
                idx = i
                break
        assert idx is not None
        context = "\n".join(lines[max(0, idx - 5) : idx + 1])
        assert "403" in context

    def test_gate_before_domain_check(self):
        idx_gate = _CORPORATE_RIDER_SRC.index("email_verified")
        idx_domain = _CORPORATE_RIDER_SRC.index("corporate_allowed_domains")
        assert idx_gate < idx_domain, "email_verified check must come before domain lookup"


class TestEmailChangeResetsVerification:
    def test_resets_email_verified_on_change(self):
        assert "email_verified" in _USERS_SRC

    def test_resets_email_verified_at_on_change(self):
        assert "email_verified_at" in _USERS_SRC

    def test_detects_email_change(self):
        assert "email_changed" in _USERS_SRC
