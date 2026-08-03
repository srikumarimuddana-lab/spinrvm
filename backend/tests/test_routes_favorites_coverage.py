"""Coverage-focused tests for backend/routes/favorites.py.

A1c Sub-tier C: test-only, no application code changed. Written purely by
reading backend/routes/favorites.py — pytest was NOT run against this file
(per task instructions); the full suite runs once, at the end, by someone
else. Targets the previously-uncovered branches: the duplicate-check
exception swallow, the whole `use_favorite_route` handler (increment +
success), the success path of `delete_favorite_route`, and the whole
`save_favorite_from_ride` handler (404 / 403 / success delegation).

Follows the `routes.favorites.db` patch pattern already established in
test_p3_addresses_favorites_safety_disputes.py.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIDER = {"id": "user_1", "role": "rider", "phone": "+13061234567"}


@pytest.fixture
def client():
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: RIDER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _mock_fav_db(**overrides):
    m = MagicMock()
    m.get_rows = AsyncMock(return_value=[])
    m.find_one = AsyncMock(return_value=None)
    m.insert_one = AsyncMock(return_value={"id": "fav_new"})
    m.update_one = AsyncMock(return_value=None)
    m.delete_one = AsyncMock(return_value=[{"id": "fav_1"}])
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


FAV_ROW = {
    "id": "fav_1",
    "user_id": "user_1",
    "name": "Airport Run",
    "pickup_address": "123 Main St",
    "pickup_lat": 52.1,
    "pickup_lng": -106.0,
    "dropoff_address": "YXE Airport",
    "dropoff_lat": 52.17,
    "dropoff_lng": -106.7,
    "use_count": 3,
}

SAVE_PAYLOAD = {
    "name": "Airport Run",
    "pickup_address": "123 Main St",
    "pickup_lat": 52.1,
    "pickup_lng": -106.0,
    "dropoff_address": "YXE Airport",
    "dropoff_lat": 52.17,
    "dropoff_lng": -106.7,
}

RIDE_ROW = {
    "id": "ride_1",
    "rider_id": "user_1",
    "pickup_address": "123 Main St",
    "pickup_lat": 52.1,
    "pickup_lng": -106.0,
    "dropoff_address": "YXE Airport",
    "dropoff_lat": 52.17,
    "dropoff_lng": -106.7,
    "vehicle_type_id": "standard",
}


class TestSaveFavoriteDuplicateCheckErrorHandling:
    def test_duplicate_check_db_error_is_swallowed_and_save_proceeds(self, client):
        """Covers lines 80-81: db.get_rows raising during the duplicate scan
        must not fail the request — it's logged at debug and the handler
        falls through to the address-verification + insert path."""
        fdb = _mock_fav_db(get_rows=AsyncMock(side_effect=RuntimeError("db down")))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None))),
        ):
            r = client.post("/api/v1/favorites", json=SAVE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["name"] == "Airport Run"
        fdb.insert_one.assert_called_once()


class TestUseFavoriteRoute:
    def test_use_favorite_not_found_404(self, client):
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=None))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/missing/use")
        assert r.status_code == 404
        assert r.json()["detail"] == "Favorite not found"

    def test_use_favorite_success_increments_and_returns_fav(self, client):
        """Covers lines 118-132. Fixed (2026-08-03, favorites.py): the
        handler now returns the post-increment row (merging the write
        locally) instead of the *pre-increment* `fav` object it fetched —
        previously a client reading `use_count` off this response saw the
        stale count even though the DB row was correctly incremented."""
        fav = dict(FAV_ROW)
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=fav))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/fav_1/use")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "fav_1"
        # The response now reflects the post-increment value.
        assert body["use_count"] == 4
        assert body["last_used_at"] is not None

        fdb.update_one.assert_called_once()
        args, _ = fdb.update_one.call_args
        assert args[0] == "favorite_routes"
        assert args[1] == {"id": "fav_1"}
        assert args[2]["$set"]["use_count"] == 4
        assert "last_used_at" in args[2]["$set"]

    def test_use_favorite_missing_use_count_defaults_to_zero(self, client):
        """use_count absent/None on the row -> treated as 0 before +1
        (the `(fav.get("use_count", 0) or 0) + 1` guard)."""
        fav = {"id": "fav_2", "user_id": "user_1", "use_count": None}
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=fav))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/fav_2/use")
        assert r.status_code == 200
        args, _ = fdb.update_one.call_args
        assert args[2]["$set"]["use_count"] == 1


class TestDeleteFavoriteRoute:
    def test_delete_favorite_success(self, client):
        """Covers lines 142-143 (the success path of delete_favorite_route;
        the 404 path was already covered by the existing P3 suite)."""
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=FAV_ROW))
        with patch("routes.favorites.db", fdb):
            r = client.delete("/api/v1/favorites/fav_1")
        assert r.status_code == 200
        assert r.json() == {"success": True}
        fdb.delete_one.assert_called_once_with("favorite_routes", {"id": "fav_1"})


class TestSaveFavoriteFromRide:
    def test_ride_not_found_404(self, client):
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=None))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/from-ride/ghost_ride")
        assert r.status_code == 404
        assert r.json()["detail"] == "Ride not found"

    def test_ride_not_owned_by_caller_403(self, client):
        other_ride = {**RIDE_ROW, "rider_id": "someone_else"}
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=other_ride))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/from-ride/ride_1")
        assert r.status_code == 403
        assert r.json()["detail"] == "Not authorized"

    def test_save_from_ride_success_delegates_to_save_favorite_route(self, client):
        """Covers lines 153-169: builds a SaveFavoriteRequest from the ride
        row and delegates to save_favorite_route (which itself runs the
        duplicate-check + address-verification path again)."""
        # find_one is called twice: once for the ride lookup (find_one on
        # "rides"), once inside save_favorite_route's dedupe path is
        # get_rows, not find_one, so a single find_one mock returning the
        # ride is sufficient here.
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=RIDE_ROW), get_rows=AsyncMock(return_value=[]))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_1", params={"name": "Commute"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Commute"
        assert body["pickup_address"] == RIDE_ROW["pickup_address"]
        assert body["dropoff_address"] == RIDE_ROW["dropoff_address"]
        assert body["vehicle_type_id"] == "standard"
        fdb.insert_one.assert_called_once()

    def test_save_from_ride_default_name(self, client):
        """The `name` query param defaults to "My Route" when omitted."""
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=RIDE_ROW), get_rows=AsyncMock(return_value=[]))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_1")
        assert r.status_code == 200
        assert r.json()["name"] == "My Route"

    def test_save_from_ride_missing_coordinate_fields_default_to_zero(self, client):
        """Ride row missing lat/lng/address fields entirely -> .get(..., 0)
        / .get(..., "") defaults keep SaveFavoriteRequest validation happy
        (lat/lng defaults of 0 are within the -90..90 / -180..180 bounds)."""
        sparse_ride = {"id": "ride_2", "rider_id": "user_1"}
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=sparse_ride), get_rows=AsyncMock(return_value=[]))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_2")
        assert r.status_code == 200
        body = r.json()
        assert body["pickup_lat"] == 0
        assert body["pickup_lng"] == 0
        assert body["dropoff_address"] == ""
        assert body["vehicle_type_id"] is None
