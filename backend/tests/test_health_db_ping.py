"""Regression tests for the DB readiness probe used by GET /health.

Background (Fly deploy outage, 2026-06-17):
  After F1 ("make /health a real DB readiness probe") wired /health to
  db_supabase.ping(), the probe queried a *non-existent* table —
  `app_settings` — instead of the real `settings` table (whose single config
  row has id='app_settings'). Production PostgREST answered PGRST205
  ("Could not find the table 'public.app_settings'"), so /health returned 503,
  every rolling-deploy machine's health check went critical, and releases
  v86–v88 failed while v85 (pre-F1, static health) kept serving.

These tests pin the probe to the table that actually exists so the readiness
gate can never again 503 a healthy replica over a wrong table name.

run_sync is stubbed to execute the probe closure inline — this exercises the
real ping() body (and its `_check` table name) without depending on the
thread-pool executor or the module-level circuit-breaker state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_ping_probes_settings_table_not_app_settings():
    """ping() must read the real `settings` table, never `app_settings`."""
    from backend.repositories import _base

    mock_supabase = MagicMock()

    async def _inline_run_sync(func, *args, **kwargs):
        return func()

    with (
        patch.object(_base, "supabase", mock_supabase),
        patch.object(_base, "run_sync", _inline_run_sync),
    ):
        result = await _base.ping()

    queried_tables = [call.args[0] for call in mock_supabase.table.call_args_list if call.args]
    assert "settings" in queried_tables, f"ping() should probe `settings`, got {queried_tables}"
    assert "app_settings" not in queried_tables, (
        "ping() must NOT query `app_settings` — that table does not exist "
        "(it's the row id in the `settings` table); querying it 503s /health "
        "and aborts rolling deploys (PGRST205)."
    )
    # Healthy probe returns the telemetry shape /health surfaces.
    assert "circuit_state" in result


@pytest.mark.anyio
async def test_ping_raises_databaseerror_when_probe_fails():
    """A failed probe must raise DatabaseError so /health correctly 503s.

    This is the failure mode the wrong table name triggered in production:
    PGRST205 -> APIError -> DatabaseError -> /health readiness check fails.
    """
    from backend.repositories._base import DatabaseError, ping

    async def _failing_run_sync(func, *args, **kwargs):
        raise RuntimeError("PGRST205: Could not find the table 'public.app_settings'")

    with patch("backend.repositories._base.run_sync", _failing_run_sync):
        with pytest.raises(DatabaseError):
            await ping()
