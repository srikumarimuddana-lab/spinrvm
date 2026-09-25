"""Regression: admin JTI-revocation denylist fails OPEN on a Redis outage.

A Redis (Upstash) blip must not lock every admin out of the live dashboard —
the per-JTI denylist is a fast-path, and the authoritative revocation
(/auth/logout-all) is enforced via the DB token_version check, not Redis. But
a genuine revocation (Redis reachable, key present) must still be blocked.

Uses the admin-001 bootstrap identity so the staff-table DB lookup is skipped
and the test exercises only the revocation branch.

`get_env_admin_token_version` is mocked to return a fixed 0 in every test:
admin-001's own token_version DB read (utils/env_admin_tokens.py, added
2026-09-21) is a separate, later check in `_verify_admin_payload` from the
one these tests exercise, so it's stubbed out here rather than left to hit
the real (mocked-empty) `settings` table and fail closed with an unrelated
503 before the revocation-branch logic under test ever runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import dependencies
from dependencies import JWT_AUD_ADMIN, _verify_admin_payload


def _admin_payload() -> dict:
    return {
        "user_id": "admin-001",
        "role": "admin",
        "email": "ops@spinr.ca",
        "aud": JWT_AUD_ADMIN,
        "jti": "tok-1",
        "token_version": 0,
    }


@pytest.mark.anyio
async def test_fails_open_when_redis_unavailable(monkeypatch):
    """redis_get raising (Upstash down) must NOT reject a valid admin token."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(side_effect=RuntimeError("Upstash unreachable")))
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
    user = await _verify_admin_payload(_admin_payload())
    assert user is not None
    assert user["id"] == "admin-001"
    assert user["role"] == "admin"


@pytest.mark.anyio
async def test_blocks_when_redis_reports_revoked(monkeypatch):
    """When Redis IS reachable and the JTI is on the denylist, still 401."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value="1"))
    with pytest.raises(HTTPException) as exc:
        await _verify_admin_payload(_admin_payload())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_TOKEN_REVOKED"


@pytest.mark.anyio
async def test_allows_when_redis_reports_not_revoked(monkeypatch):
    """Healthy Redis, JTI absent from denylist → token passes."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
    user = await _verify_admin_payload(_admin_payload())
    assert user is not None
    assert user["id"] == "admin-001"


# ── SEC-A2-003: ADMIN_REVOCATION_FAIL_CLOSED switch ──────────────────────────
#
# The tests above pin the default (flag off) behaviour. These pin both modes of
# the switch, and that the flag cannot leak into the rider/driver paths.

_ERR_METRIC = "spinr_auth_revocation_check_error_total"


def _set_fail_closed(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(dependencies.settings, "ADMIN_REVOCATION_FAIL_CLOSED", value)


def _redis_down(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(side_effect=RuntimeError("Upstash unreachable")))


def test_flag_defaults_off():
    """Merging must not change production behaviour: the field defaults False."""
    assert type(dependencies.settings).model_fields["ADMIN_REVOCATION_FAIL_CLOSED"].default is False


@pytest.mark.anyio
async def test_flag_off_redis_error_accepts_logs_error_and_counts(monkeypatch):
    _set_fail_closed(monkeypatch, False)
    _redis_down(monkeypatch)
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
    metric = MagicMock()
    monkeypatch.setattr(dependencies, "_metric_inc", metric)
    spy = MagicMock()
    monkeypatch.setattr(dependencies, "logger", spy)

    user = await _verify_admin_payload(_admin_payload())

    assert user is not None and user["id"] == "admin-001"
    metric.assert_called_once_with(_ERR_METRIC, {"outcome": "fail_open"})
    # ERROR with a traceback via loguru's .opt(exception=True) (an exc_info=
    # kwarg would be silently swallowed), domain bound for the Sentry sink.
    spy.bind.assert_any_call(domain="auth", user_id="admin-001")
    spy.bind.return_value.opt.assert_any_call(exception=True)
    assert spy.bind.return_value.opt.return_value.error.called
    assert not spy.warning.called


@pytest.mark.anyio
async def test_flag_on_redis_error_returns_503(monkeypatch):
    _set_fail_closed(monkeypatch, True)
    _redis_down(monkeypatch)
    env_version = AsyncMock(return_value=0)
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", env_version)
    metric = MagicMock()
    monkeypatch.setattr(dependencies, "_metric_inc", metric)
    spy = MagicMock()
    monkeypatch.setattr(dependencies, "logger", spy)

    with pytest.raises(HTTPException) as exc:
        await _verify_admin_payload(_admin_payload())

    assert exc.value.status_code == 503
    # Generic detail — no Redis / exception internals leak to the client.
    assert "Upstash" not in exc.value.detail
    assert "Redis" not in exc.value.detail
    metric.assert_called_once_with(_ERR_METRIC, {"outcome": "fail_closed"})
    assert spy.bind.return_value.opt.return_value.error.called
    # Rejected before any later check ran.
    env_version.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("fail_closed", [False, True])
async def test_revoked_token_with_healthy_redis_rejected_in_both_modes(monkeypatch, fail_closed):
    _set_fail_closed(monkeypatch, fail_closed)
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value="1"))
    metric = MagicMock()
    monkeypatch.setattr(dependencies, "_metric_inc", metric)

    with pytest.raises(HTTPException) as exc:
        await _verify_admin_payload(_admin_payload())

    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_TOKEN_REVOKED"
    metric.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("fail_closed", [False, True])
async def test_healthy_redis_not_revoked_counts_nothing(monkeypatch, fail_closed):
    """The error counter must stay flat on the normal path."""
    _set_fail_closed(monkeypatch, fail_closed)
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
    metric = MagicMock()
    monkeypatch.setattr(dependencies, "_metric_inc", metric)

    user = await _verify_admin_payload(_admin_payload())

    assert user is not None
    metric.assert_not_called()


@pytest.mark.anyio
async def test_flag_on_does_not_touch_mobile_tokens(monkeypatch):
    """A rider/driver token is not an admin payload: _verify_admin_payload
    returns None before the JTI denylist, so the flag and a Redis outage cannot
    503 it."""
    _set_fail_closed(monkeypatch, True)
    redis = AsyncMock(side_effect=RuntimeError("Upstash unreachable"))
    monkeypatch.setattr(dependencies, "redis_get", redis)

    result = await _verify_admin_payload(
        {"user_id": "rider-1", "aud": "spinr:rider", "jti": "tok-r", "token_version": 0}
    )

    assert result is None
    redis.assert_not_awaited()


@pytest.mark.anyio
async def test_flag_on_leaves_rider_driver_session_tombstone_fail_open(monkeypatch):
    """utils/session_revocation (rider/driver logout tombstones) keeps its
    documented fail-open posture regardless of the admin-only flag."""
    from utils import session_revocation

    _set_fail_closed(monkeypatch, True)
    monkeypatch.setattr(session_revocation, "redis_get", AsyncMock(side_effect=RuntimeError("Upstash unreachable")))

    assert await session_revocation.is_session_revoked("sess-1") is False
