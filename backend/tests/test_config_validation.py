"""Tests for production config validation guards in core/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _make_settings(**overrides):
    """Build a Settings instance with production-safe defaults, then apply overrides."""
    from backend.core.config import Settings

    defaults = {
        "ENV": "production",
        "JWT_SECRET": "a" * 48,
        "ADMIN_PASSWORD": "strong-prod-password-123!",
        "SUPABASE_URL": "https://xxx.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-placeholder",
        "FIREBASE_DRIVER_APP_ID": "1:123:android:abc",
        "FIREBASE_RIDER_APP_ID": "1:123:ios:def",
        "WS_REDIS_URL": "redis://localhost:6379/0",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestWsRedisUrlRequired:
    def test_ws_redis_url_required_in_production(self):
        with pytest.raises(ValidationError) as exc_info:
            _make_settings(WS_REDIS_URL="", RATE_LIMIT_REDIS_URL="")
        assert "WS_REDIS_URL" in str(exc_info.value)

    def test_rate_limit_redis_url_satisfies_ws_requirement(self):
        s = _make_settings(WS_REDIS_URL="", RATE_LIMIT_REDIS_URL="redis://localhost:6379/1")
        assert s.ENV == "production"

    def test_ws_redis_url_not_required_in_dev(self):
        s = _make_settings(ENV="development", WS_REDIS_URL="", RATE_LIMIT_REDIS_URL="")
        assert s.ENV == "development"
