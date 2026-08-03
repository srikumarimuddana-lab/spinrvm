"""P3 coverage: addresses, favorites, safety, and disputes routes."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIDER = {"id": "user_1", "role": "rider", "phone": "+13061234567"}
DRIVER_USER = {"id": "driver_user_1", "role": "driver", "is_driver": True, "phone": "+13069998888"}


@pytest.fixture
def client():
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: RIDER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def driver_client():
    """Same as `client` but authenticated as a driver (is_driver=True) —
    needed to exercise routes.safety's driver-side ride-membership branch,
    which `client`'s hardcoded RIDER can never reach."""
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: DRIVER_USER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _mock_db(**overrides):
    """Build a mock db_supabase module with sensible defaults."""
    m = MagicMock()
    m.get_rows = AsyncMock(return_value=[])
    m.find_one = AsyncMock(return_value=None)
    m.insert_one = AsyncMock(return_value={"id": "new_id"})
    m.update_one = AsyncMock(return_value=None)
    m.delete_one = AsyncMock(return_value=[{"id": "deleted"}])
    m.delete_many = AsyncMock(return_value=[])
    m.get_ride = AsyncMock(return_value=None)
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


# ──────────────────────────────── addresses ──────────────────────────────────


class TestAddresses:
    def test_get_addresses_returns_list(self, client):
        rows = [{"id": "a1", "user_id": "user_1", "name": "Home", "address": "123 Main", "lat": 52.1, "lng": -106.0}]
        db = _mock_db(get_rows=AsyncMock(return_value=rows))
        with patch("routes.addresses.db_supabase", db):
            r = client.get("/api/v1/addresses")
        assert r.status_code == 200
        assert r.json()[0]["name"] == "Home"

    def test_get_addresses_empty(self, client):
        db = _mock_db()
        with patch("routes.addresses.db_supabase", db):
            r = client.get("/api/v1/addresses")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_address_success(self, client):
        db = _mock_db()
        with patch("routes.addresses.db_supabase", db):
            r = client.post(
                "/api/v1/addresses",
                json={"name": "Work", "address": "456 Office Blvd", "lat": 52.2, "lng": -106.1, "icon": "work"},
            )
        assert r.status_code == 200
        assert r.json()["name"] == "Work"

    def test_create_address_stores_place_id_from_verification(self, client):
        # B9 enhancement: place_id captured during the write-time
        # geocode-verify call is persisted on the saved address row.
        db = _mock_db()
        with (
            patch("routes.addresses.db_supabase", db),
            patch(
                "routes.addresses.verify_address_matches_coordinate",
                AsyncMock(return_value=(True, None, "ChIJ_real_place_id")),
            ),
        ):
            r = client.post(
                "/api/v1/addresses",
                json={"name": "Work", "address": "456 Office Blvd", "lat": 52.2, "lng": -106.1, "icon": "work"},
            )
        assert r.status_code == 200
        assert r.json()["place_id"] == "ChIJ_real_place_id"
        inserted = db.insert_one.call_args.args[1]
        assert inserted["place_id"] == "ChIJ_real_place_id"

    def test_create_address_place_id_none_when_verification_fails_open(self, client):
        db = _mock_db()
        with (
            patch("routes.addresses.db_supabase", db),
            patch(
                "routes.addresses.verify_address_matches_coordinate",
                AsyncMock(return_value=(True, None, None)),
            ),
        ):
            r = client.post(
                "/api/v1/addresses",
                json={"name": "Home", "address": "123 Main St", "lat": 52.1, "lng": -106.0, "icon": "home"},
            )
        assert r.status_code == 200
        assert r.json()["place_id"] is None

    def test_delete_address_success(self, client):
        db = _mock_db(delete_one=AsyncMock(return_value=[{"id": "a1"}]))
        with patch("routes.addresses.db_supabase", db):
            r = client.delete("/api/v1/addresses/a1")
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_delete_address_not_found(self, client):
        db = _mock_db(delete_one=AsyncMock(return_value=[]))
        with patch("routes.addresses.db_supabase", db):
            r = client.delete("/api/v1/addresses/missing")
        assert r.status_code == 404

    def test_create_address_rejects_mismatched_coordinate(self, client):
        # B9: a confident geocode mismatch must block the save with 400,
        # not be persisted verbatim (Glide Crescent incident).
        db = _mock_db()
        with (
            patch("routes.addresses.db_supabase", db),
            patch(
                "routes.addresses.verify_address_matches_coordinate",
                AsyncMock(
                    return_value=(False, "'456 Office Blvd' geocodes 12.3 km from the supplied location", None)
                ),
            ),
        ):
            r = client.post(
                "/api/v1/addresses",
                json={"name": "Work", "address": "456 Office Blvd", "lat": 52.2, "lng": -106.1, "icon": "work"},
            )
        assert r.status_code == 400
        assert "don't match" in r.json()["detail"]
        db.insert_one.assert_not_called()


# ──────────────────────────────── favorites ──────────────────────────────────


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


class TestFavorites:
    def test_get_favorites_returns_list(self, client):
        fdb = _mock_fav_db(get_rows=AsyncMock(return_value=[FAV_ROW]))
        with patch("routes.favorites.db", fdb):
            r = client.get("/api/v1/favorites")
        assert r.status_code == 200
        assert r.json()[0]["name"] == "Airport Run"

    def test_get_favorites_error_returns_empty(self, client):
        fdb = _mock_fav_db(get_rows=AsyncMock(side_effect=RuntimeError("db down")))
        with patch("routes.favorites.db", fdb):
            r = client.get("/api/v1/favorites")
        assert r.status_code == 200
        assert r.json() == []

    def test_save_favorite_new(self, client):
        fdb = _mock_fav_db(get_rows=AsyncMock(return_value=[]))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites", json=SAVE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["name"] == "Airport Run"

    def test_save_favorite_duplicate_returns_existing(self, client):
        fdb = _mock_fav_db(get_rows=AsyncMock(return_value=[FAV_ROW]))
        with patch("routes.favorites.db", fdb):
            r = client.post("/api/v1/favorites", json=SAVE_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["id"] == "fav_1"

    def test_delete_favorite_not_found(self, client):
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=None))
        with patch("routes.favorites.db", fdb):
            r = client.delete("/api/v1/favorites/missing")
        assert r.status_code == 404

    def test_delete_favorite_wrong_owner(self, client):
        # Route filters by user_id in the DB query; wrong owner → no row returned → 404
        fdb = _mock_fav_db(find_one=AsyncMock(return_value=None))
        with patch("routes.favorites.db", fdb):
            r = client.delete("/api/v1/favorites/fav_1")
        assert r.status_code == 404

    def test_save_favorite_rejects_mismatched_dropoff(self, client):
        # B9: same confident-mismatch guard as POST /addresses, applied to
        # both pickup and dropoff — also closes the save_favorite_from_ride
        # "poisoned ride laundered into a permanent favorite" gap since that
        # route delegates to this same handler.
        fdb = _mock_fav_db(get_rows=AsyncMock(return_value=[]))

        async def fake_verify(address, lat, lng):
            if address == SAVE_PAYLOAD["dropoff_address"]:
                return False, f"'{address}' geocodes 8.0 km from the supplied location", None
            return True, None, None

        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(side_effect=fake_verify)),
        ):
            r = client.post("/api/v1/favorites", json=SAVE_PAYLOAD)
        assert r.status_code == 400
        assert "Dropoff" in r.json()["detail"]
        fdb.insert_one.assert_not_called()

    def test_save_favorite_dedupe_checks_both_axes(self, client):
        # B9 regression: the old dedupe compared latitude only, so a route
        # sharing pickup/dropoff latitude but with a completely different
        # longitude (opposite side of the city) was wrongly treated as a
        # duplicate. Same latitudes as FAV_ROW, very different longitudes.
        same_lat_different_lng = {
            "name": "Different Route",
            "pickup_address": "999 Other St",
            "pickup_lat": 52.1,
            "pickup_lng": -105.0,
            "dropoff_address": "Some Other Place",
            "dropoff_lat": 52.17,
            "dropoff_lng": -105.5,
        }
        fdb = _mock_fav_db(get_rows=AsyncMock(return_value=[FAV_ROW]))
        with (
            patch("routes.favorites.db", fdb),
            patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None))),
        ):
            r = client.post("/api/v1/favorites", json=same_lat_different_lng)
        assert r.status_code == 200
        assert r.json()["id"] != "fav_1"
        fdb.insert_one.assert_called_once()


# ──────────────────────────────── safety ─────────────────────────────────────


class TestSafety:
    def test_submit_report_success(self, client):
        db = _mock_db()
        with patch("routes.safety.db_supabase", db):
            r = client.post(
                "/api/v1/safety/report",
                json={"category": "driver_behaviour", "description": "Driver was reckless"},
            )
        assert r.status_code == 200
        body = r.json()
        assert "incident_id" in body
        assert body["success"] is True

    def test_submit_report_with_ride_context(self, client):
        db = _mock_db()
        with patch("routes.safety.db_supabase", db):
            r = client.post(
                "/api/v1/safety/report",
                json={
                    "category": "assault",
                    "description": "Physical altercation",
                    "location": {"latitude": 52.1, "longitude": -106.0},
                    "ride_context": {"ride_id": "ride_123"},
                },
            )
        assert r.status_code == 200
        assert "incident_id" in r.json()

    def test_submit_report_db_failure_raises_503(self, client):
        db = _mock_db(insert_one=AsyncMock(side_effect=Exception("db error")))
        with patch("routes.safety.db_supabase", db):
            r = client.post(
                "/api/v1/safety/report",
                json={"category": "fraud", "description": "Fraudulent charge"},
            )
        assert r.status_code == 503

    def test_submit_report_driver_party_to_ride_gets_verified_ride_id(self, driver_client):
        """WS-18: a driver reporting on a ride they actually drove gets
        verified_ride_id set on the incident — exercises the driver-side
        (not rider-side) branch of the ride-membership check."""
        driver_row = {"id": "driver_row_1", "user_id": DRIVER_USER["id"]}
        ride = {"id": "ride_9", "rider_id": "some_other_rider", "driver_id": "driver_row_1"}
        db = _mock_db(
            get_ride=AsyncMock(return_value=ride),
            get_rows=AsyncMock(return_value=[driver_row]),
        )
        captured = {}

        async def _insert(table, row):
            captured["row"] = row

        db.insert_one = AsyncMock(side_effect=_insert)
        with patch("routes.safety.db_supabase", db):
            r = driver_client.post(
                "/api/v1/safety/report",
                json={
                    "category": "unsafe_driving",
                    "description": "Rider was aggressive",
                    "ride_context": {"ride_id": "ride_9"},
                },
            )
        assert r.status_code == 200
        assert captured["row"]["ride_id"] == "ride_9"
        assert captured["row"]["role"] == "driver"

    def test_submit_report_non_party_ride_context_is_dropped(self, driver_client):
        """A caller who isn't actually a party to the referenced ride gets
        their report persisted, but ride_id is NOT attached (WS-18 anti-spoof)."""
        driver_row = {"id": "driver_row_1", "user_id": DRIVER_USER["id"]}
        ride = {"id": "ride_9", "rider_id": "some_rider", "driver_id": "a_different_driver_row"}
        db = _mock_db(
            get_ride=AsyncMock(return_value=ride),
            get_rows=AsyncMock(return_value=[driver_row]),
        )
        captured = {}

        async def _insert(table, row):
            captured["row"] = row

        db.insert_one = AsyncMock(side_effect=_insert)
        with patch("routes.safety.db_supabase", db):
            r = driver_client.post(
                "/api/v1/safety/report",
                json={
                    "category": "unsafe_driving",
                    "description": "Not actually my ride",
                    "ride_context": {"ride_id": "ride_9"},
                },
            )
        assert r.status_code == 200
        assert captured["row"]["ride_id"] is None

    def test_submit_report_notify_safety_team_failure_does_not_fail_request(self, client):
        """notify_safety_team is best-effort — the report is already
        persisted by the time it's called, so a notify failure must not
        turn an otherwise-successful report into an error response."""
        db = _mock_db()
        with (
            patch("routes.safety.db_supabase", db),
            patch("routes.safety.notify_safety_team", AsyncMock(side_effect=RuntimeError("notify down"))),
        ):
            r = client.post(
                "/api/v1/safety/report",
                json={"category": "harassment", "description": "Verbal harassment"},
            )
        assert r.status_code == 200
        assert r.json()["success"] is True


# ──────────────────────────────── disputes ───────────────────────────────────


RIDE_ROW = {
    "id": "ride_1",
    "rider_id": "user_1",
    "status": "completed",
    "total_fare": "25.50",
}

DISPUTE_ROW = {
    "id": "disp_1",
    "ride_id": "ride_1",
    "user_id": "user_1",
    "reason": "overcharged",
    "description": "Fare was higher than estimated",
    "status": "open",
    "created_at": "2026-01-01T00:00:00Z",
}


class TestDisputes:
    def test_create_dispute_success(self, client):
        db = _mock_db(
            get_ride=AsyncMock(return_value=RIDE_ROW),
            get_rows=AsyncMock(return_value=[]),
        )
        with patch("routes.disputes.db_supabase", db):
            r = client.post(
                "/api/v1/disputes",
                json={"ride_id": "ride_1", "reason": "overcharged", "description": "Fare too high"},
            )
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_create_dispute_ride_not_found(self, client):
        db = _mock_db(get_ride=AsyncMock(return_value=None))
        with patch("routes.disputes.db_supabase", db):
            r = client.post(
                "/api/v1/disputes",
                json={"ride_id": "ghost", "reason": "overcharged", "description": "x"},
            )
        assert r.status_code == 404

    def test_create_dispute_unauthorized_rider(self, client):
        other_ride = {**RIDE_ROW, "rider_id": "other_user"}
        db = _mock_db(get_ride=AsyncMock(return_value=other_ride))
        with patch("routes.disputes.db_supabase", db):
            r = client.post(
                "/api/v1/disputes",
                json={"ride_id": "ride_1", "reason": "overcharged", "description": "x"},
            )
        assert r.status_code == 403

    def test_create_dispute_ride_not_complete(self, client):
        active_ride = {**RIDE_ROW, "status": "in_progress"}
        db = _mock_db(get_ride=AsyncMock(return_value=active_ride))
        with patch("routes.disputes.db_supabase", db):
            r = client.post(
                "/api/v1/disputes",
                json={"ride_id": "ride_1", "reason": "overcharged", "description": "x"},
            )
        assert r.status_code == 400

    def test_create_dispute_already_open(self, client):
        db = _mock_db(
            get_ride=AsyncMock(return_value=RIDE_ROW),
            get_rows=AsyncMock(return_value=[DISPUTE_ROW]),
        )
        with patch("routes.disputes.db_supabase", db):
            r = client.post(
                "/api/v1/disputes",
                json={"ride_id": "ride_1", "reason": "overcharged", "description": "x"},
            )
        assert r.status_code == 400

    def test_get_user_disputes(self, client):
        db = _mock_db(get_rows=AsyncMock(return_value=[DISPUTE_ROW]))
        with patch("routes.disputes.db_supabase", db):
            r = client.get("/api/v1/disputes")
        assert r.status_code == 200
        assert r.json()[0]["id"] == "disp_1"

    def test_get_dispute_by_id(self, client):
        db = _mock_db(get_rows=AsyncMock(return_value=[DISPUTE_ROW]))
        with patch("routes.disputes.db_supabase", db):
            r = client.get("/api/v1/disputes/disp_1")
        assert r.status_code == 200
        assert r.json()["id"] == "disp_1"

    def test_get_dispute_not_found(self, client):
        db = _mock_db(get_rows=AsyncMock(return_value=[]))
        with patch("routes.disputes.db_supabase", db):
            r = client.get("/api/v1/disputes/missing")
        assert r.status_code == 404

    def test_get_dispute_wrong_owner(self, client):
        other_dispute = {**DISPUTE_ROW, "user_id": "other_user"}
        db = _mock_db(get_rows=AsyncMock(return_value=[other_dispute]))
        with patch("routes.disputes.db_supabase", db):
            r = client.get("/api/v1/disputes/disp_1")
        assert r.status_code == 403
