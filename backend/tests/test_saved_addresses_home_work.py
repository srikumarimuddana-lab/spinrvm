"""Saved addresses: one Home / one Work, PATCH, ordering, validation (2026-09-25).

Runs the real routes/addresses.py -> db_supabase -> repositories/_base.py
path on top of conftest's autouse ``mock_supabase_client``, so the filters
asserted below are the ones actually compiled into the PostgREST query
chain — not a hand-mocked db_supabase that would accept any filter.

Fixture values are synthetic (no real addresses or coordinates).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIDER = {"id": "user_1", "role": "rider", "phone": "+10000000000"}
PAYLOAD = {"name": "Home", "address": "1 Test Street", "lat": 50.0, "lng": -100.0, "icon": "home"}


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
def table(mock_supabase_client):
    """Make the shared mock table chainable for writes and record per-op data."""
    t = mock_supabase_client.table.return_value
    state = {"op": None}
    responses = {"select": [], "update": [], "delete": [], "insert": []}

    def _op(name):
        def _f(*_a, **_k):
            state["op"] = name
            return t

        return _f

    for name in responses:
        getattr(t, name).side_effect = _op(name)

    def _execute():
        r = MagicMock()
        r.data = responses[state["op"]]
        r.count = len(r.data)
        return r

    t.execute.side_effect = _execute
    t.responses = responses
    return t


def _patch_verify(result):
    """Patch the verifier on whichever module path the app imported."""
    import importlib

    patches = []
    for mod_path in ("routes.addresses", "backend.routes.addresses"):
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            continue
        p = patch.object(mod, "verify_address_matches_coordinate", AsyncMock(return_value=result))
        p.start()
        patches.append(p)
    return patches


@pytest.fixture(autouse=True)
def verify_ok():
    """Default: the geocode verifier fails open with no place_id (no network)."""
    patches = _patch_verify((True, None, None))
    yield
    for p in patches:
        p.stop()


class TestSecondHomeReplacesFirst:
    def test_second_home_updates_existing_row_in_place(self, client, table):
        table.responses["update"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        assert r.json()["id"] == "old_home"  # id kept
        table.update.assert_called_once()
        table.insert.assert_not_called()
        # Update scoped to this rider's Home rows only.
        assert call("user_id", "user_1") in table.eq.call_args_list
        assert call("icon", "home") in table.eq.call_args_list
        # Any leftover duplicate Home is removed, never the kept row.
        table.delete.assert_called_once()
        table.neq.assert_called_with("id", "old_home")

    def test_first_home_inserts(self, client, table):
        table.responses["update"] = []
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        table.insert.assert_called_once()
        table.delete.assert_not_called()

    def test_home_detected_by_label_when_untyped(self, client, table):
        # Legacy/untyped icon ("location") falls back to the label, and the
        # stored icon is normalised to the type so the next save finds it.
        r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "home", "icon": "location"})
        assert r.status_code == 200
        table.update.assert_called_once()
        assert table.update.call_args.args[0]["icon"] == "home"
        assert r.json()["icon"] == "home"

    def test_typed_icon_wins_over_label(self, client, table):
        # "My house" typed as Home -> Home. "Home" typed as Gym -> not Home.
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "My house", "icon": "home"})
        table.update.assert_called_once()
        table.update.reset_mock()
        table.insert.reset_mock()
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Home", "icon": "gym"})
        table.update.assert_not_called()
        table.insert.assert_called_once()

    def test_non_singleton_labels_can_repeat(self, client, table):
        for _ in range(2):
            r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Gym", "icon": "gym"})
            assert r.status_code == 200
        table.update.assert_not_called()
        assert table.insert.call_count == 2

    def test_work_replaces_work_not_home(self, client, table):
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Work", "icon": "work"})
        assert call("icon", "work") in table.eq.call_args_list
        assert call("icon", "home") not in table.eq.call_args_list


class TestPlaceId:
    def test_client_place_id_stored_when_verification_has_none(self, client, table):
        r = client.post(
            "/api/v1/addresses", json={**PAYLOAD, "icon": "other", "name": "Cafe", "place_id": "client_pid"}
        )
        assert r.status_code == 200
        assert table.insert.call_args.args[0]["place_id"] == "client_pid"

    def test_server_place_id_wins(self, client, table):
        patches = _patch_verify((True, None, "server_pid"))
        try:
            r = client.post(
                "/api/v1/addresses", json={**PAYLOAD, "icon": "other", "name": "Cafe", "place_id": "client_pid"}
            )
        finally:
            for p in patches:
                p.stop()
        assert r.json()["place_id"] == "server_pid"


class TestValidation:
    @pytest.mark.parametrize(
        "override",
        [{"lat": 91}, {"lat": -90.5}, {"lng": 181}, {"lng": -180.01}, {"icon": "rocket"}],
    )
    def test_rejects_out_of_range_or_unknown_icon(self, client, table, override):
        r = client.post("/api/v1/addresses", json={**PAYLOAD, **override})
        assert r.status_code == 422
        table.insert.assert_not_called()
        table.update.assert_not_called()

    def test_icon_is_case_insensitive(self, client, table):
        r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Cafe", "icon": "School"})
        assert r.status_code == 200
        assert table.insert.call_args.args[0]["icon"] == "school"

    def test_mismatch_message_does_not_echo_address(self, client, table):
        patches = _patch_verify((False, "'1 Test Street' geocodes 12.3 km from the supplied location", None))
        try:
            r = client.post("/api/v1/addresses", json=PAYLOAD)
        finally:
            for p in patches:
                p.stop()
        assert r.status_code == 400
        assert "don't match" in r.json()["detail"]
        assert "1 Test Street" not in r.json()["detail"]
        table.insert.assert_not_called()


class TestOrdering:
    def test_get_orders_by_created_at(self, client, table):
        table.responses["select"] = [{"id": "a"}, {"id": "b"}]
        r = client.get("/api/v1/addresses")
        assert r.status_code == 200
        assert [a["id"] for a in r.json()] == ["a", "b"]
        table.order.assert_called_with("created_at", desc=False)


class TestPatch:
    def test_patch_other_users_row_is_404(self, client, table):
        table.responses["select"] = []  # owner-scoped lookup finds nothing
        r = client.patch("/api/v1/addresses/someone_elses", json={"name": "Mine now"})
        assert r.status_code == 404
        table.update.assert_not_called()
        assert call("user_id", "user_1") in table.eq.call_args_list

    def test_patch_renames_own_row(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "name": "Coffee"}]
        r = client.patch("/api/v1/addresses/a1", json={"name": "Coffee"})
        assert r.status_code == 200
        assert r.json()["name"] == "Coffee"
        assert table.update.call_args.args[0] == {"name": "Coffee"}
        table.delete.assert_not_called()

    def test_patch_to_home_drops_previous_home(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "icon": "home"}]
        r = client.patch("/api/v1/addresses/a1", json={"icon": "home"})
        assert r.status_code == 200
        table.delete.assert_called_once()
        table.neq.assert_called_with("id", "a1")

    def test_patch_location_requires_all_three(self, client, table):
        table.responses["select"] = [{"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}]
        r = client.patch("/api/v1/addresses/a1", json={"lat": 50.1})
        assert r.status_code == 422
        table.update.assert_not_called()

    def test_patch_location_is_verified_and_stores_place_id(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [row]
        r = client.patch(
            "/api/v1/addresses/a1",
            json={"address": "2 Test Street", "lat": 50.1, "lng": -100.1, "place_id": "client_pid"},
        )
        assert r.status_code == 200
        sent = table.update.call_args.args[0]
        assert sent["lat"] == 50.1 and sent["lng"] == -100.1
        assert sent["place_id"] == "client_pid"

    def test_patch_rejects_bad_lat(self, client, table):
        r = client.patch("/api/v1/addresses/a1", json={"address": "2 Test Street", "lat": 95, "lng": 0})
        assert r.status_code == 422

    def test_patch_empty_body_is_422(self, client, table):
        table.responses["select"] = [{"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}]
        r = client.patch("/api/v1/addresses/a1", json={})
        assert r.status_code == 422
