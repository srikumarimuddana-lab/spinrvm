"""H3 index rebuild: completeness and admin force vs leader-lock skip."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def test_tick_skips_when_leader_lock_held():
    with (
        patch("utils.h3_index_reconciler.try_acquire_leader_lock", AsyncMock(return_value=False)),
        patch("utils.h3_index_reconciler.db.get_rows", AsyncMock()) as get_rows,
    ):
        from utils.h3_index_reconciler import _tick

        result = await _tick()
    assert result["skipped"] is True
    assert result["ok"] is False
    get_rows.assert_not_awaited()


async def test_tick_force_runs_without_leader_lock():
    with (
        patch("utils.h3_index_reconciler.try_acquire_leader_lock", AsyncMock(return_value=False)) as lock,
        patch("utils.h3_index_reconciler.db.get_rows", AsyncMock(return_value=[])),
        patch("utils.h3_index_reconciler.set_ready", AsyncMock()) as ready,
        patch("utils.h3_index_reconciler.record_event", AsyncMock()),
        patch("utils.h3_index_reconciler._next_generation", AsyncMock(return_value=2)),
    ):
        from utils.h3_index_reconciler import _tick

        result = await _tick(force=True)
    lock.assert_not_awaited()
    assert result["skipped"] is False
    assert result["incomplete"] is False
    ready.assert_awaited()
    assert ready.await_args.kwargs["incomplete"] is False


async def test_failed_upserts_do_not_mark_index_complete():
    rows = [{"id": "d1", "lat": 52.13, "lng": -106.67}]
    with (
        patch("utils.h3_index_reconciler.try_acquire_leader_lock", AsyncMock(return_value=True)),
        patch("utils.h3_index_reconciler.db.get_rows", AsyncMock(side_effect=[rows, []])),
        patch("utils.h3_index_reconciler.on_location_written", AsyncMock(return_value=False)),
        patch("utils.h3_index_reconciler.set_ready", AsyncMock()) as ready,
        patch("utils.h3_index_reconciler.record_event", AsyncMock()),
        patch("utils.h3_index_reconciler._next_generation", AsyncMock(return_value=3)),
    ):
        from utils.h3_index_reconciler import _tick

        result = await _tick()
    assert result["ok"] is False
    assert result["incomplete"] is True
    assert result["failed"] == 1
    assert ready.await_args.kwargs["incomplete"] is True


def test_admin_rebuild_endpoint_forces_tick():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "routes" / "admin" / "monitoring.py").read_text(encoding="utf-8")
    assert "await _tick(force=True)" in src
