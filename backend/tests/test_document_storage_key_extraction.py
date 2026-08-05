"""_extract_storage_key must handle every document_url shape this codebase
writes, or the document silently exports as metadata with no file.

Regression cover for the "File is ticked but the ZIP still has no image"
report: a key that doesn't parse produces _content=None, which before the
status column landed was indistinguishable from a deliberate opt-out.
"""

import pytest

from backend.documents import _extract_storage_key


@pytest.mark.parametrize(
    "url,expected",
    [
        # Signed URL, absolute (documents.save_upload / regenerate_signed_url)
        (
            "https://abc.supabase.co/storage/v1/object/sign/driver-documents/7f3a.jpg?token=xyz",
            "7f3a.jpg",
        ),
        # Signed URL, host-relative (some supabase-py versions return this)
        ("/storage/v1/object/sign/driver-documents/7f3a.jpg?token=xyz", "7f3a.jpg"),
        # Public URL
        ("https://abc.supabase.co/storage/v1/object/public/driver-documents/7f3a.jpg", "7f3a.jpg"),
        # Authenticated (RLS-scoped) URL — was previously unhandled
        ("https://abc.supabase.co/storage/v1/object/authenticated/driver-documents/7f3a.jpg", "7f3a.jpg"),
        # Nested key from the bulk import
        (
            "https://abc.supabase.co/storage/v1/object/sign/driver-documents/"
            "saskatoon-import/b1/d7/criminal_record_check/main-uuid.pdf?token=t",
            "saskatoon-import/b1/d7/criminal_record_check/main-uuid.pdf",
        ),
        # driver_import_service's custom scheme — was previously unhandled
        (
            "storage://driver-documents/saskatoon-import/b1/d7/criminal_record_check/main-uuid.pdf",
            "saskatoon-import/b1/d7/criminal_record_check/main-uuid.pdf",
        ),
        # Bare key with no scheme
        ("7f3a.jpg", "7f3a.jpg"),
    ],
)
def test_extracts_key_from_every_written_url_shape(url, expected):
    assert _extract_storage_key(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        # A space in a source spreadsheet cell percent-encodes into the URL.
        # storage.download() needs the RAW key — handing it "%20" 404s, which
        # surfaced as a document that exported metadata-only for no visible
        # reason.
        (
            "https://abc.supabase.co/storage/v1/object/sign/driver-documents/"
            "saskatoon-import/batch%201/old%20id/criminal_record_check/main-u.pdf?token=t",
            "saskatoon-import/batch 1/old id/criminal_record_check/main-u.pdf",
        ),
        ("storage://driver-documents/a%20b/c%23d.pdf", "a b/c#d.pdf"),
    ],
)
def test_percent_encoded_keys_are_decoded_for_download(url, expected):
    assert _extract_storage_key(url) == expected


@pytest.mark.parametrize("url", ["", None, "   "])
def test_empty_input_yields_no_key(url):
    assert _extract_storage_key(url) is None


def test_unparseable_url_is_reported_not_guessed():
    """A URL we can't parse must return None so the caller records
    'unavailable', rather than fabricating a key out of a hostname."""
    assert _extract_storage_key("https://example.com/some/other/path.jpg") is None
