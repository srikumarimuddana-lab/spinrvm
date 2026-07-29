"""Unit tests for services/admin_export_approvals.py (ACTION_ITEMS.md B10).

The property under test that matters most: a requester can never approve
or deny their own request -- everything else is standard CRUD-state-machine
plumbing around that one invariant.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import admin_export_approvals as svc


@pytest.mark.asyncio
async def test_create_request_inserts_pending_row():
    with patch.object(svc.db_supabase, "insert_one", AsyncMock(return_value={"id": "r1", "status": "pending"})) as m:
        row = await svc.create_request(
            requested_by="admin-1", route_key="data_transfer.export", params={"entities": ["a"]}, row_count=5
        )
    assert row["status"] == "pending"
    inserted = m.call_args.args[1]
    assert inserted["requested_by"] == "admin-1"
    assert inserted["status"] == "pending"
    assert inserted["row_count"] == 5


@pytest.mark.asyncio
async def test_find_approved_grant_matches_exact_params():
    rows = [
        {
            "id": "r1",
            "params": {"entities": ["a"]},
            "consumed_at": None,
            "expires_at": "2999-01-01T00:00:00+00:00",
        },
        {
            "id": "r2",
            "params": {"entities": ["b"]},
            "consumed_at": None,
            "expires_at": "2999-01-01T00:00:00+00:00",
        },
    ]
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        grant = await svc.find_approved_grant(
            requested_by="admin-1", route_key="data_transfer.export", params={"entities": ["b"]}
        )
    assert grant["id"] == "r2"


@pytest.mark.asyncio
async def test_find_approved_grant_skips_consumed():
    rows = [{"id": "r1", "params": {"x": 1}, "consumed_at": "2026-01-01T00:00:00+00:00", "expires_at": None}]
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        grant = await svc.find_approved_grant(requested_by="admin-1", route_key="rk", params={"x": 1})
    assert grant is None


@pytest.mark.asyncio
async def test_find_approved_grant_skips_expired():
    rows = [{"id": "r1", "params": {"x": 1}, "consumed_at": None, "expires_at": "2000-01-01T00:00:00+00:00"}]
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        grant = await svc.find_approved_grant(requested_by="admin-1", route_key="rk", params={"x": 1})
    assert grant is None


@pytest.mark.asyncio
async def test_find_pending_request_matches_exact_params():
    rows = [{"id": "p1", "params": {"x": 1}}, {"id": "p2", "params": {"x": 2}}]
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=rows)):
        found = await svc.find_pending_request(requested_by="admin-1", route_key="rk", params={"x": 2})
    assert found["id"] == "p2"


@pytest.mark.asyncio
async def test_find_pending_request_returns_none_when_no_match():
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[])):
        found = await svc.find_pending_request(requested_by="admin-1", route_key="rk", params={"x": 1})
    assert found is None


@pytest.mark.asyncio
async def test_approve_rejects_self_approval():
    existing = {"id": "r1", "status": "pending", "requested_by": "admin-1"}
    with patch.object(svc.db_supabase, "find_one", AsyncMock(return_value=existing)):
        with pytest.raises(svc.SelfApprovalError):
            await svc.approve("r1", decided_by="admin-1")


@pytest.mark.asyncio
async def test_approve_by_different_admin_succeeds():
    existing = {"id": "r1", "status": "pending", "requested_by": "admin-1"}
    with (
        patch.object(svc.db_supabase, "find_one", AsyncMock(return_value=existing)),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": "r1", "status": "approved"})) as m,
    ):
        result = await svc.approve("r1", decided_by="admin-2", decision_note="looks fine")
    assert result["status"] == "approved"
    update_payload = m.call_args.args[2]
    assert update_payload["status"] == "approved"
    assert update_payload["decided_by"] == "admin-2"
    assert update_payload["decision_note"] == "looks fine"


@pytest.mark.asyncio
async def test_deny_by_different_admin_succeeds():
    existing = {"id": "r1", "status": "pending", "requested_by": "admin-1"}
    with (
        patch.object(svc.db_supabase, "find_one", AsyncMock(return_value=existing)),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": "r1", "status": "denied"})),
    ):
        result = await svc.deny("r1", decided_by="admin-2")
    assert result["status"] == "denied"


@pytest.mark.asyncio
async def test_approve_raises_not_found():
    with patch.object(svc.db_supabase, "find_one", AsyncMock(return_value=None)):
        with pytest.raises(svc.RequestNotFound):
            await svc.approve("nope", decided_by="admin-2")


@pytest.mark.asyncio
async def test_approve_raises_already_decided():
    existing = {"id": "r1", "status": "approved", "requested_by": "admin-1"}
    with patch.object(svc.db_supabase, "find_one", AsyncMock(return_value=existing)):
        with pytest.raises(svc.RequestAlreadyDecided):
            await svc.approve("r1", decided_by="admin-2")


@pytest.mark.asyncio
async def test_list_pending_filters_status():
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "r1"}])) as m:
        rows = await svc.list_pending()
    assert rows == [{"id": "r1"}]
    assert m.call_args.args[1] == {"status": "pending"}


@pytest.mark.asyncio
async def test_consume_stamps_consumed_at():
    with patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": "r1"})) as m:
        await svc.consume("r1")
    update_payload = m.call_args.args[2]
    assert "consumed_at" in update_payload
