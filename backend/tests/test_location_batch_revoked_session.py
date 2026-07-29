"""Location ingest rejects a signed-out session, and fails open otherwise.

Defense in depth behind the driver app's logout teardown: a signed-out app still
holds an access token valid for the rest of its exp, and a stale build will keep
uploading GPS with it.

The fail-open cases matter as much as the rejection. A false 401 drops
breadcrumbs that settle a driver's billed distance and back the SGI
per-insurance-period audit, so every ambiguous state is pinned here.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

import pytest
from fastapi import HTTPException

from routes.drivers import location
from utils import session_revocation

pytestmark = pytest.mark.unit


@pytest.fixture
def flag_on(monkeypatch):
    """app_settings with the guard explicitly enabled."""

    async def _settings():
        return {"location_reject_revoked_sessions_enabled": True}

    monkeypatch.setattr("settings_loader.get_app_settings", _settings)
    return _settings


@pytest.mark.anyio
async def test_revoked_session_is_rejected(mock_redis, flag_on):
    await session_revocation.revoke_session("sess-dead")

    with pytest.raises(HTTPException) as exc:
        await location._guard_revoked_session("sess-dead")

    # 401 not 403: the credential is dead, so the client re-auths rather than
    # retrying the same batch forever.
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_SESSION_REVOKED"


@pytest.mark.anyio
async def test_live_session_is_allowed(mock_redis, flag_on):
    assert await location._guard_revoked_session("sess-live") is None


@pytest.mark.anyio
async def test_no_session_id_is_allowed(mock_redis, flag_on):
    """Firebase-authenticated tokens carry no session_id claim."""
    assert await location._guard_revoked_session(None) is None
    assert await location._guard_revoked_session("") is None


@pytest.mark.anyio
async def test_flag_off_allows_a_revoked_session(mock_redis, monkeypatch):
    """The app_settings kill switch turns the guard off without a redeploy."""

    async def _settings():
        return {"location_reject_revoked_sessions_enabled": False}

    monkeypatch.setattr("settings_loader.get_app_settings", _settings)
    await session_revocation.revoke_session("sess-dead")

    assert await location._guard_revoked_session("sess-dead") is None


@pytest.mark.anyio
async def test_guard_defaults_on_when_flag_absent(mock_redis, monkeypatch):
    """An app_settings row predating this change must still be protected —
    otherwise the guard ships dark and the PIPEDA hole stays open."""

    async def _settings():
        return {}

    monkeypatch.setattr("settings_loader.get_app_settings", _settings)
    await session_revocation.revoke_session("sess-dead")

    with pytest.raises(HTTPException) as exc:
        await location._guard_revoked_session("sess-dead")
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_explicit_null_flag_is_treated_as_enabled(mock_redis, monkeypatch):
    """A NULL app_settings column means "unset → default", not "disabled".
    `.get(key, True)` returns None for a NULL and bool(None) is False, which
    would have shipped the guard dark on any row that has the key set to null."""

    async def _settings():
        return {"location_reject_revoked_sessions_enabled": None}

    monkeypatch.setattr("settings_loader.get_app_settings", _settings)
    await session_revocation.revoke_session("sess-dead")

    with pytest.raises(HTTPException) as exc:
        await location._guard_revoked_session("sess-dead")
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_falsy_but_explicit_flag_values_disable_the_guard(mock_redis, monkeypatch):
    """0 / "" / False are deliberate opt-outs, unlike None."""
    await session_revocation.revoke_session("sess-dead")

    for value in (False, 0, ""):
        async def _settings(_v=value):
            return {"location_reject_revoked_sessions_enabled": _v}

        monkeypatch.setattr("settings_loader.get_app_settings", _settings)
        assert await location._guard_revoked_session("sess-dead") is None, value


@pytest.mark.anyio
async def test_unreadable_settings_fails_open(mock_redis, monkeypatch):
    async def _boom():
        raise RuntimeError("settings table unreachable")

    monkeypatch.setattr("settings_loader.get_app_settings", _boom)
    await session_revocation.revoke_session("sess-dead")

    # Losing GPS for every driver during a settings outage is worse than a
    # zombie writer the client fix already stopped.
    assert await location._guard_revoked_session("sess-dead") is None


@pytest.mark.anyio
async def test_redis_outage_fails_open(flag_on, monkeypatch):
    async def _boom(_key):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(session_revocation, "redis_get", _boom)
    assert await location._guard_revoked_session("sess-dead") is None


@pytest.mark.anyio
async def test_guard_runs_before_either_persistence_path(mock_redis, flag_on, monkeypatch):
    """The guard sits in update_location_batch, ahead of the v1/v2 split, so a
    revoked session cannot reach either writer."""
    called = {"v2": False}

    async def _should_not_run(*_args, **_kwargs):
        called["v2"] = True
        return {}

    monkeypatch.setattr(location, "_persist_v2_location_batch", _should_not_run)
    await session_revocation.revoke_session("sess-dead")

    with pytest.raises(HTTPException) as exc:
        await location.update_location_batch(
            {
                "ride_id": "ride_1",
                "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                "points": [
                    {"sequence_number": 0, "captured_at": "2026-07-29T10:00:00Z", "lat": 50.4, "lng": -104.6},
                ],
            },
            current_user={"id": "user-1"},
            token_session_id="sess-dead",
        )

    assert exc.value.status_code == 401
    assert called["v2"] is False
