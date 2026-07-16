"""Unit tests for POST /users/profile duplicate-email handling.

Uber-style single-identity model: one person = one account (both rider and
driver roles), keyed on a unique phone AND a unique email. If an email already
belongs to another account, the profile route must block it and point the user
to log into their existing account, while never disclosing the other account's
phone number.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

try:
    from routes.users import create_profile
    from schemas import CreateProfileRequest
except ImportError:
    from backend.routes.users import create_profile  # type: ignore[no-redef]
    from backend.schemas import CreateProfileRequest  # type: ignore[no-redef]


def _req(email: str = "Shared@Example.com", role: str | None = None) -> CreateProfileRequest:
    return CreateProfileRequest(
        first_name="Sam",
        last_name="Rider",
        email=email,
        gender="Male",
        role=role,
    )


@pytest.mark.anyio
async def test_duplicate_email_is_blocked_with_account_recovery_message():
    """Email already on another account → 400 pointing the user to log into it."""
    current_user = {"id": "u-new", "phone": "+13060000002"}
    other_account = {"id": "u-existing", "phone": "+13060000001", "email": "shared@example.com"}

    with (
        patch(
            "routes.users.db_supabase.get_rows",
            AsyncMock(return_value=[other_account]),
        ),
        patch("routes.users.db_supabase.update_one", AsyncMock()) as update_mock,
    ):
        with pytest.raises(HTTPException) as ei:
            await create_profile(request=_req(), current_user=current_user)

    assert ei.value.status_code == 400
    detail = ei.value.detail.lower()
    # Uber-style: point the user to their existing account, not a new one.
    assert "already linked to an existing spinr account" in detail
    assert "log in" in detail
    # Never disclose the other account's phone number (PII / enumeration).
    assert "+13060000001" not in ei.value.detail
    # Must fit a mobile toast whole (TOAST_MESSAGE_MAX in
    # shared/utils/toastMessage.ts) — no clamping mid-guidance.
    assert len(ei.value.detail) <= 140
    # The duplicate must not be written.
    update_mock.assert_not_called()


@pytest.mark.anyio
async def test_duplicate_email_lookup_is_case_insensitive():
    """The guard normalizes to lowercase before comparing, so casing can't bypass it."""
    captured: dict = {}

    async def _get_rows(table, filt, limit=None):
        captured["filter"] = filt
        return [{"id": "u-existing", "phone": "+1306", "email": "shared@example.com"}]

    with (
        patch("routes.users.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("routes.users.db_supabase.update_one", AsyncMock()),
    ):
        with pytest.raises(HTTPException):
            await create_profile(request=_req(email="SHARED@EXAMPLE.COM"), current_user={"id": "u-new", "phone": "+1"})

    assert captured["filter"]["email"] == "shared@example.com"


@pytest.mark.anyio
async def test_unique_email_completes_profile():
    """No collision → profile is written and email stored normalized (lowercased)."""
    captured: dict = {}
    user = {"id": "u-new", "phone": "+13060000002", "created_at": "2026-06-15T00:00:00Z"}

    async def _update(table, filt, fields):
        captured.update(fields)

    with (
        patch("routes.users.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("routes.users.db_supabase.update_one", AsyncMock(side_effect=_update)),
        patch(
            "routes.users.db_supabase.get_user_by_id",
            AsyncMock(side_effect=lambda _id: {**user, **captured}),
        ),
    ):
        out = await create_profile(request=_req(email="Fresh@Example.com"), current_user=user)

    assert captured["email"] == "fresh@example.com"
    assert captured["profile_complete"] is True
    assert out.email == "fresh@example.com"
