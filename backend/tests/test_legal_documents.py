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
    }
    assert ALLOWED_TYPES == expected
