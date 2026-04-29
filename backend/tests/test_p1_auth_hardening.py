"""
Regression tests for backend P1 auth-hardening items.

B-P1-1: FIREBASE_DRIVER_APP_ID + FIREBASE_RIDER_APP_ID must be set in
        production; startup raises ValueError when either is empty.
B-P1-2: JWT_SECRET must be ≥32 chars in production; startup raises
        ValueError for shorter secrets.
B-P1-5: firebase_auth DB-persist failure now raises HTTPException(503)
        and logs at ERROR (not WARNING); no silent orphaned-user path.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Stub heavy deps before any backend import.
_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# auth.py decorates endpoint functions with @limiter.limit(...) where limiter
# comes from slowapi.Limiter (stubbed as MagicMock).  A plain MagicMock
# decorator replaces the async def with a MagicMock, breaking await calls.
# Configure the stub to act as a no-op decorator factory instead.


def _noop_limit_factory(*args, **kwargs):
    def _passthrough(fn):
        return fn

    return _passthrough


sys.modules["slowapi"].Limiter.return_value.limit = _noop_limit_factory


# ── B-P1-2 + B-P1-1 ───────────────────────────────────────────────────────────


class TestProductionStartupGuards:
    """Settings._guard_production_secrets must reject weak or missing values."""

    def _make_settings(self, **overrides):
        """Return a fresh Settings instance with production env vars."""
        base = {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test_key",
            "JWT_SECRET": "a" * 32,  # 32 chars — just enough
            "ADMIN_PASSWORD": "StrongPass123!",
            "FIREBASE_DRIVER_APP_ID": "driver-app-id",
            "FIREBASE_RIDER_APP_ID": "rider-app-id",
            "ENV": "production",
        }
        base.update(overrides)
        for k, v in base.items():
            os.environ[k] = v
        try:
            # Import Settings class directly — do NOT reload the module.
            # reload() replaces the module-level `settings` singleton in
            # sys.modules["backend.core.config"], which breaks JWT signing
            # in tests that run after this one (InvalidSignatureError) because
            # the bare "core.config" entry is left unchanged while
            # "backend.core.config" now holds a Settings instance with a
            # different JWT_SECRET. pydantic-settings reads env vars at
            # instantiation time, so calling Settings() is sufficient.
            from backend.core.config import Settings

            return Settings()
        finally:
            for k in base:
                os.environ.pop(k, None)

    def test_strong_secret_passes(self):
        """32-char JWT_SECRET + both Firebase IDs → no exception."""
        self._make_settings()  # must not raise

    def test_short_jwt_secret_raises(self):
        """JWT_SECRET shorter than 32 chars must raise in production."""
        with pytest.raises(Exception, match="JWT_SECRET"):
            self._make_settings(JWT_SECRET="tooshort")

    def test_exactly_32_chars_passes(self):
        """Exactly 32 chars is the minimum — must not raise."""
        self._make_settings(JWT_SECRET="x" * 32)

    def test_missing_driver_app_id_raises(self):
        """Empty FIREBASE_DRIVER_APP_ID must raise in production."""
        with pytest.raises(Exception, match="FIREBASE_DRIVER_APP_ID"):
            self._make_settings(FIREBASE_DRIVER_APP_ID="")

    def test_missing_rider_app_id_raises(self):
        """Empty FIREBASE_RIDER_APP_ID must raise in production."""
        with pytest.raises(Exception, match="FIREBASE_RIDER_APP_ID"):
            self._make_settings(FIREBASE_RIDER_APP_ID="")

    def test_development_allows_short_secret(self):
        """Short JWT_SECRET is permitted outside production (dev/test)."""
        base = {
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test_key",
            "JWT_SECRET": "short",
            "ADMIN_PASSWORD": "anything",
            "ENV": "development",
        }
        for k, v in base.items():
            os.environ[k] = v
        try:
            from backend.core.config import Settings

            Settings()  # must not raise
        finally:
            for k in base:
                os.environ.pop(k, None)


# ── B-P1-5 ────────────────────────────────────────────────────────────────────


class TestFirebaseAuthDbFailureRaises503:
    """
    When the DB write fails during Firebase auth, the endpoint must raise
    HTTPException(503) and log at ERROR — not silently swallow the failure.
    """

    def _make_firebase_stub(self, payload: dict) -> MagicMock:
        """Return a firebase_admin.auth stub whose verify_id_token returns payload."""
        stub = MagicMock()
        stub.verify_id_token.return_value = payload
        return stub

    async def test_new_user_db_persist_failure_raises_503(self):
        fake_payload = {"uid": "firebase_uid_1", "phone_number": "+13061234567", "aud": "driver-app"}
        fb_stub = self._make_firebase_stub(fake_payload)

        with patch.dict(sys.modules, {"firebase_admin.auth": fb_stub}):
            # `from firebase_admin import auth` resolves via the module object's
            # .auth attribute, not via sys.modules["firebase_admin.auth"].
            # Set both so the lazy import inside the route function finds the stub.
            sys.modules["firebase_admin"].auth = fb_stub
            if "backend.routes.auth" in sys.modules:
                del sys.modules["backend.routes.auth"]
            from backend.routes import auth as auth_mod

            with (
                patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
                patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
                patch("backend.routes.auth.db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
                patch(
                    "backend.routes.auth.db_supabase.create_user",
                    AsyncMock(side_effect=Exception("DB write failed")),
                ),
            ):
                req = MagicMock()
                req.headers.get.return_value = "test-agent"
                resp = MagicMock()
                body = auth_mod.FirebaseAuthRequest(firebase_token="fake_token")

                with pytest.raises(HTTPException) as exc_info:
                    await auth_mod.firebase_auth_login(req, resp, body)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "auth_persist_failed"

    async def test_existing_user_session_update_failure_raises_503(self):
        fake_payload = {"uid": "firebase_uid_2", "phone_number": "+13061234568", "aud": "driver-app"}
        existing_user = {"id": "firebase_uid_2", "phone": "+13061234568", "token_version": 0}
        fb_stub = self._make_firebase_stub(fake_payload)

        with patch.dict(sys.modules, {"firebase_admin.auth": fb_stub}):
            sys.modules["firebase_admin"].auth = fb_stub
            if "backend.routes.auth" in sys.modules:
                del sys.modules["backend.routes.auth"]
            from backend.routes import auth as auth_mod

            with (
                patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
                patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=existing_user)),
                patch(
                    "backend.routes.auth.db_supabase.update_one",
                    AsyncMock(side_effect=Exception("DB update failed")),
                ),
            ):
                req = MagicMock()
                req.headers.get.return_value = "test-agent"
                resp = MagicMock()
                body = auth_mod.FirebaseAuthRequest(firebase_token="fake_token")

                with pytest.raises(HTTPException) as exc_info:
                    await auth_mod.firebase_auth_login(req, resp, body)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "auth_session_failed"
