"""Upload format handling for driver-signup document uploads.

Regression cover for the "upload failed / unsupported format" reports from
live driver signup. The pickers the driver app uses do not report a file's
real type reliably:

* ``expo-image-picker``'s ``asset.type`` is the media *category*
  ('image' | 'video'), so the app declared ``image/jpeg`` for every asset —
  a gallery PNG/GIF then failed the backend's declared-vs-actual check.
* An iPhone gallery asset keeps its ``IMG_0001.HEIC`` filename even after
  Expo has re-encoded the bytes to JPEG, so an extension allowlist rejected
  a perfectly good JPEG.
* Android's document picker frequently supplies no extension at all, or
  ``application/octet-stream`` as the type.

The endpoint therefore derives both the stored MIME type and the stored
extension from the file's own bytes. These tests pin that contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

try:
    from documents import (
        _MIME_ALIASES,
        _MIME_TO_EXTENSION,
        _UNRENDERABLE_MIME,
        ALLOWED_MIME_TYPES,
        _resolve_upload_type,
        _sniff_mime_type,
        upload_file,
    )
except ImportError:  # pragma: no cover - dual import pattern
    from backend.documents import (  # type: ignore[no-redef]
        _resolve_upload_type,
        _sniff_mime_type,
        upload_file,
    )

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 32
PDF = b"%PDF-1.4\n" + b"\x00" * 32
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
HEIF = b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 32
AVIF = b"\x00\x00\x00\x1cftypavif" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32
WAV = b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 32


class _FakeUpload:
    """Mirrors starlette's UploadFile.read(size) cursor semantics, so the
    chunked read in read_upload_capped is exercised rather than bypassed."""

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


def _request(content_length: str | None = None):
    req = MagicMock()
    req.headers = {"content-length": content_length} if content_length else {}
    return req


class TestSniffMimeType:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (JPEG, "image/jpeg"),
            (PNG, "image/png"),
            (GIF, "image/gif"),
            (WEBP, "image/webp"),
            (PDF, "application/pdf"),
            (HEIC, _UNRENDERABLE_MIME),
            (HEIF, _UNRENDERABLE_MIME),
            (AVIF, _UNRENDERABLE_MIME),
        ],
    )
    def test_identifies_supported_headers(self, content, expected):
        assert _sniff_mime_type(content) == expected

    def test_riff_without_webp_marker_is_not_webp(self):
        """A WAV/AVI shares WebP's RIFF container; only the WEBP marker counts."""
        assert _sniff_mime_type(WAV) is None

    def test_mp4_brand_is_not_treated_as_an_image(self):
        """isom is an ISO-BMFF brand too — only the image brands may match."""
        assert _sniff_mime_type(MP4) is None

    def test_unknown_header_returns_none(self):
        assert _sniff_mime_type(b"NOTAKNOWNFORMAT" + b"\x00" * 32) is None

    def test_unrenderable_sentinel_is_never_storable(self):
        """The sentinel must stay out of the extension map, or a caller that
        skips the rejection branch would store a HEIC as a real type."""
        assert _UNRENDERABLE_MIME not in _MIME_TO_EXTENSION
        assert _UNRENDERABLE_MIME not in ALLOWED_MIME_TYPES


class TestAllowlistsAgree:
    """ALLOWED_MIME_TYPES gates _validate_file_type (admin illustration upload,
    bundle replay); _MIME_TO_EXTENSION gates the driver upload endpoints. A type
    added to one and not the other is accepted on one path and refused on the
    other, with nothing to surface the divergence."""

    def test_every_allowed_type_has_a_canonical_extension(self):
        assert set(_MIME_TO_EXTENSION) | set(_MIME_ALIASES) == ALLOWED_MIME_TYPES

    def test_aliases_resolve_to_a_storable_type(self):
        for alias, target in _MIME_ALIASES.items():
            assert alias not in _MIME_TO_EXTENSION, f"{alias} is an alias, not a storable type"
            assert target in _MIME_TO_EXTENSION


class TestResolveUploadType:
    @pytest.mark.parametrize(
        ("label", "content", "declared", "expected"),
        [
            # The picker declares image/jpeg for everything it returns.
            ("gallery PNG mislabelled jpeg", PNG, "image/jpeg", ("image/png", ".png")),
            ("gallery GIF mislabelled jpeg", GIF, "image/jpeg", ("image/gif", ".gif")),
            ("gallery WEBP mislabelled jpeg", WEBP, "image/jpeg", ("image/webp", ".webp")),
            # Android document picker: no usable type at all.
            ("PDF as octet-stream", PDF, "application/octet-stream", ("application/pdf", ".pdf")),
            ("JPEG as octet-stream", JPEG, "application/octet-stream", ("image/jpeg", ".jpg")),
            # File picker returning a PDF with the app's old image/jpeg default.
            ("PDF mislabelled jpeg", PDF, "image/jpeg", ("application/pdf", ".pdf")),
            # Non-standard alias some Android providers send.
            ("image/jpg alias", JPEG, "image/jpg", ("image/jpeg", ".jpg")),
            ("camera JPEG", JPEG, "image/jpeg", ("image/jpeg", ".jpg")),
            # expo-image-picker's media category leaking through as a "type".
            ("picker category 'image'", PNG, "image", ("image/png", ".png")),
        ],
    )
    def test_bytes_win_over_declared_type(self, label, content, declared, expected):
        assert _resolve_upload_type(content, declared) == expected, label

    def test_unrecognised_header_falls_back_to_declared_type(self):
        """Preserves the old behaviour for image formats we have no signature
        for — an allowed declared type is still honoured."""
        assert _resolve_upload_type(b"\x00" * 40, "image/jpeg") == ("image/jpeg", ".jpg")

    def test_unrecognised_header_and_unusable_type_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _resolve_upload_type(b"\x00" * 40, "application/octet-stream")
        assert exc.value.status_code == 400
        assert "Unsupported file format" in exc.value.detail

    def test_executable_declared_as_image_is_rejected(self):
        """A declared type outside the allowlist is still refused, even though
        the bytes are unrecognised rather than positively identified."""
        with pytest.raises(HTTPException) as exc:
            _resolve_upload_type(b"MZ\x90\x00" + b"\x00" * 32, "application/x-msdownload")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        ("content", "expected_name"),
        [(HEIC, "HEIC"), (HEIF, "HEIF"), (AVIF, "AVIF")],
    )
    def test_unrenderable_formats_rejected_with_actionable_message(self, content, expected_name):
        with pytest.raises(HTTPException) as exc:
            _resolve_upload_type(content, "image/jpeg")
        assert exc.value.status_code == 400
        assert expected_name in exc.value.detail
        # The driver has to be told what to do about it, not just "no".
        assert "Most" in exc.value.detail and "Compatible" in exc.value.detail

    @pytest.mark.parametrize(
        ("declared", "expected_name"),
        [("image/heic", "HEIC"), ("image/heif", "HEIF"), ("image/avif", "AVIF")],
    )
    def test_truncated_unrenderable_file_still_gets_the_actionable_message(self, declared, expected_name):
        """Too short to sniff (a partial upload, or a header under 12 bytes) —
        the declared type is the only signal left, and it should still produce
        guidance rather than the generic 'unsupported format'."""
        with pytest.raises(HTTPException) as exc:
            _resolve_upload_type(b"\x00\x00\x00", declared)
        assert exc.value.status_code == 400
        assert expected_name in exc.value.detail
        assert "Most" in exc.value.detail and "Compatible" in exc.value.detail

    def test_avif_is_not_silently_stored_as_jpeg(self):
        """Regression: with no AVIF signature, an AVIF declared image/jpeg fell
        through the fallback and was stored under a content-type it wasn't."""
        with pytest.raises(HTTPException):
            _resolve_upload_type(AVIF, "image/jpeg")


class TestUploadEndpoint:
    """End-to-end through the handler, with Supabase Storage mocked."""

    @staticmethod
    def _storage():
        sb = MagicMock()
        sb.storage.from_.return_value.upload.return_value = None
        sb.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://signed/doc"}
        return sb

    async def _upload(self, sb, filename, content_type, data):
        with patch("documents.supabase", sb):
            return await upload_file(
                request=_request(),
                file=_FakeUpload(filename, content_type, data),
                current_user={"id": "u-1"},
            )

    @pytest.mark.anyio
    async def test_gallery_png_declared_as_jpeg_is_accepted(self):
        """The single most common live failure: every gallery image was
        declared image/jpeg, so a PNG screenshot 400'd."""
        sb = self._storage()
        out = await self._upload(sb, "IMG_0001.PNG", "image/jpeg", PNG)

        assert out["success"] is True
        assert out["content_type"] == "image/png"
        assert out["filename"].endswith(".png")
        # Stored under the sniffed type, not the client's claim.
        assert sb.storage.from_.return_value.upload.call_args.kwargs["file_options"] == {"content-type": "image/png"}
        assert sb.storage.from_.return_value.upload.call_args.kwargs["path"].endswith(".png")

    @pytest.mark.anyio
    async def test_ios_heic_filename_with_jpeg_bytes_is_accepted(self):
        """Expo re-encodes gallery assets to JPEG but keeps the .HEIC
        filename; the old extension allowlist rejected the real JPEG."""
        out = await self._upload(self._storage(), "IMG_0002.HEIC", "image/jpeg", JPEG)
        assert out["content_type"] == "image/jpeg"
        assert out["filename"].endswith(".jpg")

    @pytest.mark.anyio
    async def test_file_with_no_extension_is_accepted(self):
        """Android SAF display names often carry no extension at all."""
        out = await self._upload(self._storage(), "Document", "application/octet-stream", PDF)
        assert out["content_type"] == "application/pdf"
        assert out["filename"].endswith(".pdf")

    @pytest.mark.anyio
    async def test_pdf_declared_as_jpeg_is_accepted(self):
        out = await self._upload(self._storage(), "licence.pdf", "image/jpeg", PDF)
        assert out["content_type"] == "application/pdf"

    @pytest.mark.anyio
    async def test_real_heif_is_rejected_as_400_not_500(self):
        with pytest.raises(HTTPException) as exc:
            await self._upload(self._storage(), "IMG_0003.heic", "image/heic", HEIC)
        assert exc.value.status_code == 400
        assert "HEIC" in exc.value.detail

    @pytest.mark.anyio
    async def test_empty_file_still_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await self._upload(self._storage(), "a.jpg", "image/jpeg", b"")
        assert exc.value.status_code == 400
        assert exc.value.detail == "Empty file"

    @pytest.mark.anyio
    async def test_oversize_file_still_rejected(self):
        sb = self._storage()
        with patch("documents.supabase", sb), pytest.raises(HTTPException) as exc:
            await upload_file(
                request=_request(content_length=str(11 * 1024 * 1024)),
                file=_FakeUpload("a.jpg", "image/jpeg", JPEG),
                current_user={"id": "u-1"},
            )
        assert exc.value.status_code == 413

    @pytest.mark.anyio
    async def test_oversize_body_rejected_even_when_content_length_lies(self):
        """The content-length header is a fast path, not the enforcement — a
        client can omit it or understate it. The read itself must cap."""
        oversize = JPEG + b"\x00" * (11 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            await self._upload(self._storage(), "a.jpg", "image/jpeg", oversize)
        assert exc.value.status_code == 413

    @pytest.mark.anyio
    async def test_multi_chunk_file_under_the_cap_is_reassembled_intact(self):
        """Spans several 1 MB read chunks; guards against a chunking bug that
        would truncate or reorder the body."""
        sb = self._storage()
        body = JPEG + b"\x5a" * (3 * 1024 * 1024)
        out = await self._upload(sb, "a.jpg", "image/jpeg", body)

        assert out["size"] == len(body)
        assert sb.storage.from_.return_value.upload.call_args.kwargs["file"] == body

    @pytest.mark.anyio
    async def test_original_filename_is_never_echoed_back(self):
        """Driver filenames carry licence numbers / names (12-8)."""
        out = await self._upload(self._storage(), "NIGHIL_KUMAR_LICENCE_123.png", "image/jpeg", PNG)
        assert "NIGHIL" not in out["filename"]
        assert out["filename"].startswith("document_")
