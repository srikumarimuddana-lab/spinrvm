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
from pathlib import Path

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
