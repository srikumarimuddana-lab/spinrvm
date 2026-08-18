"""Size and read-failure handling for `documents.save_upload`.

save_upload backs POST /drivers/documents/upload (driver app) and
POST /api/admin/documents/upload (admin manual upload). Unlike
POST /api/v1/upload it has no Request object to pre-check content-length
against, and the app installs no global body-size middleware — so until
read_upload_capped was introduced these two routes buffered an unbounded
request body into worker memory.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

try:
    from documents import MAX_FILE_SIZE, save_upload
except ImportError:  # pragma: no cover - dual import pattern
    from backend.documents import MAX_FILE_SIZE, save_upload  # type: ignore[no-redef]

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class _FakeUpload:
    """Mirrors starlette's UploadFile.read(size) cursor semantics."""

    def __init__(self, filename: str | None, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
        else:
            chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class _ExplodingUpload:
    filename = "a.jpg"
    content_type = "image/jpeg"

    async def read(self, size: int = -1):
        raise OSError("connection reset mid-upload")


def _storage():
    sb = MagicMock()
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://signed/doc"}
    return sb


@pytest.mark.anyio
async def test_oversize_upload_is_refused_with_413():
    oversize = JPEG + b"\x00" * (MAX_FILE_SIZE + 1)
    with patch("documents.supabase", _storage()), pytest.raises(HTTPException) as exc:
        await save_upload(_FakeUpload("a.jpg", "image/jpeg", oversize))
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_oversize_upload_never_reaches_storage():
    """The cap must bail before the object is written, not after."""
    sb = _storage()
    oversize = JPEG + b"\x00" * (MAX_FILE_SIZE + 1)
    with patch("documents.supabase", sb), pytest.raises(HTTPException):
        await save_upload(_FakeUpload("a.jpg", "image/jpeg", oversize))
    sb.storage.from_.return_value.upload.assert_not_called()


@pytest.mark.anyio
async def test_file_at_the_cap_is_accepted():
    """Boundary: exactly MAX_FILE_SIZE must pass, only past it fails."""
    sb = _storage()
    body = JPEG + b"\x00" * (MAX_FILE_SIZE - len(JPEG))
    assert len(body) == MAX_FILE_SIZE
    with patch("documents.supabase", sb):
        url = await save_upload(_FakeUpload("a.jpg", "image/jpeg", body))
    assert url == "https://signed/doc"
    assert sb.storage.from_.return_value.upload.call_args.kwargs["file"] == body


@pytest.mark.anyio
async def test_empty_upload_is_refused():
    with patch("documents.supabase", _storage()), pytest.raises(HTTPException) as exc:
        await save_upload(_FakeUpload("a.jpg", "image/jpeg", b""))
    assert exc.value.status_code == 400
    assert exc.value.detail == "Empty file"


@pytest.mark.anyio
async def test_unsupported_format_surfaces_as_400_not_500():
    """Regression: the resolver's 400 used to be caught by save_upload's
    `except Exception` and re-raised as 500 'Could not save file'."""
    with patch("documents.supabase", _storage()), pytest.raises(HTTPException) as exc:
        await save_upload(_FakeUpload("a.bin", "application/octet-stream", b"\x00" * 64))
    assert exc.value.status_code == 400
    assert "Unsupported file format" in exc.value.detail


@pytest.mark.anyio
async def test_read_failure_is_reported_not_swallowed():
    """A read that dies mid-body must not be reported as a storage failure."""
    with patch("documents.supabase", _storage()), pytest.raises(HTTPException) as exc:
        await save_upload(_ExplodingUpload())
    assert exc.value.status_code == 400
    assert "Could not read" in exc.value.detail


@pytest.mark.anyio
async def test_storage_type_is_the_sniffed_type_not_the_declared_one():
    sb = _storage()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    with patch("documents.supabase", sb):
        await save_upload(_FakeUpload("IMG.PNG", "image/jpeg", png))
    assert sb.storage.from_.return_value.upload.call_args.kwargs["file_options"] == {"content-type": "image/png"}
    assert sb.storage.from_.return_value.upload.call_args.kwargs["path"].endswith(".png")
