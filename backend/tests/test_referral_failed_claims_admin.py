"""Admin failed-referral-claim re-queue endpoint (bug #5).

The re-queue DELETES a failed referral_payouts row so the payout loop re-credits
it. Money-adjacent, so pin the safety behaviour: 404 when there's no failed
claim, and the delete is guarded to status='failed' (never a paid/processing row)
plus audit-logged. Called directly (the FastAPI router doesn't wrap the function).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

try:
    from backend.routes.admin import drivers as admin_drivers
except ImportError:  # pragma: no cover - bare-path test runs
    from routes.admin import drivers as admin_drivers


def test_requeue_404_when_no_failed_claim(monkeypatch):
    monkeypatch.setattr(admin_drivers.db_supabase, "get_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_drivers.db_supabase, "delete_one", AsyncMock())
    with pytest.raises(HTTPException) as ei:
        asyncio.run(admin_drivers.admin_requeue_failed_referral("rf1", admin={"id": "a1"}))
    assert ei.value.status_code == 404


def test_requeue_deletes_only_failed_and_audits(monkeypatch):
    monkeypatch.setattr(
        admin_drivers.db_supabase,
        "get_rows",
        AsyncMock(return_value=[{"id": "claim1", "kind": "rider"}]),
    )
    deleted = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(admin_drivers.db_supabase, "delete_one", deleted)
    monkeypatch.setattr(admin_drivers, "log_admin_action", audit)

    out = asyncio.run(admin_drivers.admin_requeue_failed_referral("rf1", admin={"id": "a1"}))
    assert out["success"] is True
    # Atomic guard: delete by id AND status='failed' — never a paid/processing row.
    deleted.assert_awaited_once()
    table, where = deleted.await_args.args
    assert table == "referral_payouts"
    assert where == {"id": "claim1", "status": "failed"}
    audit.assert_awaited_once()
