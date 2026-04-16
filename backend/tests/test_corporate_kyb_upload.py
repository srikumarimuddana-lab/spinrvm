# backend/tests/test_corporate_kyb_upload.py
from unittest.mock import AsyncMock, patch


def test_generates_signed_upload_url(test_client, admin_override):
    with patch(
        "db_supabase.create_kyb_upload_url",
        AsyncMock(
            return_value={
                "signed_url": "https://supabase.test/signed?sig=x",
                "path": "kyb/c1/doc.pdf",
                "expires_at": "2026-04-16T01:00:00Z",
            }
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-upload-url",
            json={"content_type": "application/pdf"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["signed_url"].startswith("https://")
    assert body["path"] == "kyb/c1/doc.pdf"


def test_rejects_non_pdf_or_image(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/kyb-upload-url",
        json={"content_type": "application/zip"},
    )
    assert resp.status_code == 400, resp.text


def test_rejects_extra_fields(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/kyb-upload-url",
        json={"content_type": "application/pdf", "evil": "x"},
    )
    assert resp.status_code == 422, resp.text
