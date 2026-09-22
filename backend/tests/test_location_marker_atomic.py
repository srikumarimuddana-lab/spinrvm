"""Live coordinates must be committed with their capture-time condition."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from repositories import driver_repo


@pytest.mark.anyio
async def test_marker_uses_atomic_capture_rpc(monkeypatch):
    stamp = datetime.now(timezone.utc)
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(driver_repo, 'supabase', client)
    async def run(fn, *args, **kwargs):
        return fn()
    monkeypatch.setattr(driver_repo, 'run_sync', run)
    invalidation = AsyncMock()
    monkeypatch.setattr(driver_repo, 'invalidate_driver_cache', invalidation)
    accepted = await driver_repo.update_driver_location('d1', 50, -104, heading=395, captured_at=stamp)
    assert accepted is True
    params = client.rpc.call_args.args[1]
    assert client.rpc.call_args.args[0] == 'update_live_driver_marker'
    assert params['p_captured_at'] == stamp.isoformat()
    assert params['p_values']['heading'] == 35
    client.table.assert_not_called()
    invalidation.assert_awaited_once_with(driver_id='d1')


@pytest.mark.anyio
async def test_older_marker_rejection_is_propagated(monkeypatch):
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=False)
    monkeypatch.setattr(driver_repo, 'supabase', client)
    async def run(fn, *args, **kwargs): return fn()
    monkeypatch.setattr(driver_repo, 'run_sync', run)
    monkeypatch.setattr(driver_repo, 'invalidate_driver_cache', AsyncMock())
    assert await driver_repo.update_driver_location('d1', 50, -104, captured_at=datetime.now(timezone.utc)) is False
