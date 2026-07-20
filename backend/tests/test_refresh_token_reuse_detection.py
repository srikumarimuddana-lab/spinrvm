"""B-P1-3 — refresh-token reuse detection (replay-attack escalation).

Pins the cascade behaviour in utils/refresh_tokens.py:
_handle_refresh_token_reuse. A revoked refresh token presented to the
backend is treated as theft per OAuth2 BCP §4.14.2, and we:

    1. Bump token_version on users (rider/driver) or admin_staff (admin)
       to invalidate every in-flight access token on next request.
    2. Revoke every active refresh token for the user.
    3. Write an audit_logs row tagged 'refresh_token_reuse_detected'.
    4. Return None to the auth route either way (no client-side oracle).

The cascade is best-effort: a failure in any step must NOT crash the
auth path, must logger.error, and must not leak information to the
client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Import the module up-front so patch("utils.refresh_tokens.X") can
# resolve attribute paths during test collection (otherwise the
# namespace-package submodule is not yet bound to `utils`).
import utils.refresh_tokens  # noqa: F401


def _revoked_row(audience: str = "rider", user_id: str = "user-rider-1") -> dict:
    return {
        "id": "rtk-revoked-001",
        "user_id": user_id,
        "audience": audience,
        "token_hash": "doesnt-matter-tests-patch-find_one",
        "revoked_at": "2026-04-01T00:00:00+00:00",
        "replaced_by": "rtk-newer-002",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "user_agent": "TestClient/1.0",
        "ip": "10.0.0.1",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Happy path — non-revoked token does NOT trigger the cascade
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_token_does_not_trigger_cascade():
    valid_row = {
        "id": "rtk-valid-001",
        "user_id": "user-rider-1",
        "audience": "rider",
        "token_hash": "hash",
        "revoked_at": None,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    cascade_mock = AsyncMock()

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=valid_row)),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("any-raw")

    assert result == valid_row
    cascade_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Revoked-token replay → cascade fires; lookup returns None either way
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoked_token_replay_triggers_cascade_and_returns_none():
    cascade_mock = AsyncMock()

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=_revoked_row())),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("replayed-raw")

    assert result is None, "lookup must return None on replay (no oracle to client)"
    cascade_mock.assert_called_once()
    # The cascade receives the full revoked row so it can derive user_id + audience.
    passed_row = cascade_mock.call_args.args[0]
    assert passed_row["id"] == "rtk-revoked-001"
    assert passed_row["audience"] == "rider"


# ─────────────────────────────────────────────────────────────────────────────
# Rotation-race grace window — benign retries must NOT cascade (incident fix)
# ─────────────────────────────────────────────────────────────────────────────


def _recently_revoked_rotated_row(**over) -> dict:
    """A token revoked by NORMAL rotation moments ago (replaced_by set, fresh
    revoked_at) — i.e. a client retry / concurrent-refresh race."""
    from datetime import datetime, timezone

    row = _revoked_row()
    row["revoked_at"] = datetime.now(timezone.utc).isoformat()
    row["replaced_by"] = "rtk-newer-002"
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_benign_rotation_replay_within_grace_does_not_cascade():
    """A just-rotated token replayed within the grace window (lost rotation
    response / two near-simultaneous refreshes) must return a clean 401 WITHOUT
    the cascade. This is the regression for the mass-logout + perpetual-WS-
    reconnect incident."""
    cascade_mock = AsyncMock()

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=_recently_revoked_rotated_row())),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("rotated-raw")

    assert result is None, "benign replay still returns None (no oracle), just no cascade"
    cascade_mock.assert_not_called()


@pytest.mark.asyncio
async def test_rotation_replay_minutes_later_within_window_does_not_cascade():
    """Mobile reality: a phone loses the rotation response in a dead zone and
    only retries when foregrounded a few minutes later, still holding the
    pre-rotation token. Within REFRESH_REUSE_GRACE_SECONDS this is benign — a
    clean 401, no all-device cascade."""
    from datetime import datetime, timedelta, timezone

    from utils.refresh_tokens import REFRESH_REUSE_GRACE_SECONDS

    # Half a window ago — comfortably inside the grace period.
    revoked_at = datetime.now(timezone.utc) - timedelta(
        seconds=REFRESH_REUSE_GRACE_SECONDS / 2
    )
    row = _recently_revoked_rotated_row(revoked_at=revoked_at.isoformat())
    cascade_mock = AsyncMock()

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("rotated-raw-mins-ago")

    assert result is None
    cascade_mock.assert_not_called()


@pytest.mark.asyncio
async def test_rotation_replay_past_grace_window_still_cascades():
    """A rotated token replayed well AFTER the grace window is back to being a
    theft signal — the legitimate client would have stepped forward long ago.
    This pins the upper edge so the window can't silently grow unbounded."""
    from datetime import datetime, timedelta, timezone

    from utils.refresh_tokens import REFRESH_REUSE_GRACE_SECONDS

    revoked_at = datetime.now(timezone.utc) - timedelta(
        seconds=REFRESH_REUSE_GRACE_SECONDS + 120
    )
    row = _recently_revoked_rotated_row(revoked_at=revoked_at.isoformat())
    cascade_mock = AsyncMock()

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("rotated-raw-too-late")

    assert result is None
    cascade_mock.assert_called_once()


@pytest.mark.asyncio
async def test_recent_revocation_without_rotation_still_cascades():
    """A token revoked WITHOUT a replacement (explicit logout / a prior
    cascade), even moments ago, is a real signal — replaying it MUST still
    cascade. The grace applies only to rotated-forward tokens."""
    cascade_mock = AsyncMock()
    row = _recently_revoked_rotated_row(replaced_by=None)

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=row)),
        patch("utils.refresh_tokens._handle_refresh_token_reuse", cascade_mock),
    ):
        from utils.refresh_tokens import lookup_refresh_token

        result = await lookup_refresh_token("logged-out-raw")

    assert result is None
    cascade_mock.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Cascade for rider audience bumps users.token_version
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_bumps_users_token_version_for_rider():
    user_row = {"id": "user-rider-1", "token_version": 4}
    update_calls = []
    insert_calls = []

    async def _find_one(table, query):
        if table == "users" and query.get("id") == "user-rider-1":
            return user_row
        return None

    async def _update_one(table, query, payload):
        update_calls.append((table, query, payload))
        return True

    async def _insert_one(table, payload):
        insert_calls.append((table, payload))
        return {"id": payload.get("id")}

    async def _revoke_all(_uid):
        return 7

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(side_effect=_find_one)),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_update_one)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(side_effect=_revoke_all)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="rider", user_id="user-rider-1"))

    # users.token_version went from 4 → 5, and the Firebase revocation
    # watermark is stamped alongside it so the cascade also kills Firebase
    # sessions (which carry no token_version claim).
    bump_calls = [c for c in update_calls if c[0] == "users"]
    assert bump_calls, "users.token_version was not bumped"
    bump_set = bump_calls[0][2]["$set"]
    assert bump_set["token_version"] == 5
    assert isinstance(bump_set["sessions_invalid_before"], str) and bump_set["sessions_invalid_before"]


@pytest.mark.asyncio
async def test_cascade_bumps_admin_staff_for_admin_audience():
    staff_row = {"id": "staff-001", "token_version": 0}
    update_calls = []

    async def _find_one(table, query):
        if table == "admin_staff" and query.get("id") == "staff-001":
            return staff_row
        return None

    async def _update_one(table, query, payload):
        update_calls.append((table, query, payload))
        return True

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(side_effect=_find_one)),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_update_one)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(return_value={"id": "audit-1"})),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=2)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="admin", user_id="staff-001"))

    bump_calls = [c for c in update_calls if c[0] == "admin_staff"]
    assert bump_calls, "admin_staff.token_version was not bumped"
    assert bump_calls[0][2] == {"$set": {"token_version": 1}}


@pytest.mark.asyncio
async def test_cascade_skips_token_version_for_admin_001_super_admin():
    """admin-001's creds live in env vars — there is no admin_staff row to
    bump. The cascade must skip the bump (not raise a DB miss) but still
    revoke refresh tokens and write the audit row."""
    update_calls = []
    insert_calls = []

    async def _update_one(table, query, payload):
        update_calls.append((table, query, payload))
        return True

    async def _insert_one(table, payload):
        insert_calls.append((table, payload))
        return {"id": payload.get("id")}

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value=None)),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_update_one)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=1)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="admin", user_id="admin-001"))

    # No bump on admin_staff or users
    assert not [c for c in update_calls if c[0] in ("admin_staff", "users")]
    # Audit log still written
    assert insert_calls and insert_calls[0][0] == "audit_logs"


# ─────────────────────────────────────────────────────────────────────────────
# Cascade revokes every refresh token for the user
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_calls_revoke_all_for_user():
    revoke_all_mock = AsyncMock(return_value=3)

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(return_value={"id": "audit-1"})),
        patch("utils.refresh_tokens.revoke_all_for_user", revoke_all_mock),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(user_id="user-rider-1"))

    revoke_all_mock.assert_called_once_with("user-rider-1")


@pytest.mark.asyncio
async def test_cascade_kicks_ws_sockets_for_rider_audience():
    """B-P1-11: the cascade must close any live WebSocket sockets too —
    otherwise an attacker holding the access token paired with the
    replayed refresh token keeps their socket open until the heartbeat
    catches up, which is up to 30s of free dispatch events.

    Scope: rider+driver for users-table audiences; admin for admin-staff.
    Pinned here because the cascade is split across many best-effort
    steps and a future refactor could quietly drop the kick."""
    kick_mock = AsyncMock(return_value=0)

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(return_value={"id": "audit-1"})),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=0)),
        patch("socket_manager.manager.kick_user", kick_mock),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="rider", user_id="u1"))

    kick_mock.assert_awaited_once_with(
        "u1",
        client_types=["rider", "driver"],
        reason="refresh_token_reuse",
    )


@pytest.mark.asyncio
async def test_cascade_kicks_admin_ws_sockets_for_admin_audience():
    """Admin-audience reuse → admin sockets only; never rider/driver."""
    kick_mock = AsyncMock(return_value=0)

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "staff-1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(return_value={"id": "audit-2"})),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=0)),
        patch("socket_manager.manager.kick_user", kick_mock),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="admin", user_id="staff-1"))

    kick_mock.assert_awaited_once_with(
        "staff-1",
        client_types=["admin"],
        reason="refresh_token_reuse",
    )


@pytest.mark.asyncio
async def test_cascade_continues_to_audit_when_ws_kick_fails():
    """A WS kick failure must NOT skip the audit_logs insert — security
    ops needs the row even when the real-time disconnect didn't land."""
    insert_calls = []

    async def _insert_one(table, payload):
        insert_calls.append((table, payload))
        return {"id": payload.get("id")}

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=0)),
        patch(
            "socket_manager.manager.kick_user",
            AsyncMock(side_effect=RuntimeError("redis exploded")),
        ),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(audience="rider", user_id="u1"))

    audit_calls = [c for c in insert_calls if c[0] == "audit_logs"]
    assert len(audit_calls) == 1, "audit_logs row must still be written after kick failure"


# ─────────────────────────────────────────────────────────────────────────────
# Audit log row shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_writes_audit_log_with_production_schema():
    """The audit_logs row must match the production schema (migration 06):
    id TEXT PK / action / entity_type / entity_id / user_email / details TEXT."""
    insert_calls = []

    async def _insert_one(table, payload):
        insert_calls.append((table, payload))
        return {"id": payload.get("id")}

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_insert_one)),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=2)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row(user_id="user-rider-1"))

    audit_calls = [c for c in insert_calls if c[0] == "audit_logs"]
    assert len(audit_calls) == 1
    payload = audit_calls[0][1]
    assert payload["action"] == "refresh_token_reuse_detected"
    assert payload["entity_type"] == "user"
    assert payload["entity_id"] == "user-rider-1"
    assert payload["actor_id"] == "system:refresh_reuse_detector"
    # details is TEXT (production schema, NOT JSONB) — a JSON string.
    import json as _json

    detail_obj = _json.loads(payload["details"])
    assert detail_obj["replayed_row_id"] == "rtk-revoked-001"
    assert detail_obj["audience"] == "rider"
    assert detail_obj["cascade_token_version"] == 1
    assert detail_obj["cascade_refresh_revoked"] == 2
    # id must be present + look like a UUID (the PK has no DB-side default).
    assert payload["id"] and len(payload["id"]) >= 32


# ─────────────────────────────────────────────────────────────────────────────
# Best-effort guarantee: no step is allowed to crash the cascade
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_swallows_token_version_bump_failure():
    """If the users.token_version bump raises, the rest of the cascade must
    still execute (revoke + audit log) and the function must return cleanly."""

    async def _failing_update(*_, **__):
        raise RuntimeError("DB connection refused")

    revoke_mock = AsyncMock(return_value=5)
    insert_mock = AsyncMock(return_value={"id": "audit-1"})

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(side_effect=_failing_update)),
        patch("utils.refresh_tokens.db.insert_one", insert_mock),
        patch("utils.refresh_tokens.revoke_all_for_user", revoke_mock),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        # MUST NOT raise.
        await _handle_refresh_token_reuse(_revoked_row())

    revoke_mock.assert_called_once()
    insert_mock.assert_called_once()


@pytest.mark.asyncio
async def test_cascade_swallows_audit_insert_failure():
    """An audit_logs insert failure (e.g. RLS misconfiguration) must not
    crash the auth path. Token revocation has already happened; the
    forensic record loss is logged but not user-visible."""

    async def _failing_insert(*_, **__):
        raise RuntimeError("audit_logs insert failed")

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", AsyncMock(side_effect=_failing_insert)),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(return_value=1)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row())  # MUST NOT raise


@pytest.mark.asyncio
async def test_cascade_swallows_revoke_all_failure():
    async def _failing_revoke(_uid):
        raise RuntimeError("revoke scan failed")

    insert_mock = AsyncMock(return_value={"id": "audit-1"})

    with (
        patch("utils.refresh_tokens.db.find_one", AsyncMock(return_value={"id": "u1", "token_version": 0})),
        patch("utils.refresh_tokens.db.update_one", AsyncMock(return_value=True)),
        patch("utils.refresh_tokens.db.insert_one", insert_mock),
        patch("utils.refresh_tokens.revoke_all_for_user", AsyncMock(side_effect=_failing_revoke)),
    ):
        from utils.refresh_tokens import _handle_refresh_token_reuse

        await _handle_refresh_token_reuse(_revoked_row())  # MUST NOT raise

    # Audit log still attempted even when revoke cascade failed.
    insert_mock.assert_called_once()
