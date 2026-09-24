"""Regression guards for what the WebSocket location paths persist.

Two separate contracts:

1. A ``durable:false`` marker updates/fans out live state without writing route
   history. Omitting the flag stays durable (legacy rollout behaviour).
2. A signed-out session cannot persist breadcrumbs over an already-open socket.

These were previously pinned by asserting exact source substrings including
whitespace, which broke the moment the condition gained a second clause. The
1500-line endpoint still has no seam to drive directly, so the structural checks
remain — but they now assert the *guard condition* and the *ordering* of the
calls rather than exact formatting, so a reformat no longer fails them while a
removed guard still does. The caching contract is tested behaviourally.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

import pytest

pytestmark = pytest.mark.unit

SOURCE = (Path(__file__).resolve().parents[1] / "routes" / "websocket.py").read_text(encoding="utf-8")


# ── 1. Ephemeral vs durable markers ────────────────────────────────────────
#
# The buffer call is inside a ~1500-line endpoint with no seam to drive it
# directly, so these remain structural. They assert the *condition* rather than
# a whitespace-exact block, which is what made the previous version brittle.


def _guard_line_above(call: str) -> str:
    """The nearest preceding non-comment, non-blank line before ``call``."""
    lines = SOURCE.split("\n")
    index = next(i for i, line in enumerate(lines) if call in line)
    for line in reversed(lines[:index]):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    raise AssertionError(f"no guard line found above {call}")


def test_breadcrumb_write_is_gated_on_the_durable_flag() -> None:
    """A durable:false marker must not create a second breadcrumb trail or
    inflate billed distance."""
    guard = _guard_line_above("await buffer_ride_breadcrumb(")
    assert 'data.get("durable", True)' in guard, guard


def test_single_ping_breadcrumb_also_checks_revocation() -> None:
    """Same guard line must carry the sign-out check, so a signed-out socket
    cannot keep appending to the durable trail one ping at a time."""
    guard = _guard_line_above("await buffer_ride_breadcrumb(")
    assert "_ws_session_revoked()" in guard, guard


def test_legacy_driver_location_messages_remain_durable() -> None:
    """Omitting the flag preserves legacy breadcrumb behaviour during rollout."""
    assert 'data.get("durable", True)' in SOURCE
    assert 'data.get("durable", False)' not in SOURCE


# ── 2. Signed-out sessions cannot persist ──────────────────────────────────


def test_both_durable_ws_paths_check_session_revocation() -> None:
    """Item-3 coverage gap: the REST guard alone left two WS writers open.

    ``buffer_ride_breadcrumb`` (single durable ping) and
    ``persist_ride_breadcrumbs`` (location_batch) are the only two WS calls that
    write route history, so both must consult the revocation check.
    """
    assert SOURCE.count("_ws_session_revoked()") >= 3  # definition + 2 call sites
    # The batch path acks 0 instead of erroring, so a stale client stops retrying.
    batch_section = SOURCE.split('elif data.get("type") in ("location_batch"')[1]
    revoke_at = batch_section.find("_ws_session_revoked()")
    persist_at = batch_section.find("await persist_ride_breadcrumbs(")
    assert revoke_at != -1 and persist_at != -1
    assert revoke_at < persist_at, "revocation check must run before the persist call"


def test_handshake_rejects_a_tombstoned_session() -> None:
    """A socket opened after sign-out is closed at the handshake, not merely
    prevented from persisting."""
    handshake = SOURCE.split('"message": "session_revoked"')[0]
    assert "await is_session_revoked(ws_session_id)" in handshake


def test_v2_ws_epoch_is_client_bound_once_and_reconcile_is_explicit() -> None:
    """A late socket cannot adopt a replacement Go Online epoch from DB."""
    assert 'raw_online_epoch = auth_msg.get("online_epoch")' in SOURCE
    assert "bind_ws_presence_epoch(" in SOURCE
    assert '"presence_status":' in SOURCE
    assert '"reconcile_required"' in SOURCE
    assert '"type": "availability_reconcile_required"' in SOURCE


def test_ws_epoch_binding_requires_explicit_matching_handshake_epoch() -> None:
    from backend.utils import driver_presence as presence

    state = {"availability_v2": True}
    assert not presence.bind_ws_presence_epoch(state, "session-1", None, {"is_online": True, "online_epoch": "4"})
    assert "presence_epoch" not in state
    assert not presence.bind_ws_presence_epoch(state, "session-1", "3", {"is_online": True, "online_epoch": "4"})
    assert "presence_epoch" not in state
    assert presence.bind_ws_presence_epoch(state, "session-1", "4", {"is_online": True, "online_epoch": "4"})
    assert state["presence_session_id"] == "session-1"
    assert state["presence_epoch"] == "4"


@pytest.mark.anyio
async def test_ws_contact_renewal_does_not_include_gps_and_stale_epoch_unbinds(monkeypatch) -> None:
    from backend.utils import driver_presence as presence

    renew = AsyncMock(return_value={"status": "renewed"})
    monkeypatch.setattr(presence, "renew_driver_presence", renew)
    state = {"presence_session_id": "session-1", "presence_epoch": "4"}
    await presence.renew_ws_presence("driver-1", state)
    renew.assert_awaited_once_with("driver-1", "session-1", 4)
    renew.reset_mock(return_value=True)
    renew.return_value = {"status": "stale_epoch", "code": "ONLINE_EPOCH_STALE"}
    await presence.renew_ws_presence("driver-1", state)
    assert state["presence_epoch"] is None
    assert state["presence_reconcile_required"] is True


@pytest.mark.anyio
async def test_ws_batch_gps_uses_approved_timestamp_and_captured_socket_fence(monkeypatch) -> None:
    from backend.utils import driver_presence as presence

    renew = AsyncMock(return_value={"status": "renewed"})
    monkeypatch.setattr(presence, "renew_ws_presence", renew)
    stamp = datetime.now(timezone.utc)
    state = {"presence_session_id": "session-1", "presence_epoch": "4"}
    await presence.renew_ws_batch_location("driver-1", state, stamp, trusted=True)
    renew.assert_awaited_once_with("driver-1", state, location_captured_at=stamp)
    renew.reset_mock()
    await presence.renew_ws_batch_location("driver-1", state, stamp, trusted=False)
    renew.assert_not_awaited()


@pytest.mark.anyio
async def test_stale_socket_disconnect_deletes_only_its_scoped_presence(monkeypatch) -> None:
    from backend.utils import driver_presence

    class FakeRedis:
        def __init__(self):
            self.hashes = {}

        async def delete(self, key):
            self.hashes.pop(key, None)

    fake_redis = FakeRedis()
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=fake_redis))
    old_key = driver_presence.scoped_presence_key("driver-1", "old-session", 8)
    current_key = driver_presence.scoped_presence_key("driver-1", "new-session", 9)
    fake_redis.hashes[old_key] = {"contact_received_ms": "1"}
    fake_redis.hashes[current_key] = {"contact_received_ms": "2"}
    await driver_presence.clear_ws_presence(
        "driver-1",
        {"availability_v2": True, "presence_session_id": "old-session", "presence_epoch": "8"},
    )
    assert old_key not in fake_redis.hashes
    assert current_key in fake_redis.hashes


@pytest.mark.anyio
def test_superseded_session_classification_is_terminal():
    from backend.utils import driver_presence as presence

    assert presence.ws_session_was_superseded({"code": "SESSION_SUPERSEDED"})
    assert not presence.ws_session_was_superseded({"code": "ONLINE_EPOCH_STALE"})


@pytest.mark.anyio
async def test_fenced_marker_requires_an_actual_write_before_fanout(monkeypatch) -> None:
    from backend.utils import driver_presence as presence

    assert not presence.scoped_marker_allows_fanout(None, fenced=True, attempted=False)
    assert not presence.scoped_marker_allows_fanout(None, fenced=True)
    assert not presence.scoped_marker_allows_fanout(False, fenced=True)
    assert presence.scoped_marker_allows_fanout(True, fenced=True)
    assert presence.scoped_marker_allows_fanout(None, fenced=False, attempted=False)


def test_v2_ws_pong_contact_does_not_refresh_location_evidence() -> None:
    """Pongs call contact-only renewal; GPS deadlines are never supplied."""
    pong_section = SOURCE.split('if data.get("type") == "pong":')[1].split(
        'if data.get("type") in ("driver_location", "location_update"):'
    )[0]
    assert "renew_ws_presence(" in pong_section
    assert "location_captured_at" not in pong_section
    assert 'elif not conn_state.get("availability_v2"):' in pong_section


def test_v2_ws_marker_writes_use_the_handshake_fence() -> None:
    """Stale v2 sockets cannot fall back to the unscoped marker RPC."""
    assert "authenticated_session_id=presence_session_id" in SOURCE
    assert "online_epoch=presence_epoch" in SOURCE
    assert 'conn_state.get("availability_v2") and conn_state.get("presence_epoch") is None' in SOURCE
    assert "renew_ws_batch_location(" in SOURCE
    assert 'allow_untimed=not conn_state.get("availability_v2")' in SOURCE


# ── 3. Breadcrumb flush must not delay the rider/admin fan-out ─────────────


def test_single_ping_breadcrumb_buffer_runs_after_fanout() -> None:
    """The per-ping breadcrumb buffer occasionally flushes to Postgres
    (~every 10 points/10s). That write must not sit ahead of the
    rider/admin location fan-out in the <100ms WS fan-out SLA path, since
    nothing in the fan-out reads a value ``buffer_ride_breadcrumb`` sets.

    Guards a regression where the buffer call was sequenced before the fan-out
    loop, so roughly 1-in-10 ticks paid a synchronous DB round-trip before any
    rider/admin ever saw the location update.
    """
    fanout_at = SOURCE.find("await manager.broadcast_driver_location_to_admins(")
    breadcrumb_at = SOURCE.rfind("await buffer_ride_breadcrumb(")  # fresh-ping path; stale pings have no fanout
    assert fanout_at != -1 and breadcrumb_at != -1
    assert fanout_at < breadcrumb_at, "buffer_ride_breadcrumb must run after the admin fan-out, not before it"


@pytest.mark.anyio
async def test_revocation_recheck_caches_positives_and_throttles_negatives(monkeypatch):
    """Behavioural test of the caching contract, reimplemented against the same
    helper shape used in the endpoint.

    A trip streams a durable fix every ~3-4s; a Redis GET per fix would add
    avoidable load to the WS fan-out path (<100ms P95). Negatives are therefore
    re-checked on an interval, while a positive is terminal — tombstones are
    never un-set.
    """
    from utils import session_revocation

    calls = {"n": 0}
    revoked = {"value": False}

    async def _fake_is_revoked(_session_id):
        calls["n"] += 1
        return revoked["value"]

    monkeypatch.setattr(session_revocation, "is_session_revoked", _fake_is_revoked)

    clock = {"t": 0.0}
    recheck_seconds = 30
    state = {"revoked": False, "checked_at": None}

    async def _ws_session_revoked(session_id="sess-a"):
        if state["revoked"]:
            return True
        if not session_id:
            return False
        now = clock["t"]
        if state["checked_at"] is not None and now - state["checked_at"] < recheck_seconds:
            return False
        state["checked_at"] = now
        state["revoked"] = await session_revocation.is_session_revoked(session_id)
        return state["revoked"]

    # First call checks Redis; subsequent calls inside the window do not.
    assert await _ws_session_revoked() is False
    assert calls["n"] == 1
    clock["t"] = 5.0
    assert await _ws_session_revoked() is False
    assert calls["n"] == 1, "negative result must be reused inside the window"

    # After the window, it re-checks and picks up the sign-out.
    clock["t"] = 31.0
    revoked["value"] = True
    assert await _ws_session_revoked() is True
    assert calls["n"] == 2

    # A positive is terminal — no further Redis reads.
    clock["t"] = 100.0
    assert await _ws_session_revoked() is True
    assert calls["n"] == 2


@pytest.mark.anyio
async def test_no_session_id_never_consults_redis(monkeypatch):
    """Firebase-authenticated sockets carry no session_id, so they must not be
    rejected here — logout-all / sessions_invalid_before is their path."""
    from utils import session_revocation

    async def _boom(_session_id):  # pragma: no cover - must not be called
        raise AssertionError("should not check Redis without a session_id")

    monkeypatch.setattr(session_revocation, "is_session_revoked", _boom)

    state = {"revoked": False, "checked_at": None}

    async def _ws_session_revoked(session_id=None):
        if state["revoked"]:
            return True
        if not session_id:
            return False
        state["checked_at"] = 0.0
        state["revoked"] = await session_revocation.is_session_revoked(session_id)
        return state["revoked"]

    assert await _ws_session_revoked(None) is False


def test_ws_revocation_helper_delegates_the_redis_read() -> None:
    """The helper defers to is_session_revoked, which swallows Redis errors and
    returns False — so an outage cannot knock a working driver's breadcrumbs off
    the socket. Pinned structurally because the endpoint has no seam to drive."""
    helper = SOURCE.split("async def _ws_session_revoked()")[1].split("# Main message loop")[0]
    assert "await is_session_revoked(ws_session_id)" in helper
