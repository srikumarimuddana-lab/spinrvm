"""Tests for routes/legacy_consent.py — the one-time consent-refresh notice
for legacy-imported and pre-consent-tracking users.

Calls the route functions directly (same pattern as
test_rides_payments_coverage.py) rather than through a full TestClient —
current_user is a plain Depends() default, easy to pass by hand.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.legacy_consent import CONSENT_VERSION, accept_consent_notice, get_consent_status

pytestmark = pytest.mark.anyio

USER_ID = "user-1"


def _settings(enabled: bool) -> dict:
    return {"legacy_consent_notice_enabled": enabled}


# ── GET /consent/status ─────────────────────────────────────────────────


async def test_status_flag_off_reports_no_notice_regardless_of_consent_state():
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(False))),
        patch("backend.routes.legacy_consent.db_supabase.get_user_by_id", AsyncMock(return_value=None)) as get_user,
    ):
        result = await get_consent_status(current_user={"id": USER_ID})

    assert result.needs_notice is False
    assert result.current_version == CONSENT_VERSION
    # dark: doesn't even look up the user when the flag is off
    get_user.assert_not_awaited()


async def test_status_flag_on_null_consent_version_needs_notice():
    """Covers both legacy-imported (never had a consent flow) and organic
    pre-migration-334 users — both honestly NULL, both need it."""
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch(
            "backend.routes.legacy_consent.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": USER_ID, "consent_version": None}),
        ),
    ):
        result = await get_consent_status(current_user={"id": USER_ID})

    assert result.needs_notice is True


async def test_status_flag_on_current_version_already_accepted():
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch(
            "backend.routes.legacy_consent.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": USER_ID, "consent_version": CONSENT_VERSION}),
        ),
    ):
        result = await get_consent_status(current_user={"id": USER_ID})

    assert result.needs_notice is False


async def test_status_flag_on_stale_version_needs_notice():
    """If CONSENT_VERSION bumps later, an already-notified user whose stamped
    version falls behind it needs the notice again — the mechanism must be
    reusable for a future material policy change, not a one-shot import flag."""
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch(
            "backend.routes.legacy_consent.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": USER_ID, "consent_version": "some-older-version"}),
        ),
    ):
        result = await get_consent_status(current_user={"id": USER_ID})

    assert result.needs_notice is True


async def test_status_user_not_found_raises_404():
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch("backend.routes.legacy_consent.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_consent_status(current_user={"id": USER_ID})

    assert exc.value.status_code == 404


# ── POST /consent/accept ────────────────────────────────────────────────


async def test_accept_flag_off_returns_404_no_write():
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(False))),
        patch("backend.routes.legacy_consent.db_supabase.update_one", AsyncMock()) as upd,
    ):
        with pytest.raises(HTTPException) as exc:
            await accept_consent_notice(current_user={"id": USER_ID})

    assert exc.value.status_code == 404
    upd.assert_not_awaited()


async def test_accept_flag_on_stamps_current_consent_version():
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch("backend.routes.legacy_consent.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
    ):
        result = await accept_consent_notice(current_user={"id": USER_ID})

    assert result.success is True
    assert result.consent_version == CONSENT_VERSION
    upd.assert_awaited_once()
    table, filters, fields = upd.await_args.args
    assert table == "users"
    assert filters == {"id": USER_ID}
    assert fields["consent_version"] == CONSENT_VERSION
    assert "consent_accepted_at" in fields


async def test_accept_is_idempotent_safe_to_call_twice():
    """Re-accepting (e.g. a retried request) just re-stamps the same
    version/timestamp — no guard needed since the caller's own action is
    always the freshest signal, unlike the SIN/DOB backfill's stale-batch
    never-clobber requirement."""
    with (
        patch("backend.routes.legacy_consent.get_app_settings", AsyncMock(return_value=_settings(True))),
        patch("backend.routes.legacy_consent.db_supabase.update_one", AsyncMock(return_value=None)) as upd,
    ):
        first = await accept_consent_notice(current_user={"id": USER_ID})
        second = await accept_consent_notice(current_user={"id": USER_ID})

    assert first.consent_version == second.consent_version == CONSENT_VERSION
    assert upd.await_count == 2
