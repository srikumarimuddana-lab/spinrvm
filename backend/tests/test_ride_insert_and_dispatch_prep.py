"""
Unit tests for two booking-time helpers extracted from create_ride:

- backend/routes/rides.py::_insert_ride_with_code — ride_code allocation,
  the PGRST204 (pre-migration-40) fallback, the two constraint-specific
  branches (one-active-ride, idempotency-key replay), and the retry-then-503
  exhaustion path.
- backend/routes/rides.py::_prep_and_dispatch — the post-booking background
  pipeline (pickup road-snap, server polyline, dispatch kickoff), all of
  which is best-effort / fail-open except the outer handler must never let
  an exception escape (it's a fire-and-forget background task).

Every branch is exercised with a fake DB exception carrying a `.code`
attribute (mirrors how pg_error_code/db_error_text actually read errors),
never against a real Supabase/Postgres connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


class _FakeDBError(Exception):
    """Minimal stand-in for the DatabaseError/DuplicateRecordError wrapper
    db_supabase.run_sync raises — carries the same `.code` attribute
    pg_error_code() reads directly off the exception."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# ── _insert_ride_with_code ─────────────────────────────────────────────────


class TestInsertRideWithCode:
    async def test_happy_path_inserts_on_first_try(self):
        from backend.routes.rides import _insert_ride_with_code

        inserted_row = {"id": "ride-1", "ride_code": "SPR-000001"}
        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(return_value=inserted_row),
        ) as mock_insert:
            row, reused = await _insert_ride_with_code({"id": "ride-1"}, rider_id="rider-1")

        assert row == inserted_row
        assert reused is False
        mock_insert.assert_awaited_once()
        assert "ride_code" in mock_insert.call_args.args[0]

    async def test_insert_returns_falsy_falls_back_to_local_ride_data(self):
        """A stub DB that returns None still gives the caller a usable row."""
        from backend.routes.rides import _insert_ride_with_code

        ride_data = {"id": "ride-2"}
        with patch("backend.routes.rides._deps.db_supabase.insert_ride", AsyncMock(return_value=None)):
            row, reused = await _insert_ride_with_code(ride_data, rider_id="rider-1")
        assert row is ride_data
        assert reused is False

    async def test_pgrst204_retries_without_ride_code_column(self):
        """Pre-migration-40 schema: ride_code column doesn't exist yet -- pop
        it and insert without it instead of failing the booking."""
        from backend.routes.rides import _insert_ride_with_code

        fallback_row = {"id": "ride-3"}
        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(side_effect=[_FakeDBError("column ride_code does not exist", code="PGRST204"), fallback_row]),
        ) as mock_insert:
            row, reused = await _insert_ride_with_code({"id": "ride-3"}, rider_id="rider-1")

        assert row == fallback_row
        assert reused is False
        assert mock_insert.await_count == 2
        # Second (successful) call must not carry the column that doesn't exist yet.
        assert "ride_code" not in mock_insert.call_args_list[1].args[0]

    async def test_one_active_ride_constraint_raises_409(self):
        from fastapi import HTTPException

        from backend.routes.rides import _insert_ride_with_code

        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(
                side_effect=_FakeDBError(
                    'duplicate key value violates constraint "rides_one_active_per_rider"'
                )
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await _insert_ride_with_code({"id": "ride-4"}, rider_id="rider-1")
        assert exc.value.status_code == 409
        assert "already have an active ride" in exc.value.detail

    async def test_idempotency_key_replay_returns_existing_ride(self):
        """A concurrent duplicate request lost the unique-index race -- the
        caller must get back the ride that WON, not an error, and must be
        told not to re-run post-insert side effects (dispatch, promo)."""
        from backend.routes.rides import _insert_ride_with_code

        existing_ride = {"id": "ride-existing", "idempotency_key": "idem-1"}
        with (
            patch(
                "backend.routes.rides._deps.db_supabase.insert_ride",
                AsyncMock(
                    side_effect=_FakeDBError(
                        'duplicate key value violates constraint "idx_rides_rider_idempotency_key"'
                    )
                ),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.find_one",
                AsyncMock(return_value=existing_ride),
            ),
        ):
            row, reused = await _insert_ride_with_code(
                {"id": "ride-5", "idempotency_key": "idem-1"}, rider_id="rider-1"
            )

        assert row == existing_ride
        assert reused is True

    async def test_idempotency_key_replay_with_no_existing_row_raises_409(self):
        """Constraint fired but the matching row can't be found (race with a
        cleanup, or a bug) -- surface 409 rather than silently proceeding."""
        from fastapi import HTTPException

        from backend.routes.rides import _insert_ride_with_code

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.insert_ride",
                AsyncMock(
                    side_effect=_FakeDBError(
                        'duplicate key value violates constraint "idx_rides_rider_idempotency_key"'
                    )
                ),
            ),
            patch("backend.routes.rides._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await _insert_ride_with_code({"id": "ride-6", "idempotency_key": "idem-2"}, rider_id="rider-1")
        assert exc.value.status_code == 409

    async def test_ride_code_collision_retries_with_a_new_code(self):
        """A unique-constraint hit on the ride_code column itself (astronomically
        unlikely, still handled) retries with a freshly generated code."""
        from backend.routes.rides import _insert_ride_with_code

        success_row = {"id": "ride-7", "ride_code": "SPR-000002"}
        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(
                side_effect=[
                    _FakeDBError("duplicate key value violates unique constraint", code="23505"),
                    success_row,
                ]
            ),
        ) as mock_insert:
            row, reused = await _insert_ride_with_code({"id": "ride-7"}, rider_id="rider-1")

        assert row == success_row
        assert reused is False
        assert mock_insert.await_count == 2

    async def test_exhausts_retries_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.rides import _insert_ride_with_code

        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(side_effect=_FakeDBError("duplicate key value violates unique constraint", code="23505")),
        ) as mock_insert:
            with pytest.raises(HTTPException) as exc:
                await _insert_ride_with_code({"id": "ride-8"}, rider_id="rider-1")

        assert exc.value.status_code == 503
        assert mock_insert.await_count == 3

    async def test_unrecognized_db_error_is_reraised(self):
        from backend.routes.rides import _insert_ride_with_code

        with patch(
            "backend.routes.rides._deps.db_supabase.insert_ride",
            AsyncMock(side_effect=_FakeDBError("connection reset by peer")),
        ):
            with pytest.raises(_FakeDBError):
                await _insert_ride_with_code({"id": "ride-9"}, rider_id="rider-1")


# ── _prep_and_dispatch ──────────────────────────────────────────────────────


def _fresh_ride(**kw):
    base = {
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "dropoff_lat": 52.2,
        "dropoff_lng": -106.7,
        "planned_route_polyline": None,
    }
    base.update(kw)
    return base


class TestPrepAndDispatch:
    async def test_happy_path_snaps_computes_polyline_and_dispatches(self):
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride()
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=(52.101, -106.601))),
            patch("backend.routes.rides._deps.db_supabase.update_ride", new_callable=AsyncMock) as mock_update,
            patch(
                "backend.routes.rides._deps.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "key123"}),
            ),
            patch(
                "backend.routes.rides.booking._fetch_directions_polyline",
                AsyncMock(return_value=[[52.1, -106.6], [52.2, -106.7]]),
            ),
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_match,
        ):
            await _prep_and_dispatch(
                "ride-1", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )

        assert fresh_ride["pickup_nav_lat"] == 52.101
        assert fresh_ride["planned_route_polyline"] == [[52.1, -106.6], [52.2, -106.7]]
        assert mock_update.await_count == 2  # nav snap + polyline
        mock_match.assert_awaited_once_with("ride-1", ride=fresh_ride)

    async def test_snap_failure_is_swallowed_and_dispatch_still_runs(self):
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride(planned_route_polyline=[[1, 2], [3, 4]])  # skip polyline branch
        with (
            patch(
                "backend.utils.route_distance.snap_to_road",
                AsyncMock(side_effect=RuntimeError("roads api down")),
            ),
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_match,
        ):
            await _prep_and_dispatch(
                "ride-2", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )
        mock_match.assert_awaited_once()

    async def test_existing_polyline_skips_recomputation(self):
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride(planned_route_polyline=[[1, 2], [3, 4]])
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=None)),
            patch(
                "backend.routes.rides.booking._fetch_directions_polyline", new_callable=AsyncMock
            ) as mock_directions,
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock),
        ):
            await _prep_and_dispatch(
                "ride-3", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )
        mock_directions.assert_not_awaited()

    async def test_no_maps_key_skips_polyline_computation(self):
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride()
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch(
                "backend.routes.rides.booking._fetch_directions_polyline", new_callable=AsyncMock
            ) as mock_directions,
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock),
        ):
            await _prep_and_dispatch(
                "ride-4", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )
        mock_directions.assert_not_awaited()

    async def test_polyline_computation_failure_is_swallowed(self):
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride()
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=None)),
            patch(
                "backend.routes.rides._deps.get_app_settings",
                AsyncMock(return_value={"google_maps_api_key": "key123"}),
            ),
            patch(
                "backend.routes.rides.booking._fetch_directions_polyline",
                AsyncMock(side_effect=RuntimeError("directions api down")),
            ),
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_match,
        ):
            await _prep_and_dispatch(
                "ride-5", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )
        mock_match.assert_awaited_once()

    async def test_dispatch_false_skips_dispatch_but_still_preps_nav(self):
        """Deferred scheduled rides prep nav/polyline but leave dispatch to
        the scheduler loop."""
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride(planned_route_polyline=[[1, 2], [3, 4]])
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=(52.1, -106.6))),
            patch("backend.routes.rides._deps.db_supabase.update_ride", new_callable=AsyncMock),
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_match,
        ):
            await _prep_and_dispatch(
                "ride-6", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=False
            )
        mock_match.assert_not_awaited()

    async def test_dispatch_failure_is_swallowed_not_propagated(self):
        """This runs as a background task with no caller to catch an
        exception -- a stranded ride relies on ride_search_timeout / the
        stuck-ride sweeper as its safety net, not on this function raising."""
        from backend.routes.rides import _prep_and_dispatch

        fresh_ride = _fresh_ride(planned_route_polyline=[[1, 2], [3, 4]])
        with (
            patch("backend.utils.route_distance.snap_to_road", AsyncMock(return_value=None)),
            patch(
                "backend.routes.rides.matching.match_driver_to_ride",
                AsyncMock(side_effect=RuntimeError("dispatch engine down")),
            ),
        ):
            # Must not raise.
            await _prep_and_dispatch(
                "ride-7", fresh_ride, pickup_lat=52.1, pickup_lng=-106.6, stops=[], dispatch=True
            )
