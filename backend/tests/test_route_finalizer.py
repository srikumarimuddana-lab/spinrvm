# ruff: noqa: E402, I001
"""Contract tests for replay-safe, non-billing route finalization."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

from backend.utils import route_finalizer


def _run(coroutine):
    return asyncio.run(coroutine)


def _ride() -> dict:
    return {
        "id": "ride_1",
        "ride_started_at": "2026-07-17T21:00:00Z",
        "ride_completed_at": "2026-07-17T21:10:00Z",
        "actual_distance_km": 4.04,
    }


def _point(sequence: int, seconds: int) -> dict:
    return {
        "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
        "sequence_number": sequence,
        "captured_at": (datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat(),
        "lat": 50.445 + sequence * 0.0001,
        "lng": -104.618,
        "accuracy": 8,
    }


def _route_row(**overrides) -> dict:
    route = {
        "ride_id": "ride_1",
        "route_revision": 3,
        "completion_point": _point(2, 600),
        "retry_count": 0,
        "processing_status": "processing",
        "processing_claimed_at": "2026-07-17T21:11:00+00:00",
    }
    route.update(overrides)
    return route


def test_mark_route_pending_upserts_versioned_completion_metadata(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)

    _run(route_finalizer.mark_route_pending("ride_1", {"missing_tail": True}))

    update.assert_awaited_once()
    args, kwargs = update.await_args
    assert args[:2] == ("ride_routes", {"ride_id": "ride_1"})
    assert args[2]["route_schema_version"] == 2
    assert args[2]["processing_status"] == "pending"
    assert args[2]["completion_point"] == {"missing_tail": True}
    assert args[2]["snapshot_revision"] == 0
    assert args[2]["snapshot_object_path"] is None
    assert args[2]["snapshot_url"] is None
    assert args[2]["finalized_at"] is None
    assert kwargs["upsert"] is True


def test_finalizer_matches_only_phase_3_coordinates_and_writes_a_new_revision(monkeypatch):
    updates = []
    publish_snapshot = AsyncMock()
    observed = [_point(2, 10), _point(0, -5), _point(1, 0), _point(4, 601), _point(3, 600)]

    async def get_rows(table, _filters, **kwargs):
        assert table == "driver_location_history"
        assert kwargs["order"] == "captured_at"
        return observed

    async def get_ride(_ride_id):
        return _ride()

    async def update_one(*args, **kwargs):
        updates.append((args, kwargs))
        return {"ride_id": "ride_1"}

    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", get_ride)
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update_one)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    road_match = AsyncMock(
        return_value={
            "segments": [
                {
                    "matched_segments": [
                        {
                            "provider": "osrm_match",
                            "distance_km": 1.5,
                            "polyline": [[50.445, -104.618], [50.446, -104.618]],
                        }
                    ]
                }
            ],
            "distance_km": 1.5,
            "provider": "osrm_match",
            "failures": [],
        }
    )
    monkeypatch.setattr(route_finalizer, "compute_segmented_road_route", road_match)

    result = _run(route_finalizer.finalize_route("ride_1"))

    assert result["processing_status"] == "complete"
    payload = updates[-1][0][2]
    assert payload["route_revision"] == 4
    assert payload["processing_status"] == "complete"
    assert payload["route_quality"]["distance_provider"] == "osrm_match"
    assert payload["observed_segments"][0]["coordinates"][0] == [50.4451, -104.618]
    assert payload["road_matched_segments"][0]["coordinates"] == [[50.445, -104.618], [50.446, -104.618]]
    matched_points = [point for segment in road_match.await_args.args[0] for point in segment.points]
    assert [point["sequence_number"] for point in matched_points] == [1, 2, 3]
    publish_snapshot.assert_awaited_once()
    snapshot_args = publish_snapshot.await_args.args
    assert snapshot_args[0] == "ride_1"
    assert snapshot_args[2] == 4
    assert snapshot_args[3] == payload["road_matched_segments"]
    assert snapshot_args[4] == payload["route_quality"]
    assert isinstance(publish_snapshot.await_args.kwargs["finalized_at"], datetime)


def test_finalizer_does_not_publish_when_a_late_batch_supersedes_its_claim(monkeypatch):
    updates = []
    publish_snapshot = AsyncMock()

    async def update_one(_table, filters, payload, **_kwargs):
        updates.append((filters, payload))
        # A late batch changed the row from processing back to pending before
        # this worker could persist its projection.
        return None

    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update_one)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": None, "failures": []}),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    assert result["processing_status"] == "superseded"
    assert updates[0][0] == {
        "ride_id": "ride_1",
        "processing_status": "processing",
        "processing_claimed_at": "2026-07-17T21:11:00+00:00",
    }
    publish_snapshot.assert_not_awaited()


def test_finalizer_marks_missing_tail_incomplete_without_mutating_fare(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", AsyncMock())
    monkeypatch.setattr(
        route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row(completion_point={"missing_tail": True}))
    )
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": None, "failures": []}),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    assert result["processing_status"] == "incomplete"
    payload = update.await_args.args[2]
    assert payload["route_quality"]["missing_tail"] is True
    assert "actual_distance_km" not in payload
    assert "distance_km" not in payload


def test_single_point_route_is_incomplete_and_does_not_publish_snapshot(monkeypatch):
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    publish_snapshot = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 590)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(
            return_value={
                "segments": [],
                "distance_km": 0.0,
                "provider": None,
                "failures": [{"segment_index": 0, "reason": "insufficient_points"}],
            }
        ),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    payload = update.await_args.args[2]
    assert result["processing_status"] == "incomplete"
    assert payload["route_quality"]["incomplete_reason"] == "insufficient_route_points"
    assert payload["road_matched_segments"][0]["coordinates"] == [[50.445, -104.618]]
    publish_snapshot.assert_not_awaited()


def test_provider_unavailable_keeps_a_drawable_observed_segment_complete(monkeypatch):
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", AsyncMock())
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(
            return_value={
                "segments": [{"segment_index": 0, "matched_segments": []}],
                "distance_km": 0.0,
                "provider": None,
                "failures": [{"segment_index": 0, "chunk_index": 0, "reason": "provider_unavailable"}],
            }
        ),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    payload = update.await_args.args[2]
    assert result["processing_status"] == "complete"
    assert payload["route_quality"]["incomplete_reason"] is None
    assert payload["road_matched_segments"] == [
        {
            "source_segment_index": 0,
            "provider": "observed_fallback",
            "phase": "trip_in_progress",
            "coordinates": [[50.445, -104.618], [50.4451, -104.618]],
        }
    ]
    assert len(payload["road_matched_segments"][0]["coordinates"]) == 2


def test_provider_failure_schedules_a_retry_without_exposing_coordinates(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=_ride()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row(retry_count=2)))
    monkeypatch.setattr(
        route_finalizer, "compute_segmented_road_route", AsyncMock(side_effect=RuntimeError("provider down"))
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    assert result["processing_status"] == "pending"
    payload = update.await_args.args[2]
    assert payload["retry_count"] == 3
    assert payload["next_retry_at"] is not None
    assert payload["route_quality"]["finalization_reason"] == "provider_failure"


def test_finalizer_persists_reconstructed_sections_and_distance_quality(monkeypatch):
    updates = []
    reconstructed = {
        "segments": [
            {
                "id": "inferred-missing_start-1",
                "provider": "osrm_inferred",
                "geometry_kind": "inferred",
                "gap_reason": "missing_start",
                "distance_km": 1.25,
                "coordinates": [[50.45, -104.62], [50.46, -104.63]],
            }
        ],
        "distance_km": 1.25,
        "observed_distance_km": 0.75,
        "inferred_distance_km": 0.5,
        "observed_distance_ratio": 0.6,
        "inferred_distance_ratio": 0.4,
        "inferred_gap_count": 1,
        "endpoint_start_verified": True,
        "endpoint_end_verified": True,
        "failed_gaps": [],
    }

    async def update_one(table, filters, payload, **_kwargs):
        updates.append((table, filters, payload))
        return {"ride_id": "ride_1"}

    ride = {**_ride(), "pickup_lat": 50.45, "pickup_lng": -104.62}
    reconstruct = AsyncMock(return_value=reconstructed)
    recompute = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=ride))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update_one)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row()))
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", AsyncMock())
    monkeypatch.setattr(route_finalizer, "_recompute_ride_distance_stats", recompute)
    monkeypatch.setattr(route_finalizer, "reconstruct_completed_route", reconstruct, raising=False)
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": "osrm_match", "failures": []}),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    route_update = next(payload for table, _filters, payload in updates if table == "ride_routes")
    assert result["processing_status"] == "complete"
    assert route_update["road_matched_segments"] == reconstructed["segments"]
    assert route_update["route_quality"]["observed_distance_ratio"] == 0.6
    assert route_update["route_quality"]["inferred_distance_ratio"] == 0.4
    assert route_update["route_quality"]["inferred_gap_count"] == 1
    assert route_update["route_quality"]["matched_distance_km"] == 0.75
    reconstruct.assert_awaited_once()
    assert reconstruct.await_args.args[2] == {"lat": 50.45, "lng": -104.62}
    # The finalizer now passes the whole reconstruction dict + coverage to the
    # recompute (which resolves observed + routed-connector distance itself),
    # not the observed-only float it used to.
    recompute.assert_awaited_once()
    _rc_args, _rc_kwargs = recompute.await_args
    assert _rc_args[0] == "ride_1"
    assert _rc_args[2] == 4
    assert _rc_kwargs["reconstructed"] == reconstructed
    assert isinstance(_rc_kwargs["coverage"], float)


def _failed_reconstruction() -> dict:
    return {
        "segments": [
            {
                "id": "observed-0-0",
                "provider": "observed_fallback",
                "geometry_kind": "observed",
                "gap_reason": None,
                "distance_km": 0.2,
                "coordinates": [[50.45, -104.62], [50.451, -104.621]],
            }
        ],
        "distance_km": 0.2,
        "observed_distance_km": 0.2,
        "inferred_distance_km": 0.0,
        "observed_distance_ratio": 1.0,
        "inferred_distance_ratio": 0.0,
        "inferred_gap_count": 0,
        "endpoint_start_verified": True,
        "endpoint_end_verified": True,
        "failed_gaps": ["internal_gap"],
    }


def test_failed_reconstruction_stays_pending_without_stats_or_snapshot(monkeypatch):
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    recompute = AsyncMock()
    publish_snapshot = AsyncMock()
    ride = {**_ride(), "pickup_lat": 50.45, "pickup_lng": -104.62}
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=ride))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row(retry_count=0)))
    monkeypatch.setattr(route_finalizer, "_recompute_ride_distance_stats", recompute)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(
        route_finalizer, "reconstruct_completed_route", AsyncMock(return_value=_failed_reconstruction())
    )
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": "osrm_match", "failures": []}),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    payload = update.await_args.args[2]
    assert result["processing_status"] == "pending"
    assert payload["processing_status"] == "pending"
    assert payload["retry_count"] == 1
    assert payload["next_retry_at"] is not None
    assert payload["finalized_at"] is None
    assert payload["snapshot_revision"] == 0
    assert payload["snapshot_object_path"] is None
    assert payload["route_quality"]["reconstruction_status"] == "retrying"
    assert payload["route_quality"]["failed_gaps"] == ["internal_gap"]
    recompute.assert_not_awaited()
    publish_snapshot.assert_not_awaited()


def test_failed_reconstruction_becomes_terminal_after_retry_budget(monkeypatch):
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    recompute = AsyncMock()
    publish_snapshot = AsyncMock()
    ride = {**_ride(), "pickup_lat": 50.45, "pickup_lng": -104.62}
    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", AsyncMock(return_value=[_point(0, 0), _point(1, 10)]))
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=ride))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row(retry_count=4)))
    monkeypatch.setattr(route_finalizer, "_recompute_ride_distance_stats", recompute)
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", publish_snapshot)
    monkeypatch.setattr(
        route_finalizer, "reconstruct_completed_route", AsyncMock(return_value=_failed_reconstruction())
    )
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": "osrm_match", "failures": []}),
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    payload = update.await_args.args[2]
    assert result["processing_status"] == "incomplete"
    assert payload["processing_status"] == "incomplete"
    assert payload["retry_count"] == 5
    assert payload["next_retry_at"] is None
    assert payload["route_quality"]["reconstruction_status"] == "failed"
    assert payload["snapshot_revision"] == 0
    assert payload["snapshot_object_path"] is None
    recompute.assert_not_awaited()
    publish_snapshot.assert_not_awaited()


def test_recomputed_statistics_use_reconstructed_distance_without_touching_fare(monkeypatch):
    updates = []
    inserts = []
    distances = SimpleNamespace(
        actual_distance_km=0.4,
        actual_distance_km_haversine=0.38,
        actual_distance_km_road=0.4,
        phase_distances={"trip_in_progress": 0.4},
        phase_durations={"trip_in_progress": 180},
        pickup_to_driver_km=0.2,
        gps_points_count=4,
    )

    async def update_one(table, filters, payload, **_kwargs):
        updates.append((table, filters, payload))
        return {"id": "ride_1"}

    async def insert_one(table, payload):
        inserts.append((table, payload))
        return payload

    ride = {
        **_ride(),
        "status": "completed",
        "total_fare": "8.00",
        "fare_breakdown_snapshot": {"locked": True},
    }
    monkeypatch.setattr(route_finalizer, "load_ride_breadcrumbs", AsyncMock(return_value=[]))
    monkeypatch.setattr(route_finalizer, "compute_trip_distances", AsyncMock(return_value=distances))
    monkeypatch.setattr(route_finalizer, "get_app_settings", AsyncMock(return_value={"fare_lock_enabled": False}))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update_one)
    monkeypatch.setattr(route_finalizer.db_supabase, "insert_one", insert_one)

    # New signature: pass the reconstruction dict + coverage; the resolver
    # returns observed + routed-connector distance (1.55 here) as the measured
    # value. No pickup/dropoff on this fixture → straight-line floor is skipped.
    _reconstructed = {
        "observed_distance_km": 1.55,
        "routed_connector_distance_km": 0.0,
        "straight_connector_distance_km": 0.0,
    }
    _run(route_finalizer._recompute_ride_distance_stats("ride_1", ride, 4, reconstructed=_reconstructed, coverage=0.9))

    ride_update = next(payload for table, _filters, payload in updates if table == "rides")
    assert ride_update["actual_distance_km"] == 1.55
    assert ride_update["phase_distances"]["trip_in_progress"] == 1.55
    assert ride_update["ride_metrics"]["phases"]["trip_in_progress"]["actual_distance_km"] == 1.55
    for forbidden in ("total_fare", "fare_breakdown_snapshot", "tax_amount", "driver_earnings", "payment_status"):
        assert forbidden not in ride_update
    assert inserts[0][0] == "ride_distance_recomputes"
    assert inserts[0][1]["new_actual_distance_km"] == 1.55


# ---------------------------------------------------------------------------
# Booked-dropoff tail anchor (route_booked_dropoff_anchor_enabled)
# ---------------------------------------------------------------------------


def _ride_with_endpoints() -> dict:
    return {
        **_ride(),
        "pickup_lat": 50.45,
        "pickup_lng": -104.62,
        "dropoff_lat": 50.46,
        "dropoff_lng": -104.63,
    }


def _reconstruction_ok() -> dict:
    return {
        "segments": [
            {
                "id": "inferred-missing_tail-1",
                "provider": "osrm_inferred",
                "geometry_kind": "inferred",
                "gap_reason": "missing_tail",
                "distance_km": 0.8,
                "coordinates": [[50.45, -104.62], [50.46, -104.63]],
            }
        ],
        "distance_km": 1.0,
        "observed_distance_km": 0.2,
        "inferred_distance_km": 0.8,
        "routed_connector_distance_km": 0.8,
        "straight_connector_distance_km": 0.0,
        "observed_distance_ratio": 0.2,
        "inferred_distance_ratio": 0.8,
        "inferred_gap_count": 1,
        "endpoint_start_verified": True,
        "endpoint_end_verified": True,
        "failed_gaps": [],
    }


def _anchor_case(monkeypatch, *, settings, completion_point=None, ride=None, points=None):
    """Shared wiring for the tail-anchor tests; returns (update, reconstruct)."""
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    reconstruct = AsyncMock(return_value=_reconstruction_ok())
    monkeypatch.setattr(
        route_finalizer.db_supabase,
        "get_rows",
        AsyncMock(return_value=[_point(0, 0), _point(1, 10)] if points is None else points),
    )
    monkeypatch.setattr(route_finalizer.db_supabase, "get_ride", AsyncMock(return_value=ride or _ride_with_endpoints()))
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)
    monkeypatch.setattr(
        route_finalizer, "_get_route_row", AsyncMock(return_value=_route_row(completion_point=completion_point))
    )
    monkeypatch.setattr(route_finalizer, "_publish_finalized_snapshot", AsyncMock())
    monkeypatch.setattr(route_finalizer, "_recompute_ride_distance_stats", AsyncMock())
    monkeypatch.setattr(route_finalizer, "reconstruct_completed_route", reconstruct, raising=False)
    monkeypatch.setattr(route_finalizer, "get_app_settings", settings, raising=False)
    monkeypatch.setattr(
        route_finalizer,
        "compute_segmented_road_route",
        AsyncMock(return_value={"segments": [], "distance_km": 0.0, "provider": "osrm_match", "failures": []}),
    )
    return update, reconstruct


def test_missing_completion_fix_skips_reconstruction_when_flag_off(monkeypatch):
    update, reconstruct = _anchor_case(monkeypatch, settings=AsyncMock(return_value={}))

    result = _run(route_finalizer.finalize_route("ride_1"))

    reconstruct.assert_not_awaited()
    payload = update.await_args.args[2]
    assert result["processing_status"] == "incomplete"
    assert payload["route_quality"]["incomplete_reason"] == "missing_completion_fix"
    assert "completion_anchor_source" not in payload["route_quality"]


def test_missing_completion_fix_anchors_to_booked_dropoff_when_flag_on(monkeypatch):
    update, reconstruct = _anchor_case(
        monkeypatch, settings=AsyncMock(return_value={"route_booked_dropoff_anchor_enabled": True})
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    reconstruct.assert_awaited_once()
    assert reconstruct.await_args.args[2] == {"lat": 50.45, "lng": -104.62}
    assert reconstruct.await_args.args[3] == {"lat": 50.46, "lng": -104.63}
    payload = update.await_args.args[2]
    assert result["processing_status"] == "complete"
    assert payload["road_matched_segments"] == _reconstruction_ok()["segments"]
    assert payload["route_quality"]["completion_anchor_source"] == "booked_dropoff"
    assert payload["route_quality"]["incomplete_reason"] is None


def test_completion_fix_present_wins_over_booked_dropoff_and_records_provenance(monkeypatch):
    fix = _point(2, 600)
    update, reconstruct = _anchor_case(
        monkeypatch,
        settings=AsyncMock(return_value={"route_booked_dropoff_anchor_enabled": True}),
        completion_point=fix,
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    reconstruct.assert_awaited_once()
    assert reconstruct.await_args.args[3] == fix
    payload = update.await_args.args[2]
    assert result["processing_status"] == "complete"
    assert payload["route_quality"]["completion_anchor_source"] == "completion_fix"


def test_flag_on_without_observed_evidence_never_fabricates_a_route(monkeypatch):
    update, reconstruct = _anchor_case(
        monkeypatch,
        settings=AsyncMock(return_value={"route_booked_dropoff_anchor_enabled": True}),
        points=[],
    )

    result = _run(route_finalizer.finalize_route("ride_1"))

    reconstruct.assert_not_awaited()
    assert result["processing_status"] == "incomplete"
    payload = update.await_args.args[2]
    assert "completion_anchor_source" not in payload["route_quality"]


def test_anchor_flag_read_failure_falls_back_to_legacy_fragments(monkeypatch):
    update, reconstruct = _anchor_case(monkeypatch, settings=AsyncMock(side_effect=RuntimeError("settings store down")))

    result = _run(route_finalizer.finalize_route("ride_1"))

    reconstruct.assert_not_awaited()
    payload = update.await_args.args[2]
    assert result["processing_status"] == "incomplete"
    assert payload["route_quality"]["incomplete_reason"] == "missing_completion_fix"
