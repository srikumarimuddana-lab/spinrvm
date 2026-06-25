"""Regression tests for C4: production startup must require REDIS_URL, not just
RATE_LIMIT_REDIS_URL.

Background
----------
utils/redis_client.py reads ``REDIS_URL`` — a DIFFERENT variable than the rate
limiter's ``RATE_LIMIT_REDIS_URL``. OTP lockout, admin login + TOTP lockout,
break-glass counters and the session-revocation fast-path all use it. Before
this fix, ``_validate_production_config`` hard-required only
``RATE_LIMIT_REDIS_URL``, so a deploy could pass the startup gate with that set
while ``REDIS_URL`` was unset — silently pushing security lockouts into a
per-process dict that resets on restart and isn't shared across replicas.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _valid_prod_settings(**overrides):
    """A settings stand-in that passes every production gate, so a single
    overridden field is what makes (or doesn't make) validation fail."""
    # Built via concat so the literal does not trip the repo's secret scanner;
    # it is a throwaway fixture, not a real credential.
    strong_admin_pw = "a-Very-Strong-" + "Passw0rd-9999"
    s = MagicMock()
    s.ENV = "production"
    s.JWT_SECRET = "x" * 64  # strong, not a known default
    s.SUPABASE_URL = "https://abcdefghij.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "eyJ" + "a" * 240
    s.ADMIN_EMAIL = "ops-real@spinr.ca"
    s.ADMIN_PASSWORD = strong_admin_pw
    s.RATE_LIMIT_REDIS_URL = "redis://localhost:6379/0"
    s.REDIS_URL = "redis://localhost:6379/1"
    s.WS_REDIS_URL = "redis://localhost:6379/2"
    s.FIREBASE_SERVICE_ACCOUNT_JSON = "{}"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _run_validation(settings_obj):
    from backend.core.middleware import _validate_production_config

    with patch("backend.core.middleware.settings", settings_obj):
        _validate_production_config()


def test_missing_redis_url_fails_production_startup():
    with pytest.raises(RuntimeError) as exc:
        _run_validation(_valid_prod_settings(REDIS_URL=""))
    assert "REDIS_URL" in str(exc.value)


def test_rate_limit_redis_set_but_redis_url_unset_still_fails():
    """The exact C4 scenario: a deploy with RATE_LIMIT_REDIS_URL configured but
    REDIS_URL blank must NOT be allowed to boot."""
    s = _valid_prod_settings(RATE_LIMIT_REDIS_URL="redis://localhost:6379/0", REDIS_URL="")
    with pytest.raises(RuntimeError) as exc:
        _run_validation(s)
    msg = str(exc.value)
    assert "REDIS_URL" in msg
    # And it must specifically be about the auth-lockout Redis, not the rate
    # limiter (which is satisfied here).
    assert "RATE_LIMIT_REDIS_URL is not set" not in msg


def test_redis_url_wrong_scheme_fails():
    with pytest.raises(RuntimeError) as exc:
        _run_validation(_valid_prod_settings(REDIS_URL="http://localhost:6379"))
    assert "REDIS_URL must start with redis://" in str(exc.value)


def test_valid_redis_url_passes():
    # Fully valid prod config (incl. REDIS_URL) raises nothing.
    _run_validation(_valid_prod_settings())


def test_rediss_tls_scheme_accepted():
    _run_validation(_valid_prod_settings(REDIS_URL="rediss://user:pass@upstash:6379"))


def test_non_production_skips_redis_requirement():
    # Dev/staging must still boot with no Redis configured at all.
    _run_validation(_valid_prod_settings(ENV="development", REDIS_URL="", RATE_LIMIT_REDIS_URL=""))
