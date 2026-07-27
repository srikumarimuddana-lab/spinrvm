"""
P3-5: Promo concurrency tests.

increment_promo_uses is an atomic Postgres RPC:
    UPDATE promo_codes SET uses = uses + 1
    WHERE id = p_promo_id AND uses < p_max_uses
It returns True when the row was updated (capacity remaining) and False
when uses has already reached max_uses.  These tests verify:

  1. The helper correctly maps True/False RPC results.
  2. asyncio.gather across concurrent calls produces exactly N successes
     for a max_uses=N promo, mirroring the DB's serialized behavior.
  3. The /promo/apply route returns HTTP 409 when increment_promo_uses
     returns False (promo exhausted mid-flight).

Run:
    pytest backend/tests/test_p3_promo_concurrency.py -v
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

USER_ID = "user_promo_race"
PROMO_ID = "promo_race_001"
RIDE_ID = "ride_promo_race"


def _promo(max_uses: int = 5, uses: int = 0, **extra) -> dict:
    base = {
        "id": PROMO_ID,
        "code": "RACETEST",
        "is_active": True,
        "discount_type": "flat",
        "discount_value": 5.00,
        "max_uses": max_uses,
        "uses": uses,
        "max_uses_per_user": 1,
        "expiry_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "assigned_user_ids": [],
        "first_ride_only": False,
        "new_user_days": 0,
        "inactive_days": 0,
        "min_total_rides": 0,
        "max_total_rides": 0,
        "total_budget": 0,
        "min_ride_fare": 0.0,
        "budget_used": 0,
    }
    base.update(extra)
    return base


def _ride(fare: float = 20.0) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": USER_ID,
        "status": "completed",
        "total_fare": fare,
        "grand_total": fare,
    }


def _make_atomic_counter(capacity: int) -> MagicMock:
    """Return a supabase mock whose rpc().execute() returns True for the
    first `capacity` thread-safe calls and False thereafter, mirroring
    the UPDATE … WHERE uses < max_uses Postgres RPC."""
    lock = threading.Lock()
    granted = [0]

    def _execute():
        with lock:
            if granted[0] < capacity:
                granted[0] += 1
                res = MagicMock()
                res.data = True
                return res
        res = MagicMock()
        res.data = False
        return res

    mock = MagicMock()
    mock.rpc.return_value.execute.side_effect = _execute
    return mock


# ── increment_promo_uses helper ───────────────────────────────────────


class TestIncrementPromoUsesHelper:
    @pytest.mark.anyio
    async def test_returns_true_when_capacity_remains(self):
        """RPC data=True → helper returns True."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        res = MagicMock()
        res.data = True
        mock.rpc.return_value.execute.return_value = res

        with patch.object(dbs, "supabase", mock):
            result = await dbs.increment_promo_uses(PROMO_ID, max_uses=10)

        assert result is True

    @pytest.mark.anyio
    async def test_returns_false_when_exhausted(self):
        """RPC data=False (uses == max_uses) → helper returns False."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        res = MagicMock()
        res.data = False
        mock.rpc.return_value.execute.return_value = res

        with patch.object(dbs, "supabase", mock):
            result = await dbs.increment_promo_uses(PROMO_ID, max_uses=5)

        assert result is False

    @pytest.mark.anyio
    async def test_concurrent_calls_respect_capacity(self):
        """
        N concurrent calls with capacity=2 → exactly 2 True results,
        the rest False. Mirrors the DB's serialized UPDATE behavior.
        """
        import repositories.wallet_repo as dbs

        capacity = 2
        total = 6
        mock = _make_atomic_counter(capacity)

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(
                *[dbs.increment_promo_uses(PROMO_ID, max_uses=capacity) for _ in range(total)],
                return_exceptions=True,
            )

        granted = sum(1 for r in results if r is True)
        denied = sum(1 for r in results if r is False)
        assert granted == capacity, f"expected {capacity} grants, got {granted}"
        assert denied == total - capacity

    @pytest.mark.anyio
    async def test_unlimited_promo_always_returns_true(self):
        """max_uses=0 is treated as unlimited — helper always returns True."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        res = MagicMock()
        res.data = True
        mock.rpc.return_value.execute.return_value = res

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(*[dbs.increment_promo_uses(PROMO_ID, max_uses=0) for _ in range(5)])

        assert all(r is True for r in results)


# ── /promo/apply route ────────────────────────────────────────────────


class TestPromoApplyRouteRaceHandling:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        import dependencies
        from backend.server import app

        app.dependency_overrides[dependencies.get_current_user] = lambda: {
            "id": USER_ID,
            "phone": "+11234567890",
            "role": "rider",
            "is_driver": False,
        }
        # TestClient boots the full lifespan, which spawns ~29 background
        # loops including retention_purge_loop. Its own supabase reference
        # (module-level `from ..db_supabase import supabase`) isn't covered
        # by the shared mock_supabase_client fixture's calling convention
        # (that mock's .rpc() is an AsyncMock returning an awaited coroutine
        # directly, incompatible with retention_purge's .rpc(...).execute()
        # sync-chain-then-run_sync pattern) -- irrelevant to what this test
        # verifies, so stub the tick to a no-op instead of fixing the
        # shared fixture's broader calling-convention mismatch here.
        with patch("utils.retention_purge.run_retention_purge_tick", new_callable=AsyncMock):
            with TestClient(app) as c:
                yield c
        app.dependency_overrides.clear()

    def _patch_apply(self, promo: dict, ride: dict, *, increment_result: bool):
        """Return a context manager stack that stubs out all DB calls for apply_promo."""
        validation_result = {
            "valid": True,
            "promo_id": promo["id"],
            "code": promo["code"],
            "discount_type": promo["discount_type"],
            "discount_value": float(promo["discount_value"]),
            "discount_amount": float(promo["discount_value"]),
        }
        return (
            # find_one("rides", ...) must return the ride dict so rider_id check passes
            patch("routes.promotions.db_supabase.find_one", AsyncMock(return_value=ride)),
            # get_rows("promotions", ...) returns the promo row for max_uses lookup
            patch("routes.promotions.db_supabase.get_rows", AsyncMock(return_value=[promo])),
            patch("routes.promotions.validate_promo", AsyncMock(return_value=validation_result)),
            patch(
                "routes.promotions.increment_promo_uses",
                AsyncMock(return_value=increment_result),
            ),
            patch("routes.promotions.db_supabase.insert_one", AsyncMock(return_value={"id": "app_1"})),
        )

    def test_apply_returns_409_when_promo_exhausted_mid_flight(self, client):
        """increment_promo_uses returns False → route returns HTTP 409."""
        promo = _promo(max_uses=1, uses=1)
        ride = _ride()

        patches = self._patch_apply(promo, ride, increment_result=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post(
                "/api/v1/promo/apply",
                json={"code": "RACETEST", "ride_id": RIDE_ID},
            )

        assert resp.status_code == 409
        assert "redeemed" in resp.json()["detail"].lower()

    def test_apply_succeeds_when_capacity_available(self, client):
        """increment_promo_uses returns True → route returns 200 with discount."""
        promo = _promo(max_uses=10, uses=0)
        ride = _ride()

        patches = self._patch_apply(promo, ride, increment_result=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = client.post(
                "/api/v1/promo/apply",
                json={"code": "RACETEST", "ride_id": RIDE_ID},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["discount_applied"] == float(promo["discount_value"])

    @pytest.mark.anyio
    async def test_concurrent_increments_cap_at_max_uses(self):
        """
        Direct concurrent calls to increment_promo_uses with capacity=3
        and 8 callers → exactly 3 granted. The route-level 409 follows
        automatically from the False return, tested above.
        """
        import repositories.wallet_repo as dbs

        capacity = 3
        callers = 8
        mock = _make_atomic_counter(capacity)

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(
                *[dbs.increment_promo_uses(PROMO_ID, max_uses=capacity) for _ in range(callers)],
                return_exceptions=True,
            )

        granted = sum(1 for r in results if r is True)
        assert granted == capacity
