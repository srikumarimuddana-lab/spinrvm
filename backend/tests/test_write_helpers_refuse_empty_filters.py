"""The shared write helpers must refuse an empty filter (2026-09-20 review, C3).

``repositories/_base.py`` already raises when a ``$or`` collapses to nothing,
with the reasoning that "applying no filter would instead match the ENTIRE
table, and on an update/delete would write it" — but the top-level ``{}`` /
``None`` case fell straight through ``_apply_filters`` and issued an
unfiltered ``UPDATE`` / ``DELETE``. Any caller whose filter dict is built
conditionally and collapses to ``{}`` would silently rewrite or truncate a
table. These tests pin the guard shut on every write helper, in both the
"client configured" and "client absent" states, and confirm the one
deliberate exemption (``upsert=True`` carries its key in the payload).

Patch target is ``backend.repositories._base.supabase`` (the binding
``update_one``/``delete_many`` actually read) — see the module docstring of
``test_base_pii_logging.py`` for why ``backend.db_supabase.supabase`` would be
a no-op here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import backend.repositories._base as base

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("empty", [{}, None])
async def test_update_one_refuses_empty_filters(mock_supabase_client, empty):
    with patch.object(base, "supabase", mock_supabase_client):
        with pytest.raises(ValueError, match="update_one"):
            await base.update_one("rides", empty, {"status": "cancelled"})
    mock_supabase_client.table.assert_not_called()


@pytest.mark.parametrize("empty", [{}, None])
async def test_delete_many_refuses_empty_filters(mock_supabase_client, empty):
    with patch.object(base, "supabase", mock_supabase_client):
        with pytest.raises(ValueError, match="delete_many"):
            await base.delete_many("rides", empty)
    mock_supabase_client.table.assert_not_called()


async def test_delete_one_refuses_empty_filters(mock_supabase_client):
    with patch.object(base, "supabase", mock_supabase_client):
        with pytest.raises(ValueError):
            await base.delete_one("users", {})
    mock_supabase_client.table.assert_not_called()


async def test_guard_fires_before_the_client_check():
    """Even with no Supabase client configured (dev/test), an empty filter is
    a caller bug and must not degrade to the silent ``_write_skipped`` path —
    the same code would write the whole table the moment a client exists."""
    with patch.object(base, "supabase", None):
        with pytest.raises(ValueError):
            await base.update_one("rides", {}, {"status": "cancelled"})
        with pytest.raises(ValueError):
            await base.delete_many("rides", {})


async def test_upsert_with_empty_filters_is_still_allowed(mock_supabase_client):
    """``upsert=True`` merges the filters into the payload and lets PostgREST
    match on the primary key, so an empty filter is a plain insert-or-update
    of one row, never a table-wide write."""
    res = MagicMock(data=[{"id": "u1", "name": "x"}])
    mock_supabase_client.table.return_value.upsert.return_value.execute.return_value = res
    with patch.object(base, "supabase", mock_supabase_client):
        row = await base.update_one("users", {}, {"id": "u1", "name": "x"}, upsert=True)
    assert row == {"id": "u1", "name": "x"}
    mock_supabase_client.table.return_value.upsert.assert_called_once_with({"id": "u1", "name": "x"})


async def test_non_empty_filter_still_writes(mock_supabase_client):
    """Positive anchor so the guard can't be 'fixed' by refusing everything."""
    res = MagicMock(data=[{"id": "r1", "status": "cancelled"}])
    q = mock_supabase_client.table.return_value.update.return_value
    q.eq.return_value = q
    q.execute.return_value = res
    with patch.object(base, "supabase", mock_supabase_client):
        row = await base.update_one("rides", {"id": "r1"}, {"status": "cancelled"})
    assert row == {"id": "r1", "status": "cancelled"}
