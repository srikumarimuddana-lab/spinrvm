"""
PIPEDA log hygiene: admin emails must never appear raw in log lines.

Pins the redaction contract for the lockout helpers in
backend/routes/admin/auth.py:
  - _log_safe_email produces a stable non-PII digest
  - _is_account_locked / _record_login_failure / _clear_login_failures
    log the digest, never the raw address, when Redis fails
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.admin import auth as admin_auth

RAW_EMAIL = "ops.admin@spinr.ca"


@pytest.mark.unit
class TestLogSafeEmail:
    def test_digest_contains_no_raw_email(self):
        safe = admin_auth._log_safe_email(RAW_EMAIL)
        assert RAW_EMAIL not in safe
        assert "spinr.ca" not in safe
        assert safe.startswith("email_sha256:")

    def test_digest_is_stable_and_normalized(self):
        # Same address modulo case/whitespace → same digest, so log lines
        # for one account stay correlatable.
        assert admin_auth._log_safe_email(RAW_EMAIL) == admin_auth._log_safe_email(f"  {RAW_EMAIL.upper()}  ")

    def test_different_emails_differ(self):
        assert admin_auth._log_safe_email(RAW_EMAIL) != admin_auth._log_safe_email("other.admin@spinr.ca")


@pytest.mark.unit
@pytest.mark.anyio
class TestLockoutHelpersRedactEmail:
    async def test_is_account_locked_redis_failure_redacts_email(self, caplog):
        with (
            patch(
                "backend.routes.admin.auth.redis_get",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            caplog.at_level(logging.ERROR),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await admin_auth._is_account_locked(RAW_EMAIL)
        assert exc_info.value.status_code == 503
        assert RAW_EMAIL not in caplog.text
        assert admin_auth._log_safe_email(RAW_EMAIL) in caplog.text

    async def test_record_login_failure_redis_failure_redacts_email(self, caplog):
        with (
            patch(
                "backend.routes.admin.auth.redis_incr",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            caplog.at_level(logging.ERROR),
        ):
            await admin_auth._record_login_failure(RAW_EMAIL)
        assert RAW_EMAIL not in caplog.text
        assert admin_auth._log_safe_email(RAW_EMAIL) in caplog.text

    async def test_clear_login_failures_redis_failure_redacts_email(self, caplog):
        with (
            patch(
                "backend.routes.admin.auth.redis_delete",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            caplog.at_level(logging.ERROR),
        ):
            await admin_auth._clear_login_failures(RAW_EMAIL)
        assert RAW_EMAIL not in caplog.text
        assert admin_auth._log_safe_email(RAW_EMAIL) in caplog.text
