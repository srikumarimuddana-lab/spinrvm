"""Unit tests for the public /legal-documents endpoint.

Covers the doc_type expansion beyond tos/privacy: the new policy-page types
(community-guidelines, non-discrimination, etc.) have no legacy
`/settings/legal` blob to fall back to, so an unpublished row must return
empty content rather than erroring or reusing the tos/privacy legacy text.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes.legal_documents import ALLOWED_TYPES, get_legal_document
except ImportError:
    from backend.routes.legal_documents import (  # type: ignore[no-redef]
        ALLOWED_TYPES,
        get_legal_document,
    )


# NOTE: every test above this line calls get_legal_document() directly — it
# exercises the handler's data-resolution logic but never touches how the
# router is actually mounted in server.py. That gap is exactly how the
# 2026-08-27 blank-policy production bug shipped unnoticed: the endpoint was
# only ever wired into v1_api_router (-> /api/v1/legal-documents), while its
# own docstring and all four mobile call sites assumed a root mount
# (/legal-documents, no prefix) — see server.py's dual app.include_router()
# calls for legal_documents_router. The test below goes through the real
# ASGI app (test_client fixture, conftest.py) so a future regression on
# *either* mount actually fails CI, instead of only testing logic that was
# never wrong in the first place.
@pytest.mark.anyio
async def test_legal_documents_reachable_at_documented_root_path(test_client):
    """GET /legal-documents (no /api/v1 prefix) must resolve — this is the
    path routes/legal_documents.py's own docstring documents as canonical,
    and the only path every mobile legal-content fetch actually calls."""
    row = {"content": "Published ToS text", "version": 2, "updated_at": "2026-08-17"}
    with patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=row)):
        response = test_client.get("/legal-documents", params={"audience": "rider", "type": "tos"})
    assert response.status_code == 200
    assert response.json()["content"] == "Published ToS text"


@pytest.mark.anyio
async def test_legal_documents_also_reachable_under_api_v1_prefix(test_client):
    """The /api/v1 alias must keep working too — additive, not a replacement
    for the root mount."""
    row = {"content": "Published Privacy text", "version": 1, "updated_at": "2026-08-17"}
    with patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=row)):
        response = test_client.get("/api/v1/legal-documents", params={"audience": "driver", "type": "privacy"})
    assert response.status_code == 200
    assert response.json()["content"] == "Published Privacy text"


@pytest.mark.anyio
async def test_published_row_wins_over_legacy():
    row = {"content": "Published ToS text", "version": 2, "updated_at": "2026-08-17"}
    with patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=row)):
        out = await get_legal_document(audience="rider", doc_type="tos")
    assert out == {
        "audience": "rider",
        "type": "tos",
        "content": "Published ToS text",
        "version": 2,
        "updated_at": "2026-08-17",
    }


@pytest.mark.anyio
async def test_tos_falls_back_to_legacy_settings_blob_when_unpublished():
    with (
        patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "routes.legal_documents.get_app_settings",
            AsyncMock(return_value={"terms_of_service_text": "Legacy ToS blob"}),
        ),
    ):
        out = await get_legal_document(audience="driver", doc_type="tos")
    assert out["content"] == "Legacy ToS blob"
    assert out["version"] == 0


@pytest.mark.anyio
async def test_privacy_falls_back_to_legacy_settings_blob_when_unpublished():
    with (
        patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "routes.legal_documents.get_app_settings",
            AsyncMock(return_value={"privacy_policy_text": "Legacy privacy blob"}),
        ),
    ):
        out = await get_legal_document(audience="rider", doc_type="privacy")
    assert out["content"] == "Legacy privacy blob"


@pytest.mark.anyio
async def test_new_doc_type_returns_empty_when_unpublished_not_legacy_text():
    """A new-style doc type (e.g. community-guidelines) has no legacy
    source — an unpublished row must return empty content, never the
    unrelated tos/privacy legacy blob."""
    settings_mock = AsyncMock(return_value={"terms_of_service_text": "should not leak here"})
    with (
        patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("routes.legal_documents.get_app_settings", settings_mock),
    ):
        out = await get_legal_document(audience="rider", doc_type="community-guidelines")
    assert out["content"] == ""
    assert out["version"] == 0
    # No legacy source exists for this type — get_app_settings should not
    # even be consulted.
    settings_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_new_doc_type_published_row_returned_normally():
    row = {"content": "Be excellent to each other.", "version": 1, "updated_at": "2026-08-17"}
    with patch("routes.legal_documents.db_supabase.find_one", AsyncMock(return_value=row)):
        out = await get_legal_document(audience="driver", doc_type="non-discrimination")
    assert out["content"] == "Be excellent to each other."
    assert out["type"] == "non-discrimination"


def test_allowed_types_includes_every_new_policy_page():
    expected = {
        "tos",
        "privacy",
        "community-guidelines",
        "non-discrimination",
        "accessibility",
        "cancellation-fees",
        "promotions-referral",
        "insurance-periods",
        "deactivation-appeals",
        "background-check-consent",
    }
    assert ALLOWED_TYPES == expected
