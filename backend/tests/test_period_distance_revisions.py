"""Revision-aware corrections for the per-period distance audit (migration 342).

Contract (record_period_distance_revision):
  - appends revision = max(existing)+1 with a supersedes_id back-pointer —
    never UPDATE/DELETE (the base table is immutable);
  - skips inside the noise band max(0.05 km, 2%) — idempotent replays must not
    churn revisions;
  - skips when no base row exists (settlement owns first rows);
  - best-effort: failures log at ERROR, return False, never raise.
Deploy coupling: routes/admin/compliance.py must read the _current view.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.utils import period_distance_audit as mod

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


_BASE = {
    "id": "row-0",
    "driver_id": "drv-1",
    "ride_id": "ride-1",
    "period": 3,
    "distance_km": 4.2,
    "started_at": "t1",
    "ended_at": "t2",
    "revision": 0,
}


def test_appends_next_revision_with_supersedes_pointer(monkeypatch):
    monkeypatch.setattr(mod.db_supabase, "get_rows", AsyncMock(return_value=[_BASE]))
    insert = AsyncMock(return_value={"id": "row-1"})
    monkeypatch.setattr(mod.db_supabase, "insert_one", insert)

    assert (
        _run(mod.record_period_distance_revision(driver_id="drv-1", ride_id="ride-1", period=3, distance_km=6.8))
        is True
    )

    row = insert.await_args.args[1]
    assert row["revision"] == 1
    assert row["supersedes_id"] == "row-0"
    assert row["distance_km"] == 6.8
    assert row["source"] == "late_tail_rederivation"
    # Span timestamps carry over from the corrected row.
    assert (row["started_at"], row["ended_at"]) == ("t1", "t2")


def test_revises_on_top_of_the_latest_revision(monkeypatch):
    rev1 = {**_BASE, "id": "row-1", "distance_km": 6.8, "revision": 1}
    monkeypatch.setattr(mod.db_supabase, "get_rows", AsyncMock(return_value=[_BASE, rev1]))
    insert = AsyncMock(return_value={"id": "row-2"})
    monkeypatch.setattr(mod.db_supabase, "insert_one", insert)

    assert (
        _run(mod.record_period_distance_revision(driver_id="drv-1", ride_id="ride-1", period=3, distance_km=8.0))
        is True
    )
    row = insert.await_args.args[1]
    assert row["revision"] == 2
    assert row["supersedes_id"] == "row-1"


@pytest.mark.parametrize("new_km", [4.2, 4.24, 4.28])  # <= max(0.05, 2% of 4.2 = 0.084)
def test_noise_band_changes_are_skipped(monkeypatch, new_km):
    monkeypatch.setattr(mod.db_supabase, "get_rows", AsyncMock(return_value=[_BASE]))
    insert = AsyncMock()
    monkeypatch.setattr(mod.db_supabase, "insert_one", insert)

    assert (
        _run(mod.record_period_distance_revision(driver_id="drv-1", ride_id="ride-1", period=3, distance_km=new_km))
        is False
    )
    insert.assert_not_awaited()


def test_no_base_row_means_no_revision(monkeypatch):
    monkeypatch.setattr(mod.db_supabase, "get_rows", AsyncMock(return_value=[]))
    insert = AsyncMock()
    monkeypatch.setattr(mod.db_supabase, "insert_one", insert)

    assert (
        _run(mod.record_period_distance_revision(driver_id="drv-1", ride_id="ride-1", period=3, distance_km=9.9))
        is False
    )
    insert.assert_not_awaited()


def test_insert_failure_returns_false_never_raises(monkeypatch):
    monkeypatch.setattr(mod.db_supabase, "get_rows", AsyncMock(return_value=[_BASE]))
    monkeypatch.setattr(mod.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("unique violation")))

    assert (
        _run(mod.record_period_distance_revision(driver_id="drv-1", ride_id="ride-1", period=3, distance_km=9.9))
        is False
    )


def test_compliance_reads_the_current_view_not_the_base_table():
    # Deploy coupling: reading the base table after the first correction row
    # lands would double-count insurer billing.
    source = (Path(__file__).resolve().parents[1] / "routes" / "admin" / "compliance.py").read_text()
    assert '"driver_period_distances_current"' in source
    assert '\n        "driver_period_distances",' not in source
