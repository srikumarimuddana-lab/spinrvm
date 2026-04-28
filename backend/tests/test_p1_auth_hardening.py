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
# Force-replace slowapi with a MagicMock regardless of import order so that
# Limiter.return_value exists and limit() is a no-op decorator factory.


def _noop_limit_factory(*args, **kwargs):
    def _passthrough(fn):
        return fn

    return _passthrough


_slowapi_mock = MagicMock()
_slowapi_mock.Limiter.return_value.limit = _noop_limit_factory
sys.modules["slowapi"] = _slowapi_mock

# RateLimitExceeded must be a real Exception subclass so that
# core/middleware.py's app.add_exception_handler(RateLimitExceeded, ...)
# survives Starlette's issubclass() guard when backend.server is first
# imported in the same pytest session.


class _FakeRateLimitExceeded(Exception):
    pass


_slowapi_errors_mock = MagicMock()
_slowapi_errors_mock.RateLimitExceeded = _FakeRateLimitExceeded
sys.modules["slowapi.errors"] = _slowapi_errors_mock
sys.modules["slowapi.util"] = MagicMock()


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
            from importlib import reload

            import backend.core.config as cfg_mod

            reload(cfg_mod)
            return cfg_mod.Settings()
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
            from importlib import reload

            import backend.core.config as cfg_mod

            reload(cfg_mod)
            cfg_mod.Settings()  # must not raise
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

        with patch.dict(
            sys.modules,
            {
                "firebase_admin.auth": fb_stub,
                # Restore the noop limiter so the fresh auth import below
                # doesn't pick up the real slowapi (which requires a real
                # starlette Request).
                "slowapi": _slowapi_mock,
                "slowapi.errors": _slowapi_errors_mock,
                "slowapi.util": MagicMock(),
            },
        ):
            # `from firebase_admin import auth` resolves via the module object's
            # .auth attribute, not via sys.modules["firebase_admin.auth"].
            # Set both so the lazy import inside the route function finds the stub.
            sys.modules["firebase_admin"].auth = fb_stub
            # Force a fresh import by removing both the sys.modules entry and
            # any stale package attribute (patch.dict full-restore can leave
            # a stale .auth attribute on the backend.routes package object).
            # Also pop utils.rate_limiter so the fresh auth import picks up the
            # noop limiter (from the _slowapi_mock above) rather than the cached
            # real Limiter that's already in sys.modules from other test files.
            sys.modules.pop("backend.routes.auth", None)
            sys.modules.pop("utils.rate_limiter", None)
            _pkg = sys.modules.get("backend.routes")
            if _pkg is not None:
                try:
                    delattr(_pkg, "auth")
                except AttributeError:
                    pass
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

        # patch.dict restores sys.modules but does NOT restore package attributes.
        # backend.routes.auth may now point to the stale FRESH_AUTH imported
        # inside the block while sys.modules["backend.routes.auth"] points to
        # the original module. Always remove the stale attribute so subsequent
        # `from backend.routes import auth` resolves through sys.modules only.
        _pkg = sys.modules.get("backend.routes")
        if _pkg is not None:
            try:
                delattr(_pkg, "auth")
            except AttributeError:
                pass

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "auth_persist_failed"

    async def test_existing_user_session_update_failure_raises_503(self):
        fake_payload = {"uid": "firebase_uid_2", "phone_number": "+13061234568", "aud": "driver-app"}
        existing_user = {"id": "firebase_uid_2", "phone": "+13061234568", "token_version": 0}
        fb_stub = self._make_firebase_stub(fake_payload)

        with patch.dict(
            sys.modules,
            {
                "firebase_admin.auth": fb_stub,
                "slowapi": _slowapi_mock,
                "slowapi.errors": _slowapi_errors_mock,
                "slowapi.util": MagicMock(),
            },
        ):
            sys.modules["firebase_admin"].auth = fb_stub
            sys.modules.pop("backend.routes.auth", None)
            sys.modules.pop("utils.rate_limiter", None)
            _pkg = sys.modules.get("backend.routes")
            if _pkg is not None:
                try:
                    delattr(_pkg, "auth")
                except AttributeError:
                    pass
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

        # Same unconditional stale-attribute cleanup as the test above.
        _pkg = sys.modules.get("backend.routes")
        if _pkg is not None:
            try:
                delattr(_pkg, "auth")
            except AttributeError:
                pass

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "auth_session_update_failed"
