"""Coverage-focused unit tests for backend/routes/favorites.py (A1c Sub-tier C).

tests/test_p3_addresses_favorites_safety_disputes.py already covers list,
create (new + duplicate), the address-mismatch guard, and delete-not-found.
This file fills the remaining gaps: POST /favorites/{id}/use, the DELETE
success path, POST /favorites/from-ride/{ride_id} (not found / not
authorized / success delegation), and the duplicate-check's swallowed
exception branch.

Test-only — routes/favorites.py is not modified.
"""

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


class TestDuplicateCheckExceptionSwallow:
    def test_save_favorite_proceeds_when_duplicate_check_raises(self, client):
        """The dedupe lookup is best-effort — a DB error there must not block
        saving a genuinely new favorite (it only logs at debug level and
        falls through to the save path)."""
        fdb = _mock_fav_db(get_rows=AsyncMock(side_effect=RuntimeError("db down")))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None))),
        ):
            r = client.post(
                "/api/v1/favorites",
                json={
                    "name": "New Route",
                    "pickup_address": "1 A St",
                    "pickup_lat": 52.1,
                    "pickup_lng": -106.0,
                    "dropoff_address": "2 B St",
                    "dropoff_lat": 52.2,
                    "dropoff_lng": -106.2,
                },
            )
        assert r.status_code == 200
        fdb.insert_one.assert_called_once()


class TestUseFavoriteRoute:
    def test_use_favorite_route_not_found(self, client):
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=None))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/missing/use")
        assert r.status_code == 404

    def test_use_favorite_route_increments_use_count(self, client):
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=dict(FAV_ROW)))
        captured = {}

        async def _update(table, filters, update):
            captured["table"] = table
            captured["filters"] = filters
            captured["update"] = update

        fdb.update_one = AsyncMock(side_effect=_update)
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/fav_1/use")
        assert r.status_code == 200
        assert r.json()["id"] == "fav_1"
        assert captured["table"] == "favorite_routes"
        assert captured["filters"] == {"id": "fav_1"}
        assert captured["update"]["$set"]["use_count"] == 4
        assert "last_used_at" in captured["update"]["$set"]

    def test_use_favorite_route_missing_use_count_defaults_to_zero(self, client):
        """use_count may be None (row created before the field had a
        default) — must not raise a TypeError adding to None."""
        row = {**FAV_ROW, "use_count": None}
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=row))
        captured = {}

        async def _update(table, filters, update):
            captured["update"] = update

        fdb.update_one = AsyncMock(side_effect=_update)
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/fav_1/use")
        assert r.status_code == 200
        assert captured["update"]["$set"]["use_count"] == 1


class TestDeleteFavoriteRoute:
    def test_delete_favorite_route_success(self, client):
        fdb = _mock_fav_db(
            find_one=AsyncMock(return_value=dict(FAV_ROW)),
            delete_one=AsyncMock(return_value=[{"id": "fav_1"}]),
        )
        with patch("routes.favorites.db", fdb):
            r = client.delete("/api/v1/favorites/fav_1")
        assert r.status_code == 200
        assert r.json() == {"success": True}
        fdb.delete_one.assert_awaited_once_with("favorite_routes", {"id": "fav_1"})


class TestSaveFavoriteFromRide:
    def test_ride_not_found_returns_404(self, client):
        fdb = _mock_fav_db()
        with (
            patch("routes.favorites.db", fdb),
        ):
            fdb.find_one = AsyncMock(return_value=None)
            r = client.post("/api/v1/favorites/from-ride/ride_missing")
        assert r.status_code == 404

    def test_not_the_rider_of_the_ride_returns_403(self, client):
        ride = {"id": "ride_1", "rider_id": "someone_else", "pickup_address": "A", "dropoff_address": "B"}
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=ride))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites/from-ride/ride_1")
        assert r.status_code == 403

    def test_success_delegates_to_save_favorite_route(self, client):
        ride = {
            "id": "ride_1",
            "rider_id": "user_1",
            "pickup_address": "123 Main St",
            "pickup_lat": 52.1,
            "pickup_lng": -106.0,
            "dropoff_address": "YXE Airport",
            "dropoff_lat": 52.17,
            "dropoff_lng": -106.7,
            "vehicle_type_id": "vt_sedan",
        }
        fdb = _mock_fav_db(
            find_one=AsyncMock(return_value=ride),
            get_rows=AsyncMock(return_value=[]),
        )
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_1", params={"name": "Airport Trip"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Airport Trip"
        assert body["pickup_address"] == "123 Main St"
        assert body["vehicle_type_id"] == "vt_sedan"
        fdb.insert_one.assert_called_once()

    def test_default_name_used_when_not_supplied(self, client):
        ride = {
            "id": "ride_2",
            "rider_id": "user_1",
            "pickup_address": "1 St",
            "pickup_lat": 50.0,
            "pickup_lng": -104.0,
            "dropoff_address": "2 St",
            "dropoff_lat": 50.1,
            "dropoff_lng": -104.1,
        }
        fdb = _mock_fav_db(
            find_one=AsyncMock(return_value=ride),
            get_rows=AsyncMock(return_value=[]),
        )
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_2")
        assert r.status_code == 200
        assert r.json()["name"] == "My Route"

    def test_ride_with_missing_coordinates_defaults_to_zero(self, client):
        """Ride rows with no pickup/dropoff lat/lng recorded must not raise —
        SaveFavoriteRequest's field validators accept 0.0."""
        ride = {"id": "ride_3", "rider_id": "user_1"}
        fdb = _mock_fav_db(
            find_one=AsyncMock(return_value=ride),
            get_rows=AsyncMock(return_value=[]),
        )
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None))),
        ):
            r = client.post("/api/v1/favorites/from-ride/ride_3")
        assert r.status_code == 200
        assert r.json()["pickup_lat"] == 0
