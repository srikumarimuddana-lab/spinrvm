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


def _install_dependencies(monkeypatch: pytest.MonkeyPatch, *, mode: str = "shadow"):
    persist = AsyncMock(return_value=_persist_result())
    update = AsyncMock()
    monkeypatch.setattr(ride_complete, "persist_trip_location_batch", persist)
    monkeypatch.setattr(ride_complete.db_supabase, "update_one", update)

    async def route_mode():
        return mode

    monkeypatch.setattr(ride_complete, "_get_route_integrity_mode", route_mode)
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
    persist.assert_not_awaited()
    update.assert_not_awaited()


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
    update.assert_awaited_once_with(
        "ride_routes",
        {"ride_id": "ride_1"},
        {"route_schema_version": 2, "completion_point": {"missing_tail": True}},
        upsert=True,
    )


def test_completion_queues_v2_finalization_instead_of_publishing_legacy_geometry():
    source = (Path(__file__).resolve().parents[1] / "routes" / "drivers" / "ride_complete.py").read_text()

    assert "await mark_route_pending(" in source
    assert "spawn(finalize_route(ride_id))" not in source
    assert "spawn(_validate_ride_route" not in source
    assert "spawn(_shared._generate_and_store_ride_snapshot" not in source
