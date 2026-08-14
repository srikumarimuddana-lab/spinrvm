"""
Unit tests for ForcedUpgradeMiddleware's active-ride carve-out
(ACTION_ITEMS.md task #11, backend/core/middleware.py).

Verifies:
  - The new completion-critical driver endpoints (arrive/verify-otp/start/
    complete/location-batch/rides-active) bypass the 426 gate even when the
    client is below the configured minimum version.
  - A non-exempt endpoint (e.g. a driver-facing profile read, and the
    new-offer accept/decline/cancel/rate-rider paths that share the
    /drivers/rides/ prefix but must stay gated) still gets blocked when a
    minimum version is set and the client is below it.
  - The pre-existing settings/OTP exemptions are unaffected (additive-only
    change).
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub out heavy deps that middleware.py imports but the test env doesn't have.
_STUBS = ["slowapi", "slowapi.errors", "core.config", "utils.rate_limiter"]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

_core_config_mod = sys.modules["core.config"]
_original_settings = getattr(_core_config_mod, "settings", None)


@pytest.fixture(scope="module", autouse=True)
def _restore_core_config_settings():
    _core_config_mod.settings = MagicMock(ENV="development")
    yield
    if _original_settings is not None:
        _core_config_mod.settings = _original_settings


def _make_app() -> FastAPI:
    """Minimal FastAPI app with ForcedUpgradeMiddleware attached, mirroring
    the real driver-app route shapes involved in the trip-completion flow."""
    from core.middleware import ForcedUpgradeMiddleware

    app = FastAPI()

    @app.get("/api/v1/settings")
    async def get_settings():
        return {"ok": True}

    @app.post("/api/v1/auth/send-otp")
    async def send_otp():
        return {"ok": True}

    @app.post("/api/v1/auth/verify-otp")
    async def auth_verify_otp():
        return {"ok": True}

    @app.get("/api/v1/drivers/profile")
    async def driver_profile():
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/accept")
    async def accept(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/decline")
    async def decline(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/cancel")
    async def cancel(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/rate-rider")
    async def rate_rider(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/arrive")
    async def arrive(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/verify-otp")
    async def ride_verify_otp(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/start")
    async def start(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/rides/{ride_id}/complete")
    async def complete(ride_id: str):
        return {"ok": True}

    @app.post("/api/v1/drivers/location-batch")
    async def location_batch():
        return {"ok": True}

    @app.get("/api/v1/drivers/rides/active")
    async def rides_active():
        return {"ok": True}

    app.add_middleware(ForcedUpgradeMiddleware)
    return app


def _client_with_min_version(min_driver_app_version: str) -> TestClient:
    """Build a TestClient whose settings_loader.get_app_settings mock
    reports the given min_driver_app_version."""
    import settings_loader

    async def _fake_get_app_settings():
        return {"min_driver_app_version": min_driver_app_version, "min_rider_app_version": ""}

    settings_loader.get_app_settings = AsyncMock(side_effect=_fake_get_app_settings)
    app = _make_app()
    return TestClient(app, raise_server_exceptions=True)


OLD_DRIVER_HEADERS = {"X-App-Platform": "driver", "X-App-Version": "1.0.0"}
NEW_DRIVER_HEADERS = {"X-App-Platform": "driver", "X-App-Version": "9.9.9"}


class TestRideCarveoutBypassesGate:
    """Completion-critical endpoints must pass through even when the client
    is below the configured minimum — this is the fix under test."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = _client_with_min_version("2.0.0")
        yield

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/v1/drivers/rides/ride-1/arrive"),
            ("post", "/api/v1/drivers/rides/ride-1/verify-otp"),
            ("post", "/api/v1/drivers/rides/ride-1/start"),
            ("post", "/api/v1/drivers/rides/ride-1/complete"),
            ("post", "/api/v1/drivers/location-batch"),
            ("get", "/api/v1/drivers/rides/active"),
        ],
    )
    def test_carveout_path_bypasses_426_for_old_client(self, method, path):
        res = getattr(self._client, method)(path, headers=OLD_DRIVER_HEADERS)
        assert res.status_code != 426, f"{path} should be exempt from forced-upgrade gate"
        assert res.status_code == 200


class TestNonCarveoutRidePathsStillBlocked:
    """New-offer intake (accept/decline) and post-trip actions (cancel/
    rate-rider) share the /drivers/rides/ prefix but must remain gated —
    only the four completion-critical suffixes are exempt."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = _client_with_min_version("2.0.0")
        yield

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/drivers/rides/ride-1/accept",
            "/api/v1/drivers/rides/ride-1/decline",
            "/api/v1/drivers/rides/ride-1/cancel",
            "/api/v1/drivers/rides/ride-1/rate-rider",
        ],
    )
    def test_non_carveout_path_still_blocked(self, path):
        res = self._client.post(path, headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 426
        assert res.json()["detail"] == "upgrade_required"


class TestGeneralGateStillEnforced:
    @pytest.fixture(autouse=True)
    def client(self):
        self._client = _client_with_min_version("2.0.0")
        yield

    def test_unrelated_driver_endpoint_blocked_for_old_client(self):
        res = self._client.get("/api/v1/drivers/profile", headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 426

    def test_unrelated_driver_endpoint_allowed_for_new_client(self):
        res = self._client.get("/api/v1/drivers/profile", headers=NEW_DRIVER_HEADERS)
        assert res.status_code == 200

    def test_carveout_path_also_allowed_for_new_client(self):
        res = self._client.post("/api/v1/drivers/rides/ride-1/complete", headers=NEW_DRIVER_HEADERS)
        assert res.status_code == 200


class TestExistingExemptionsUnaffected:
    """Pre-existing exemptions (settings, auth OTP) must be untouched by
    this additive change."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = _client_with_min_version("2.0.0")
        yield

    def test_settings_still_exempt(self):
        res = self._client.get("/api/v1/settings", headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 200

    def test_auth_send_otp_still_exempt(self):
        res = self._client.post("/api/v1/auth/send-otp", headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 200

    def test_auth_verify_otp_still_exempt(self):
        res = self._client.post("/api/v1/auth/verify-otp", headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 200


class TestNoMinimumConfiguredStillPassesThrough:
    """Current production reality: no min_driver_app_version is set, so the
    whole gate (carve-out included) stays inert."""

    def test_empty_minimum_passes_through(self):
        client = _client_with_min_version("")
        res = client.post("/api/v1/drivers/rides/ride-1/complete", headers=OLD_DRIVER_HEADERS)
        assert res.status_code == 200
