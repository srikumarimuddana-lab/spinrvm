"""Unit tests for PUT /profile-image — object-storage upload with base64 fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from routes.users import upload_profile_image
except ImportError:
    from backend.routes.users import upload_profile_image  # type: ignore[no-redef]


class _FakeUpload:
    def __init__(self, content_type: str, data: bytes):
        self.content_type = content_type
        self._data = data

    async def read(self):
        return self._data


def _storage_mock(public_url="https://cdn.supabase/profile-photos/u1/x.png"):
    sb = MagicMock()
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = public_url
    return sb


@pytest.mark.anyio
async def test_uploads_to_storage_and_stores_url():
    captured: dict = {}

    async def _update(table, filt, fields):
        captured.update(fields)

    user = {"id": "u1", "phone": "+13060000000", "role": "driver", "created_at": "2026-06-15T00:00:00Z"}
    with (
        patch("routes.users.db_supabase.supabase", _storage_mock()),
        patch("routes.users.db_supabase.update_one", AsyncMock(side_effect=_update)),
        patch("routes.users.db_supabase.get_user_by_id", AsyncMock(side_effect=lambda _id: {**user, **captured})),
    ):
        out = await upload_profile_image(file=_FakeUpload("image/png", b"\x89PNG..."), current_user=user)

    assert captured["profile_image"] == "https://cdn.supabase/profile-photos/u1/x.png"
    assert captured["profile_image_status"] == "pending_review"  # driver → review
    assert out.profile_image.startswith("https://")


@pytest.mark.anyio
async def test_falls_back_to_base64_when_storage_unavailable():
    captured: dict = {}
    user = {"id": "u2", "phone": "+13060000001", "role": "rider", "created_at": "2026-06-15T00:00:00Z"}
    with (
        patch("routes.users.db_supabase.supabase", None),  # no storage client
        patch("routes.users.db_supabase.update_one", AsyncMock(side_effect=lambda t, f, fields: captured.update(fields))),
        patch("routes.users.db_supabase.get_user_by_id", AsyncMock(side_effect=lambda _id: {**user, **captured})),
    ):
        await upload_profile_image(file=_FakeUpload("image/png", b"abc"), current_user=user)

    assert captured["profile_image"].startswith("data:image/png;base64,")
    assert captured["profile_image_status"] == "approved"  # rider → auto-approved


@pytest.mark.anyio
async def test_storage_error_falls_back_to_base64():
    captured: dict = {}
    sb = _storage_mock()
    sb.storage.from_.return_value.upload.side_effect = RuntimeError("bucket missing")
    user = {"id": "u3", "phone": "+13060000002", "role": "driver", "created_at": "2026-06-15T00:00:00Z"}
    with (
        patch("routes.users.db_supabase.supabase", sb),
        patch("routes.users.db_supabase.update_one", AsyncMock(side_effect=lambda t, f, fields: captured.update(fields))),
        patch("routes.users.db_supabase.get_user_by_id", AsyncMock(side_effect=lambda _id: {**user, **captured})),
    ):
        await upload_profile_image(file=_FakeUpload("image/png", b"abc"), current_user=user)

    assert captured["profile_image"].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_rejects_non_image():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await upload_profile_image(file=_FakeUpload("application/pdf", b"%PDF"), current_user={"id": "u", "phone": "+1"})
    assert ei.value.status_code == 400
