"""Contract tests for the durable final location supplied at ride completion."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routes.drivers import ride_complete
from utils.breadcrumbs import LocationBatchAck, LocationBatchPersistResult


def _run(coroutine):
    return asyncio.run(coroutine)


def _ride(**overrides) -> dict:
    ride = {
        "id": "ride_1",
        "status": "in_progress",
        "driver_accepted_at": "2026-07-17T21:00:00Z",
        "ride_started_at": "2026-07-17T21:05:00Z",
        "dropoff_lat": 50.445,
        "dropoff_lng": -104.618,
    }
    ride.update(overrides)
    return ride


def _request(**overrides):
    captured_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "completion_fix": {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 10,
            "captured_at": captured_at,
            "lat": 50.445,
            "lng": -104.618,
            "accuracy": 8,
            "is_completion_fix": True,
        },
        "final_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
        "final_sequence_number": 10,
        "pending_outbox_count": 0,
    }
    payload.update(overrides)
    return ride_complete.RideCompletionRequest.model_validate(payload)


def _persist_result() -> LocationBatchPersistResult:
    return LocationBatchPersistResult(
        ack=LocationBatchAck(
            recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            acked_through=10,
            accepted_count=1,
            rejected=(),
        ),
        inserted_count=1,
    )


def _install_dependencies(monkeypatch: pytest.MonkeyPatch, *, mode: str = "shadow", has_v2_evidence: bool = False):
    persist = AsyncMock(return_value=_persist_result())
    update = AsyncMock()
    monkeypatch.setattr(ride_complete, "persist_trip_location_batch", persist)
    monkeypatch.setattr(ride_complete.db_supabase, "update_one", update)

    async def route_mode():
        return mode

    async def v2_evidence(_ride_id):
        return has_v2_evidence

    monkeypatch.setattr(ride_complete, "_get_route_integrity_mode", route_mode)
    monkeypatch.setattr(ride_complete, "_ride_has_v2_evidence", v2_evidence)
    return persist, update


def test_completion_fix_is_persisted_and_acked_before_route_metadata(monkeypatch: pytest.MonkeyPatch):
    events = []
    persist, update = _install_dependencies(monkeypatch)

    async def persist_side_effect(*args, **kwargs):
        events.append("persist")
        assert args[:3] == ("driver_1", "ride_1", "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f")
        assert kwargs["active_ride"]["id"] == "ride_1"
        assert args[3][0]["is_completion_fix"] is True
        return _persist_result()

    async def update_side_effect(*args, **kwargs):
        events.append("route")

    persist.side_effect = persist_side_effect
    update.side_effect = update_side_effect

    outcome = _run(ride_complete.prepare_completion_location(_ride(), "driver_1", _request()))

    assert outcome.location_ack == _persist_result().ack.to_dict()
    assert outcome.legacy_client_missing_tail is False
    assert outcome.distance_band == "at_destination"
    assert events == ["persist", "route"]


@pytest.mark.parametrize(
    ("latitude", "expected_band"),
    [
        (50.446, "at_destination"),
        (50.450, "near_destination"),
        (50.460, "off_route"),
    ],
)
def test_completion_distance_bands_use_200m_and_1km_thresholds(
    monkeypatch: pytest.MonkeyPatch, latitude: float, expected_band: str
):
    _install_dependencies(monkeypatch)

    outcome = _run(
        ride_complete.prepare_completion_location(
            _ride(), "driver_1", _request(completion_fix={**_request().completion_fix.model_dump(), "lat": latitude})
        )
    )

    assert outcome.distance_band == expected_band


def test_on_mode_requires_a_reason_before_completing_far_from_dropoff(monkeypatch: pytest.MonkeyPatch):
    _install_dependencies(monkeypatch, mode="on")
    request = _request(completion_fix={**_request().completion_fix.model_dump(), "lat": 50.460})

    with pytest.raises(HTTPException) as exc_info:
        _run(ride_complete.prepare_completion_location(_ride(), "driver_1", request))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "completion_confirmation_required"
    assert exc_info.value.detail["distance_band"] == "off_route"
    assert "lat" not in exc_info.value.detail
    assert "lng" not in exc_info.value.detail


def test_on_mode_accepts_an_allowed_off_route_confirmation(monkeypatch: pytest.MonkeyPatch):
    _install_dependencies(monkeypatch, mode="on")
    request = _request(
        completion_fix={**_request().completion_fix.model_dump(), "lat": 50.460},
        off_route_confirmation="changed_destination",
    )

    outcome = _run(ride_complete.prepare_completion_location(_ride(), "driver_1", request))

    assert outcome.distance_band == "off_route"


@pytest.mark.parametrize("reason", ["", "rider_changed_mind", "other"])
def test_completion_rejects_unknown_off_route_confirmation(reason: str):
    with pytest.raises(ValidationError):
        _request(off_route_confirmation=reason)


def test_stale_or_untrusted_fix_is_not_used_as_a_completion_tail(monkeypatch: pytest.MonkeyPatch):
    persist, update = _install_dependencies(monkeypatch)
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    request = _request(completion_fix={**_request().completion_fix.model_dump(), "captured_at": stale.isoformat()})

    outcome = _run(ride_complete.prepare_completion_location(_ride(), "driver_1", request))

    assert outcome.location_ack is None
    assert outcome.legacy_client_missing_tail is False
    assert outcome.completion_fix_rejection == "stale_capture"
    # The untrusted fix is never persisted as a breadcrumb tail ...
    persist.assert_not_awaited()
    # ... but the route still records the missing-tail audit marker (M-G folds the
    # queue into this same write; legacy shadow-mode here stays v1, so no stamp).
    update.assert_awaited_once_with(
        "ride_routes",
        {"ride_id": "ride_1"},
        {"completion_point": {"missing_tail": True, "rejection": "stale_capture"}},
        upsert=True,
    )


def test_legacy_completion_without_a_tail_remains_supported(monkeypatch: pytest.MonkeyPatch):
    persist, update = _install_dependencies(monkeypatch)

    outcome = _run(
        ride_complete.prepare_completion_location(
            _ride(), "driver_1", ride_complete.RideCompletionRequest.model_validate({})
        )
    )

    assert outcome.legacy_client_missing_tail is True
    assert outcome.location_ack is None
    persist.assert_not_awaited()
    # M-F: legacy app (no tail), no v2 evidence, shadow mode → NOT stamped v2 so
    # its settlement-computed polylines keep rendering. Only the audit marker.
    update.assert_awaited_once_with(
        "ride_routes",
        {"ride_id": "ride_1"},
        {"completion_point": {"missing_tail": True}},
        upsert=True,
    )


def test_legacy_evidence_completion_in_shadow_mode_stays_schema_v1(monkeypatch: pytest.MonkeyPatch):
    # M-F regression: shadow-mode legacy ride with no v2 breadcrumbs must not be
    # popped into the v2 reader (which would render an empty route).
    _persist, update = _install_dependencies(monkeypatch, mode="shadow", has_v2_evidence=False)

    _run(
        ride_complete.prepare_completion_location(
            _ride(), "driver_1", ride_complete.RideCompletionRequest.model_validate({})
        )
    )

    written = update.await_args.args[2]
    assert "route_schema_version" not in written
    assert "processing_status" not in written
    assert written == {"completion_point": {"missing_tail": True}}


def test_v2_evidence_completion_stamps_schema_v2_and_queues(monkeypatch: pytest.MonkeyPatch):
    # M-F + M-G: a ride that already emitted v2 breadcrumbs is stamped v2 AND
    # atomically queued for finalization, even without a completion_fix and in
    # shadow mode.
    _persist, update = _install_dependencies(monkeypatch, mode="shadow", has_v2_evidence=True)

    _run(
        ride_complete.prepare_completion_location(
            _ride(), "driver_1", ride_complete.RideCompletionRequest.model_validate({})
        )
    )

    written = update.await_args.args[2]
    assert written["route_schema_version"] == 2
    assert written["processing_status"] == "pending"
    assert written["next_retry_at"] is not None
    assert written["completion_point"] == {"missing_tail": True}


def test_on_mode_completion_without_evidence_still_stamps_v2(monkeypatch: pytest.MonkeyPatch):
    # M-F: mode 'on' forces v2 even for a legacy tail with no v2 evidence.
    _persist, update = _install_dependencies(monkeypatch, mode="on", has_v2_evidence=False)

    _run(
        ride_complete.prepare_completion_location(
            _ride(), "driver_1", ride_complete.RideCompletionRequest.model_validate({})
        )
    )

    written = update.await_args.args[2]
    assert written["route_schema_version"] == 2
    assert written["processing_status"] == "pending"


def test_completion_fix_folds_pending_queue_into_the_completion_write(monkeypatch: pytest.MonkeyPatch):
    # M-G: a trusted completion_fix stamps v2 and queues finalization in the SAME
    # ride_routes write as the completion point — no separate mark_route_pending
    # call that could fail after the schema stamp landed.
    _persist, update = _install_dependencies(monkeypatch)

    _run(ride_complete.prepare_completion_location(_ride(), "driver_1", _request()))

    written = update.await_args.args[2]
    assert written["route_schema_version"] == 2
    assert written["processing_status"] == "pending"
    assert written["processing_claimed_at"] is None
    assert written["next_retry_at"] is not None
    assert written["snapshot_revision"] == 0
    assert written["snapshot_object_path"] is None
    assert written["finalized_at"] is None
    assert written["completion_point"]["is_completion_fix"] is True
    assert written["completion_point"]["distance_band"] == "at_destination"


def test_completion_restores_spoof_validation_and_pickup_snap_as_nonblocking_spawns():
    source = (Path(__file__).resolve().parents[1] / "routes" / "drivers" / "ride_complete.py").read_text()

    # M-C: spoof validation + pickup-leg road-snap are restored as fire-and-forget
    # spawns (flag suspicious traces / backfill road_polyline_pickup) that must
    # never block completion — spawned, never awaited.
    assert 'spawn(_validate_ride_route(ride_id, _breadcrumbs_for_validation, driver["id"]))' in source
    assert "spawn(_snap_pickup_leg_async(ride_id, _breadcrumbs_for_validation))" in source
    assert "await _validate_ride_route(" not in source
    assert "await _snap_pickup_leg_async(" not in source

    # M-G: the separate mark_route_pending call + import are folded into the
    # atomic completion-point write, so neither the call nor the import remains
    # (an explanatory comment may still name it).
    assert "mark_route_pending(" not in source
    assert "import mark_route_pending" not in source
    assert '"processing_status": "pending"' in source

    # The v2 route publisher stays the finalizer; completion never re-publishes
    # legacy geometry or the old snapshot spawn.
    assert "spawn(finalize_route(ride_id))" not in source
    assert "spawn(_shared._generate_and_store_ride_snapshot" not in source
