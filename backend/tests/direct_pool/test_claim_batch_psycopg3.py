"""Exercise the PRODUCTION driver path for the dispatch_claim_batch RPC.

Review finding (2026-09-03): every other test of the RPC drives it through
psycopg2 (`test_claim_batch.py`) or mocks `dispatch_pool.claim_batch`
outright (`test_dispatch_claim_parity.py`), so the psycopg3 binding —
`AsyncConnectionPool`, `prepare_threshold=None`, typed parameter
adaptation for the `text[]`/`int[]` arguments, `dict_row`, jsonb -> dict
decoding — was never executed anywhere. This file opens a real
`AsyncConnectionPool` against the throwaway database the conftest builds
and calls `backend.repositories.dispatch_pool.claim_batch` itself.

Self-skips when psycopg3 / psycopg_pool are not installed (the mocked suite's
environment) — same posture as the rest of this directory.
"""

from __future__ import annotations

import asyncio
import os
import urllib.parse as _u
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
_EXPIRES = _NOW + timedelta(seconds=15)


def _dsn_for(pg_conn) -> str:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    info = pg_conn.get_dsn_parameters()
    parts = _u.urlsplit(dsn)
    return _u.urlunsplit((parts.scheme, parts.netloc, f"/{info['dbname']}", parts.query, parts.fragment))


def _seed(cur):
    for uid in ("p3-u1", "p3-u2", "p3-rider"):
        cur.execute("INSERT INTO users (id, phone) VALUES (%s, %s)", (uid, f"+1306555{uid[-4:].replace('-', '0')}"))
    for did, uid in (("p3-d1", "p3-u1"), ("p3-d2", "p3-u2")):
        cur.execute(
            "INSERT INTO drivers (id, user_id, name, phone, is_online, is_available, is_verified, status) "
            "VALUES (%s, %s, %s, %s, true, true, true, 'active')",
            (did, uid, f"Driver {did}", f"+1306555{did[-2:]}00"),
        )
    cur.execute(
        "INSERT INTO rides (id, rider_id, pickup_address, pickup_lat, pickup_lng, dropoff_address, dropoff_lat, dropoff_lng) "
        "VALUES ('p3-ride', 'p3-rider', 'A', 52.13, -106.67, 'B', 52.15, -106.60)"
    )


def test_claim_batch_over_a_real_async_pool(pg_cur, monkeypatch):
    from backend.repositories import dispatch_pool

    _seed(pg_cur)
    dsn = _dsn_for(pg_cur.connection)

    monkeypatch.setattr(dispatch_pool, "_pool", None)
    monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", dsn, raising=False)
    monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_MIN_SIZE", 1, raising=False)
    monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_MAX_SIZE", 2, raising=False)
    monkeypatch.setattr(dispatch_pool.settings, "ENV", "test", raising=False)
    monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
    monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: None)

    async def _scenario():
        pool = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)
        assert pool is not None and dispatch_pool.is_open()
        try:
            stats = pool.get_stats()
            # The keys _in_use() relies on — asserted against the real package.
            assert "pool_size" in stats and "pool_available" in stats

            rows = await dispatch_pool.claim_batch("p3-ride", ["p3-d1", "p3-d2"], [100, 200], 2, _NOW, _EXPIRES)
            assert [r["driver_id"] for r in rows] == ["p3-d1", "p3-d2"]
            assert all(r["claimed"] is True for r in rows)
            assert all(r["insurance_written"] is True for r in rows)
            assert all(isinstance(r["driver_row"], dict) and r["driver_row"]["user_id"] for r in rows)
            assert all(r["ride_offer_id"] is not None for r in rows)

            # Re-entry on the same drivers: both now unavailable -> unclaimed.
            again = await dispatch_pool.claim_batch("p3-ride", ["p3-d1"], [100], 1, _NOW, _EXPIRES)
            assert again == [] or again[0]["claimed"] is False

            assert await dispatch_pool.claim_batch("p3-ride", [], [], 1, _NOW, _EXPIRES) == []
            assert await dispatch_pool.run_query("SELECT 1", fetch="one") == (1,)
        finally:
            await dispatch_pool.close_pool()
        assert dispatch_pool.is_open() is False

    asyncio.run(_scenario())

    pg_cur.execute("SELECT count(*) FROM ride_offers WHERE ride_id = 'p3-ride' AND status = 'pending'")
    assert pg_cur.fetchone()[0] == 2
    pg_cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE ride_id = 'p3-ride' AND period = 2")
    assert pg_cur.fetchone()[0] == 2
