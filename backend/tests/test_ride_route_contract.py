"""Contract tests for the versioned route included in authorized ride detail."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.repositories import ride_repo


def _run(coroutine):
    return asyncio.run(coroutine)


class _RouteQuery:
    def __init__(self, table, rows_by_table):
        self.table = table
        self.rows_by_table = rows_by_table

    def select(self, _columns):
        return self

    def eq(self, _column, _value):
        return self

    def is_(self, _column, _value):
        return self

    def order(self, _column, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows_by_table.get(self.table, []))


class _RouteSupabase:
    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.tables_requested = []

    def table(self, name):
        self.tables_requested.append(name)
        return _RouteQuery(name, self.rows_by_table)


def test_ride_detail_projects_matched_v2_segments_without_legacy_geometry(monkeypatch):
    matched = [{"provider": "osrm_match", "coordinates": [[50.45, -104.62], [50.46, -104.63]]}]
    observed = [{"coordinates": [[50.45, -104.62], [50.47, -104.64]]}]
    client = _RouteSupabase(
        {
            "rides": [{"id": "ride_1", "status": "completed"}],
            "ride_routes": [
                {
                    "ride_id": "ride_1",
                    "route_schema_version": 2,
                    "road_matched_segments": matched,
                    "observed_segments": observed,
                    "route_quality": {"coverage_ratio": 0.91, "missing_tail": False},
                    "route_revision": 4,
                    "processing_status": "complete",
                    "snapshot_object_path": "ride_1/route-v4.png",
                    "snapshot_revision": 4,
                    "road_polyline": [[1, 2]],
                }
            ],
        }
    )

    async def _run_sync(operation):
        return operation()

    monkeypatch.setattr(ride_repo, "supabase", client)
    monkeypatch.setattr(ride_repo, "run_sync", _run_sync)
    monkeypatch.setattr(
        ride_repo,
        "create_route_snapshot_signed_url",
        AsyncMock(return_value="https://storage.example/signed/ride-v4.png"),
    )

    ride = _run(ride_repo.get_ride("ride_1", include_route=True))

    assert ride["actual_route_segments"] == matched
    assert ride["route_quality"]["coverage_ratio"] == 0.91
    assert ride["route_schema_version"] == 2
    assert ride["route_revision"] == 4
    assert ride["snapshot_revision"] == 4
    assert ride["route_geometry_status"] == "complete"
    assert ride["route_snapshot_url"].endswith("ride-v4.png")
    assert "road_polyline" not in ride
    assert client.tables_requested == ["rides", "ride_routes"]


def test_ride_detail_uses_observed_v2_segments_when_matching_is_unavailable(monkeypatch):
    observed = [{"boundary_reason": "time_gap", "coordinates": [[50.45, -104.62], [50.47, -104.64]]}]
    client = _RouteSupabase(
        {
            "rides": [{"id": "ride_1", "status": "completed"}],
            "ride_routes": [
                {
                    "ride_id": "ride_1",
                    "route_schema_version": 2,
                    "road_matched_segments": [],
                    "observed_segments": observed,
                    "route_quality": {"coverage_ratio": 0.54, "missing_tail": True},
                    "route_revision": 5,
                    "processing_status": "incomplete",
                    "snapshot_url": "https://maps.example/ride-v4.png",
                    "snapshot_revision": 4,
                }
            ],
        }
    )

    async def _run_sync(operation):
        return operation()

    monkeypatch.setattr(ride_repo, "supabase", client)
    monkeypatch.setattr(ride_repo, "run_sync", _run_sync)

    ride = _run(ride_repo.get_ride("ride_1", include_route=True))

    assert ride["actual_route_segments"] == observed
    assert ride["route_geometry_status"] == "incomplete"
    assert "route_snapshot_url" not in ride


def test_ride_detail_keeps_legacy_route_fields_only_for_legacy_rows(monkeypatch):
    client = _RouteSupabase(
        {
            "rides": [{"id": "ride_legacy", "status": "completed"}],
            "ride_routes": [
                {
                    "ride_id": "ride_legacy",
                    "route_schema_version": 1,
                    "road_polyline": [[50.45, -104.62], [50.46, -104.63]],
                    "phase_polylines": {"trip": [[50.45, -104.62], [50.46, -104.63]]},
                }
            ],
        }
    )

    async def _run_sync(operation):
        return operation()

    monkeypatch.setattr(ride_repo, "supabase", client)
    monkeypatch.setattr(ride_repo, "run_sync", _run_sync)

    ride = _run(ride_repo.get_ride("ride_legacy", include_route=True))

    assert ride["road_polyline"] == [[50.45, -104.62], [50.46, -104.63]]
    assert "actual_route_segments" not in ride


def test_admin_detail_projects_the_same_v2_route_contract(monkeypatch):
    matched = [{"coordinates": [[50.45, -104.62], [50.46, -104.63]]}]
    client = _RouteSupabase(
        {
            "rides": [{"id": "ride_admin", "status": "completed"}],
            "ride_routes": [
                {
                    "ride_id": "ride_admin",
                    "route_schema_version": 2,
                    "road_matched_segments": matched,
                    "route_quality": {"coverage_ratio": 0.88, "missing_tail": True},
                    "route_revision": 7,
                    "processing_status": "incomplete",
                    "snapshot_revision": 6,
                    "road_polyline": [[50.45, -104.62], [50.46, -104.63]],
                }
            ],
        }
    )

    async def _run_sync(operation):
        return operation()

    monkeypatch.setattr(ride_repo, "supabase", client)
    monkeypatch.setattr(ride_repo, "run_sync", _run_sync)

    ride = _run(ride_repo.get_ride_details_enriched("ride_admin"))

    assert ride["actual_route_segments"] == matched
    assert ride["route_geometry_status"] == "incomplete"
    assert ride["snapshot_revision"] == 6
    assert "road_polyline" not in ride


def test_list_queries_do_not_load_route_geometry(monkeypatch):
    client = _RouteSupabase({"rides": [{"id": "ride_1", "status": "completed"}]})

    async def _run_sync(operation):
        return operation()

    monkeypatch.setattr(ride_repo, "supabase", client)
    monkeypatch.setattr(ride_repo, "run_sync", _run_sync)

    rides = _run(ride_repo.get_rides_for_user("rider_1"))

    assert rides == [{"id": "ride_1", "status": "completed"}]
    assert client.tables_requested == ["rides"]
