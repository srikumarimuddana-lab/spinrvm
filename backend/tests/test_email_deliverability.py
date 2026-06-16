"""Unit tests for the admin email-deliverability monitoring endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes.admin.monitoring import get_email_deliverability
except ImportError:
    from backend.routes.admin.monitoring import get_email_deliverability  # type: ignore[no-redef]

_SEND_ROWS = [
    {"status": "sent", "provider": "ses", "email_type": "receipt", "recipient_user_id": "u1", "created_at": "2026-06-15T10:00:00Z"},
    {"status": "sent", "provider": "resend", "email_type": "receipt", "recipient_user_id": "u2", "created_at": "2026-06-15T10:01:00Z"},
    {"status": "failed", "provider": "none", "email_type": "dsar", "recipient_user_id": "u3", "created_at": "2026-06-15T10:02:00Z"},
    {"status": "suppressed", "provider": "none", "email_type": "receipt", "recipient_user_id": "u4", "created_at": "2026-06-15T10:03:00Z"},
]
_SUPP_ROWS = [
    {"email": "bounced@example.com", "reason": "bounce", "detail": "General", "source": "ses", "message_id": "m1", "created_at": "2026-06-15T09:00:00Z"},
]


@pytest.mark.anyio
async def test_deliverability_aggregates_and_omits_pii():
    with (
        patch("routes.admin.monitoring.get_rows", AsyncMock(side_effect=[_SEND_ROWS, _SUPP_ROWS])),
        patch("routes.admin.monitoring.count_documents", AsyncMock(return_value=1)),
    ):
        out = await get_email_deliverability(days=7, current_admin={"id": "admin"})

    assert out["total"] == 4
    assert out["by_status"] == {"sent": 2, "failed": 1, "suppressed": 1}
    assert out["by_provider"]["ses"] == 1 and out["by_provider"]["resend"] == 1
    assert out["failure_rate"] == round(1 / 4, 4)
    assert out["suppression_list_size"] == 1
    assert len(out["recent_failures"]) == 1 and out["recent_failures"][0]["email_type"] == "dsar"
    # PIPEDA: the suppression address must never be returned.
    blob = repr(out)
    assert "bounced@example.com" not in blob
    assert out["recent_suppressions"][0]["reason"] == "bounce"


@pytest.mark.anyio
async def test_deliverability_empty_window():
    with (
        patch("routes.admin.monitoring.get_rows", AsyncMock(side_effect=[[], []])),
        patch("routes.admin.monitoring.count_documents", AsyncMock(return_value=0)),
    ):
        out = await get_email_deliverability(days=1, current_admin={"id": "admin"})
    assert out["total"] == 0
    assert out["failure_rate"] == 0.0


@pytest.mark.anyio
async def test_deliverability_days_clamped():
    with (
        patch("routes.admin.monitoring.get_rows", AsyncMock(side_effect=[[], []])),
        patch("routes.admin.monitoring.count_documents", AsyncMock(return_value=0)),
    ):
        out = await get_email_deliverability(days=999, current_admin={"id": "admin"})
    assert out["window_days"] == 90  # clamped to max
