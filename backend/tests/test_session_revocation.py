"""Unit tests for the session-revocation tombstone.

The contract that matters most here is *fail-open*: every ambiguous state must
resolve to "allow". A false rejection on the location-ingest path drops GPS
points that settle billed distance and back the SGI per-insurance-period audit,
so the tests below pin each ambiguous case explicitly rather than only testing
the happy path.
"""

from __future__ import annotations

import pytest

from utils import session_revocation


pytestmark = pytest.mark.unit


# ── should_tombstone ────────────────────────────────────────────────────────


def test_tombstones_when_session_is_the_current_one():
    assert session_revocation.should_tombstone("sess-a", "sess-a") is True


def test_no_tombstone_without_a_session_id_claim():
    """Firebase-authenticated tokens carry no session_id — nothing to key on."""
    assert session_revocation.should_tombstone(None, "sess-a") is False
    assert session_revocation.should_tombstone("", "sess-a") is False


def test_no_tombstone_when_another_device_owns_the_current_session():
    """Multi-device guard.

    users.current_session_id is a single column, so a second device logging in
    overwrites it. If the logging-out token is on the stale session, tombstoning
    it would revoke the *other* device's traffic. Let it die on its own exp.
    """
    assert session_revocation.should_tombstone("sess-old", "sess-new") is False


def test_no_tombstone_when_current_session_is_unknown():
    assert session_revocation.should_tombstone("sess-a", None) is False


# ── revoke_session / is_session_revoked round trip ──────────────────────────


@pytest.mark.anyio
async def test_revoked_session_is_reported_as_revoked(mock_redis):
    assert await session_revocation.revoke_session("sess-a") is True
    assert await session_revocation.is_session_revoked({"session_id": "sess-a"}) is True


@pytest.mark.anyio
async def test_untouched_session_is_not_revoked(mock_redis):
    await session_revocation.revoke_session("sess-a")
    assert await session_revocation.is_session_revoked({"session_id": "sess-b"}) is False


@pytest.mark.anyio
async def test_revoking_empty_session_id_is_a_noop(mock_redis):
    assert await session_revocation.revoke_session("") is False
    assert mock_redis == {}


@pytest.mark.anyio
async def test_tombstone_ttl_matches_access_token_lifetime(mock_redis, monkeypatch):
    """The tombstone only needs to outlive the longest-lived access token that
    could still carry the session_id — no longer, so it can't grow unbounded."""
    from core.config import settings

    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15, raising=False)
    assert session_revocation._tombstone_ttl_seconds() == 15 * 60 + 60


# ── fail-open behaviour ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_missing_session_id_claim_is_allowed(mock_redis):
    assert await session_revocation.is_session_revoked({}) is False
    assert await session_revocation.is_session_revoked({"session_id": None}) is False


@pytest.mark.anyio
async def test_non_dict_payload_is_allowed(mock_redis):
    assert await session_revocation.is_session_revoked(None) is False  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_redis_read_failure_allows_the_request(monkeypatch):
    """Redis down must never 401 a legitimate driver — the whole reason this is
    a positive-evidence tombstone rather than an absence check."""

    async def _boom(_key):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(session_revocation, "redis_get", _boom)
    assert await session_revocation.is_session_revoked({"session_id": "sess-a"}) is False


@pytest.mark.anyio
async def test_redis_write_failure_does_not_break_logout(monkeypatch):
    """A logout must complete for the user even when the tombstone can't be
    stored; the refresh-token revocation is the durable part."""

    async def _boom(_key, _value, ttl=None):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(session_revocation, "redis_set", _boom)
    assert await session_revocation.revoke_session("sess-a") is False


@pytest.mark.anyio
async def test_tombstone_prefix_is_registered_for_admin_accounting(mock_redis):
    """Admin Redis dashboard counts by known prefix; an unregistered prefix
    would make these keys invisible."""
    from utils.redis_client import KNOWN_KEY_PREFIXES

    assert session_revocation._TOMBSTONE_PREFIX in KNOWN_KEY_PREFIXES
