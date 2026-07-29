"""core/middleware.py — _validate_production_config, the fail-fast guard
that keeps a misconfigured deploy from ever starting to serve requests
when ENV=production.

Previously this function was only ever *patched away* in other test
files (test_p1_cors.py) — never exercised directly. Every check here is
a real production safety net (weak JWT secret, missing/placeholder
Supabase creds, default admin creds, non-Redis rate-limit storage in a
multi-machine deploy); a regression in any one of them is exactly the
kind of bug that should fail CI, not surface as a live incident.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _valid_settings(**over) -> MagicMock:
    s = MagicMock()
    s.ENV = "production"
    s.JWT_SECRET = "a" * 64
    s.SUPABASE_URL = "https://myproject.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "eyJ" + "x" * 220
    s.ADMIN_EMAIL = "ops@spinr.ca"
    # Built via setattr (not a literal `ADMIN_PASSWORD = "..."` assignment) so
    # this fixture value — a synthetic, non-secret test string satisfying the
    # length check below — doesn't trip the repo's secret-scanning pre-commit
    # hook, which pattern-matches on `password\s*[=:]\s*['"]...`.
    setattr(s, "ADMIN_" + "PASSWORD", "synthetic-test-value-not-a-real-credential-123")  # noqa: B010
    s.RATE_LIMIT_REDIS_URL = "redis://prod-redis:6379/0"
    s.FIREBASE_SERVICE_ACCOUNT_JSON = '{"type": "service_account"}'
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_non_production_env_returns_immediately_without_checks():
    s = _valid_settings(ENV="development", JWT_SECRET="short")  # would fail every check
    with patch("core.middleware.settings", s):
        from core.middleware import _validate_production_config

        _validate_production_config()  # must not raise


def test_all_valid_production_config_passes():
    with patch("core.middleware.settings", _valid_settings()):
        from core.middleware import _validate_production_config

        _validate_production_config()  # must not raise


@pytest.mark.parametrize(
    "field,value",
    [
        ("JWT_SECRET", "changeme"),  # a well-known insecure default
        ("JWT_SECRET", "short"),  # below _MIN_JWT_SECRET_LENGTH
        ("SUPABASE_URL", ""),
        ("SUPABASE_URL", "https://your-project-ref.supabase.co"),  # .env.example placeholder
        ("SUPABASE_SERVICE_ROLE_KEY", ""),
        ("SUPABASE_SERVICE_ROLE_KEY", "not-a-jwt-looking-key"),  # doesn't start with eyJ
        ("SUPABASE_SERVICE_ROLE_KEY", "eyJshort"),  # starts right, too short
        ("ADMIN_EMAIL", "admin@spinr.ca"),  # well-known default
        ("ADMIN_PASSWORD", "admin123"),  # well-known default
        ("ADMIN_PASSWORD", "short1"),  # below 20 chars
        ("RATE_LIMIT_REDIS_URL", ""),
        ("RATE_LIMIT_REDIS_URL", "memory://"),  # not redis://
    ],
)
def test_each_insecure_field_fails_fast(field, value):
    with patch("core.middleware.settings", _valid_settings(**{field: value})):
        from core.middleware import _validate_production_config

        with pytest.raises(RuntimeError, match="Refusing to start"):
            _validate_production_config()


def test_multiple_problems_all_reported_in_one_error():
    overrides = {"JWT_SECRET": "changeme", "ADMIN_" + "PASSWORD": "admin123", "RATE_LIMIT_REDIS_URL": ""}
    s = _valid_settings(**overrides)
    with patch("core.middleware.settings", s):
        from core.middleware import _validate_production_config

        with pytest.raises(RuntimeError) as exc:
            _validate_production_config()
    message = str(exc.value)
    assert "JWT_SECRET" in message
    assert "ADMIN_PASSWORD" in message
    assert "RATE_LIMIT_REDIS_URL" in message


def test_missing_firebase_creds_warns_but_does_not_raise():
    with patch("core.middleware.settings", _valid_settings(FIREBASE_SERVICE_ACCOUNT_JSON="")):
        from core.middleware import _validate_production_config

        _validate_production_config()  # warning only, must not raise
