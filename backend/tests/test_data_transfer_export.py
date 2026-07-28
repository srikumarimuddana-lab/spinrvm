"""Unit tests for backend/routes/admin/data_transfer_export.py's upload helper.

Regression test for B11/R-D: _upload_bundle previously minted a signed URL
(7-day TTL) on every export and then discarded it — the route is fully
backgrounded, so there was never a request left to hand that URL back to.
Confirms the wasted create_signed_url call is gone and the function returns
a plain storage_path string.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_upload_bundle_does_not_mint_signed_url():
    from routes.admin.data_transfer_export import _upload_bundle

    with patch("routes.admin.data_transfer_export.supabase") as mock_supabase:
        mock_bucket = MagicMock()
        mock_supabase.storage.from_.return_value = mock_bucket

        storage_path = await _upload_bundle("admin_1", b"zip-bytes", "zip", "application/zip")

    assert isinstance(storage_path, str)
    assert storage_path.startswith("exports/admin_1/")
    assert storage_path.endswith(".zip")
    mock_bucket.upload.assert_called_once()
    mock_bucket.create_signed_url.assert_not_called()


@pytest.mark.anyio
async def test_upload_bundle_raises_when_storage_not_configured():
    from routes.admin.data_transfer_export import _upload_bundle

    with patch("routes.admin.data_transfer_export.supabase", None):
        with pytest.raises(RuntimeError, match="Storage client not configured"):
            await _upload_bundle("admin_1", b"zip-bytes", "zip", "application/zip")
