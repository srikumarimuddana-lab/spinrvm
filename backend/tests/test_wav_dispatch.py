"""
Unit tests for L-P0-3: WAV (wheelchair-accessible vehicle) dispatch support.

Saskatchewan Transportation Act requires WAV ride requests to be matched to
WAV-capable drivers when one is online in the service area.

Layer 1 — pure schema/logic: no backend imports needed.
Layer 2 — dispatch filter: mock DispatchService.db; verify filter dict.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Layer 1 — pure schema logic (no import failures possible)
# ---------------------------------------------------------------------------


def _make_driver_filter(ride: dict) -> dict:
    """Mirror of the dispatch_service.find_candidate_drivers() filter logic."""
    driver_filter = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": ride["vehicle_type_id"],
    }
    if ride.get("requires_wav"):
        driver_filter["is_wav"] = True
    return driver_filter


def test_wav_filter_added_when_requires_wav_true():
    ride = {"vehicle_type_id": "vt-1", "requires_wav": True}
    f = _make_driver_filter(ride)
    assert f.get("is_wav") is True


def test_wav_filter_absent_when_requires_wav_false():
    ride = {"vehicle_type_id": "vt-1", "requires_wav": False}
    f = _make_driver_filter(ride)
    assert "is_wav" not in f


def test_wav_filter_absent_when_requires_wav_missing():
    ride = {"vehicle_type_id": "vt-1"}
    f = _make_driver_filter(ride)
    assert "is_wav" not in f


def test_non_wav_filter_fields_unchanged_for_wav_ride():
    ride = {"vehicle_type_id": "vt-xyz", "requires_wav": True}
    f = _make_driver_filter(ride)
    assert f["is_online"] is True
    assert f["is_available"] is True
    assert f["is_verified"] is True
    assert f["status"] == "active"
    assert f["vehicle_type_id"] == "vt-xyz"


# ---------------------------------------------------------------------------
# Layer 2 — dispatch service integration (skipped if deps unavailable)
# ---------------------------------------------------------------------------

try:
    from backend.services.dispatch_service import DispatchService

    _HAS_DISPATCH = True
except BaseException:
    _HAS_DISPATCH = False

_skip = pytest.mark.skipif(not _HAS_DISPATCH, reason="backend deps not installed")


@_skip
@pytest.mark.anyio
async def test_find_candidate_drivers_passes_is_wav_filter():
    """
    DispatchService.find_candidate_drivers() must include is_wav=True in the
    DB filter when the ride has requires_wav=True.
    """
    wav_driver = {
        "id": "d1",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "is_wav": True,
        "vehicle_type_id": "vt-1",
    }
    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[wav_driver])

    svc = DispatchService(db=mock_db)
    ride = {"vehicle_type_id": "vt-1", "requires_wav": True}

    with pytest.MonkeyPatch.context() as mp:
        # present_driver_ids raises so we fall back to raw DB rows
        mp.setattr(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=Exception("redis unavailable")),
        )
        result = await svc.find_candidate_drivers(ride)

    call_kwargs = mock_db.get_rows.call_args
    _, filter_dict = call_kwargs.args[0], call_kwargs.args[1]
    assert filter_dict.get("is_wav") is True
    assert result == [wav_driver]


@_skip
@pytest.mark.anyio
async def test_find_candidate_drivers_no_is_wav_for_standard_ride():
    """
    is_wav must NOT appear in the filter for a standard (non-WAV) ride.
    """
    driver = {
        "id": "d2",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "is_wav": False,
        "vehicle_type_id": "vt-1",
    }
    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[driver])

    svc = DispatchService(db=mock_db)
    ride = {"vehicle_type_id": "vt-1", "requires_wav": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(side_effect=Exception("redis unavailable")),
        )
        await svc.find_candidate_drivers(ride)

    _, filter_dict = mock_db.get_rows.call_args.args[0], mock_db.get_rows.call_args.args[1]
    assert "is_wav" not in filter_dict


@_skip
@pytest.mark.anyio
async def test_find_candidate_drivers_returns_empty_when_no_wav_driver():
    """
    When no WAV-capable driver is available, return [] so the existing
    5-minute no-driver timeout auto-cancels the ride gracefully.
    """
    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(return_value=[])

    svc = DispatchService(db=mock_db)
    ride = {"vehicle_type_id": "vt-1", "requires_wav": True}

    result = await svc.find_candidate_drivers(ride)
    assert result == []


# ---------------------------------------------------------------------------
# Layer 3 — schema validation (skipped if deps unavailable)
# ---------------------------------------------------------------------------

try:

    from backend.schemas import CreateRideRequest

    _HAS_SCHEMAS = True
except BaseException:
    _HAS_SCHEMAS = False

_skip_schemas = pytest.mark.skipif(not _HAS_SCHEMAS, reason="backend schemas not importable")


@_skip_schemas
def test_create_ride_request_requires_wav_defaults_false():
    req = CreateRideRequest(
        vehicle_type_id="vt-1",
        pickup_address="123 Main St",
        pickup_lat=52.13,
        pickup_lng=-106.67,
        dropoff_address="456 Elm St",
        dropoff_lat=52.14,
        dropoff_lng=-106.68,
    )
    assert req.requires_wav is False


@_skip_schemas
def test_create_ride_request_accepts_requires_wav_true():
    req = CreateRideRequest(
        vehicle_type_id="vt-1",
        pickup_address="123 Main St",
        pickup_lat=52.13,
        pickup_lng=-106.67,
        dropoff_address="456 Elm St",
        dropoff_lat=52.14,
        dropoff_lng=-106.68,
        requires_wav=True,
    )
    assert req.requires_wav is True
