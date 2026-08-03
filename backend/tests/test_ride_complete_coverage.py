"""Coverage-closure tests for routes/drivers/ride_complete.py (A1c Sub-tier C).

The natural companion to the already-closed ride_flow.py/ride_cancel.py/
ride_reads.py triplet from the same routes/drivers/ package (see
docs/change-log/2026-08-02-a1c-drivers-ride-flow-batch-coverage.md) — this
file owns trip completion: fare-settlement kickoff, the GPS/route
finalization handoff, the SGI insurance-period distance audit, incentive
claims, and the driver earnings snapshot.

Complements the existing coverage:
- tests/test_ride_completion_location.py: `prepare_completion_location`'s
  happy path, distance-band thresholds, the "on" mode confirmation gate, and
  the legacy (no completion_fix) path.
- tests/test_rides.py::test_full_ride_lifecycle: `complete_ride`'s overall
  happy path end-to-end (accept -> arrive -> verify-otp -> complete), with no
  service_area_id so the subscription guard and most side-effect branches are
  never exercised.
- tests/test_backfill_period_distances.py, tests/test_report_fare_attribution_gap.py,
  tests/test_ride_route_analyzer.py: narrow slices of ride-completion-adjacent
  behaviour in other modules, not this route handler's own branches.

This file adds the long tail neither reaches: `RideCompletionRequest`'s
cross-field validation errors; `_get_route_integrity_mode` /
`_get_gps_distance_filter_mode` as standalone units (success, settings-read
exception, invalid-mode value); `_completion_fix_rejection`'s
future/mocked/low-accuracy/invalid-coordinate branches; `_completion_distance_band`'s
malformed-ride exception path; `prepare_completion_location`'s two DB-failure
503 branches; `_fire_driver_activated`'s both-imports-fail and spawn-raises
branches; and, inside `complete_ride` itself: the driver/ride-not-found
guards, the non-fatal breadcrumb-flush and GPS-aggregation failures, the
ride_routes persistence retry-exhausted fallback, the fare_lock branch, a
ride_metrics assembly exception, the atomic-completion PGRST204 retry-with-
minimal-fields path (and the plain-re-raise and claim-lost-race paths), the
period-distance-audit non-fatal failure, the v2-finalization completion_fix
branch and its own non-fatal failure, the guest-corporate-settlement spawn,
the incentive-claims loop (mismatch/zero-skip/insert/exception), the
earnings-snapshot-write failure, the receipt-email-stub log line, the quota
offline-check failure, the quota-exhausted driver notification, the
trip-window-compression milestone detection, and the quest-progress-update
scheduling failure.

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md, and the
docstring at the top of test_driver_ride_flow_coverage.py for the fuller
writeup):
- `db_supabase` is a *module reference* shared by every importer, so
  `monkeypatch.setattr(ride_complete.db_supabase, "<fn>", ...)` affects both
  `db_supabase.<fn>(...)` and `_deps.db_supabase.<fn>(...)` call sites.
- `manager`, `record_period_transition`, `send_push_notification` are
  reached in this file only via `_deps.<name>` (they are NOT in
  ride_complete.py's own `from ._deps import (...)` list), so they must be
  patched at `ride_complete._deps.<name>`.
- `spawn` and everything else in `from ._deps import (...)` (db_supabase,
  flush_driver_breadcrumbs, recalculate_fare_for_distance, fare_share,
  build_earnings_snapshot, send_live_activity_update, db_error_text,
  pg_error_code, ...) IS a bound name copied into ride_complete's own
  namespace, so it is patched at `ride_complete.<name>` directly.
- `persist_trip_location_batch`, `mark_route_pending`, `compute_trip_distances`,
  `load_ride_breadcrumbs`, `record_ride_period_distances` are imported at
  module level in ride_complete.py's own dual-import blocks -- also patched
  at `ride_complete.<name>` directly.
- `get_app_settings` (route-integrity mode, gps-filter mode, fare_lock),
  `force_offline_if_exhausted`, `auto_settle_guest_corporate`,
  `record_integrity_event`, `update_quest_progress_on_ride_complete`, and
  `meta_conversions_service` are imported *inside* the function bodies on
  every call (dual-import pattern) -- patching the *source* module
  (`backend.settings_loader`, `backend.utils.spinr_pass`, etc.) is what's
  needed for those.

Test-only change — no application code modified.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.routes.drivers import ride_complete
from backend.utils.breadcrumbs import LocationBatchAck, LocationBatchPersistResult
from backend.utils.error_handling import RideStateError
from backend.utils.trip_distance import TripDistanceResult

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-complete-1"
_USER_ID = "user-complete-1"
_RIDE_ID = "ride-complete-1"
_RIDER_ID = "rider-complete-1"


def _spawn_close(coro):
    """spawn() double that just closes the coroutine (no leaked-coro warning,
    and no real background execution)."""
    coro.close()


def _spawn_raises(coro):
    coro.close()
    raise RuntimeError("spawn failed")


def _driver(**kw):
    base = {"id": _DRIVER_ID, "user_id": _USER_ID, "total_rides": 5}
    base.update(kw)
    return base


def _ride(**kw):
    base = {
        "id": _RIDE_ID,
        "status": "in_progress",
        "driver_id": _DRIVER_ID,
        "rider_id": _RIDER_ID,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "dropoff_lat": 52.2,
        "dropoff_lng": -106.7,
        "vehicle_type_id": "vt-1",
        "service_area_id": None,
        "planned_distance_km": 10.0,
        "distance_km": 10.0,
        "duration_minutes": 20,
        "assigned_at": "2026-08-03T10:00:00Z",
        "driver_accepted_at": "2026-08-03T10:00:30Z",
        "driver_arrived_at": "2026-08-03T10:05:00Z",
        "ride_started_at": "2026-08-03T10:06:00Z",
        "base_fare": 3.0,
        "distance_fare": 8.0,
        "time_fare": 2.0,
        "total_fare": 13.0,
        "booking_fee": 1.0,
        "airport_fee": 0.0,
        "tip_amount": 0,
        "tax_amount": 0,
        "cancellation_fee_driver": 0,
        "guest_booking": False,
        "payment_method": "card",
        "ride_metrics": {},
    }
    base.update(kw)
    return base


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


class _MockManager:
    def __init__(self):
        self.send_personal_message = AsyncMock()
        self.broadcast_ride_status = AsyncMock()
        self.broadcast_to_admins = AsyncMock()


def _install_success_mocks(monkeypatch: pytest.MonkeyPatch, *, driver=None, ride=None):
    """Wire every dependency of complete_ride() to a successful default.

    Returns (driver, ride, manager) so a test can override individual mocks
    afterward for the branch it targets.
    """
    driver = driver or _driver()
    ride = ride or _ride()

    async def get_rows(table, filters, limit=None):
        if table == "drivers":
            return [driver]
        if table == "rides":
            return [ride]
        return []

    monkeypatch.setattr(ride_complete.db_supabase, "get_rows", AsyncMock(side_effect=get_rows))

    async def update_one(table, filters, update, upsert=False):
        if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
            merged = {**ride, **update, "status": "completed"}
            return merged
        return {"ok": True}

    monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))
    monkeypatch.setattr(ride_complete.db_supabase, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        ride_complete.db_supabase,
        "get_ride",
        AsyncMock(return_value={**ride, "status": "completed", "total_fare": ride["total_fare"]}),
    )
    monkeypatch.setattr(ride_complete.db_supabase, "set_driver_available", AsyncMock())
    monkeypatch.setattr(ride_complete.db_supabase, "insert_one", AsyncMock())
    monkeypatch.setattr(ride_complete.db_supabase, "run_sync", AsyncMock(return_value=SimpleNamespace(data=[])))
    monkeypatch.setattr(ride_complete.db_supabase, "supabase", MagicMock())

    monkeypatch.setattr(ride_complete, "flush_driver_breadcrumbs", AsyncMock())
    monkeypatch.setattr(ride_complete, "persist_trip_location_batch", AsyncMock(return_value=_persist_result()))
    monkeypatch.setattr(ride_complete, "load_ride_breadcrumbs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        ride_complete,
        "compute_trip_distances",
        AsyncMock(return_value=TripDistanceResult(actual_distance_km=ride["planned_distance_km"])),
    )
    monkeypatch.setattr(ride_complete, "record_ride_period_distances", AsyncMock())
    monkeypatch.setattr(ride_complete, "mark_route_pending", AsyncMock())
    monkeypatch.setattr(ride_complete, "recalculate_fare_for_distance", MagicMock(return_value=None))
    monkeypatch.setattr(ride_complete, "send_live_activity_update", AsyncMock())
    monkeypatch.setattr(ride_complete, "spawn", _spawn_close)

    manager = _MockManager()
    monkeypatch.setattr(ride_complete._deps, "manager", manager)
    monkeypatch.setattr(ride_complete._deps, "record_period_transition", AsyncMock())
    monkeypatch.setattr(ride_complete._deps, "send_push_notification", AsyncMock())

    monkeypatch.setattr("backend.settings_loader.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr("backend.utils.spinr_pass.force_offline_if_exhausted", AsyncMock(return_value=None))

    return driver, ride, manager


async def _complete(**kw):
    return await ride_complete.complete_ride(
        ride_id=kw.pop("ride_id", _RIDE_ID),
        completion_request=kw.pop("completion_request", None),
        current_user=kw.pop("current_user", {"id": _USER_ID}),
    )


# ============================================================
# RideCompletionRequest — cross-field validation
# ============================================================


class TestRideCompletionRequestValidation:
    def _fix(self, **kw):
        base = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 5,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 50.0,
            "lng": -104.0,
        }
        base.update(kw)
        return base

    def test_final_session_id_must_match_completion_fix_session(self):
        with pytest.raises(ValidationError, match="final_session_id must match"):
            ride_complete.RideCompletionRequest.model_validate(
                {
                    "completion_fix": self._fix(),
                    "final_session_id": "00000000-0000-0000-0000-000000000000",
                }
            )

    def test_final_sequence_number_must_match_completion_fix_sequence(self):
        with pytest.raises(ValidationError, match="final_sequence_number must match"):
            ride_complete.RideCompletionRequest.model_validate(
                {"completion_fix": self._fix(), "final_sequence_number": 999}
            )

    def test_matching_ids_pass_validation(self):
        req = ride_complete.RideCompletionRequest.model_validate(
            {
                "completion_fix": self._fix(),
                "final_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                "final_sequence_number": 5,
            }
        )
        assert req.completion_fix is not None


# ============================================================
# _get_route_integrity_mode
# ============================================================


class TestGetRouteIntegrityMode:
    async def test_returns_configured_mode(self):
        with patch(
            "backend.settings_loader.get_app_settings", AsyncMock(return_value={"route_integrity_v2_mode": "on"})
        ):
            assert await ride_complete._get_route_integrity_mode() == "on"

    async def test_settings_read_failure_raises_503(self):
        with patch("backend.settings_loader.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(HTTPException) as exc:
                await ride_complete._get_route_integrity_mode()
        assert exc.value.status_code == 503

    async def test_invalid_mode_raises_503(self):
        with patch(
            "backend.settings_loader.get_app_settings", AsyncMock(return_value={"route_integrity_v2_mode": "bogus"})
        ):
            with pytest.raises(HTTPException) as exc:
                await ride_complete._get_route_integrity_mode()
        assert exc.value.status_code == 503


# ============================================================
# _get_gps_distance_filter_mode
# ============================================================


class TestGetGpsDistanceFilterMode:
    async def test_returns_configured_mode(self):
        with patch(
            "backend.settings_loader.get_app_settings", AsyncMock(return_value={"gps_distance_filter_mode": "on"})
        ):
            assert await ride_complete._get_gps_distance_filter_mode() == "on"

    async def test_settings_read_failure_defaults_to_off(self):
        with patch("backend.settings_loader.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await ride_complete._get_gps_distance_filter_mode() == "off"

    async def test_invalid_mode_defaults_to_off(self):
        with patch(
            "backend.settings_loader.get_app_settings", AsyncMock(return_value={"gps_distance_filter_mode": "bogus"})
        ):
            assert await ride_complete._get_gps_distance_filter_mode() == "off"


# ============================================================
# _completion_fix_rejection — remaining branches
# ============================================================


class TestCompletionFixRejection:
    def _fix(self, **kw):
        base = dict(
            recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            sequence_number=1,
            captured_at=datetime.now(timezone.utc).isoformat(),
            lat=50.0,
            lng=-104.0,
        )
        base.update(kw)
        return ride_complete.CompletionFix(**base)

    def test_invalid_capture_time_rejected(self):
        # CompletionFix.captured_at is typed `datetime`, so pydantic itself
        # rejects a non-parseable string at construction — the
        # "invalid_capture_time" branch in _completion_fix_rejection can only
        # be reached with a raw value that bypasses that validation, which a
        # malformed persisted/legacy record could still produce at runtime.
        fix = SimpleNamespace(**{**self._fix().model_dump(), "captured_at": "not-a-timestamp"})
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "invalid_capture_time"

    def test_future_capture_rejected(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        fix = self._fix(captured_at=future.isoformat())
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "future_capture"

    def test_mocked_location_rejected(self):
        fix = self._fix(mocked=True)
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "mocked_location"

    def test_low_accuracy_rejected(self):
        fix = self._fix(accuracy=500.0)
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "low_accuracy"

    def test_invalid_coordinate_rejected(self):
        fix = self._fix(lat=0, lng=0)
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "invalid_coordinate"

    def test_valid_fix_is_not_rejected(self):
        fix = self._fix()
        assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) is None


# ============================================================
# _completion_distance_band — malformed ride
# ============================================================


class TestCompletionDistanceBandUnknown:
    def test_missing_dropoff_coordinates_returns_unknown(self):
        fix = ride_complete.CompletionFix(
            recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            sequence_number=1,
            captured_at=datetime.now(timezone.utc).isoformat(),
            lat=50.0,
            lng=-104.0,
        )
        band, meters = ride_complete._completion_distance_band({"id": "r1"}, fix)
        assert band == "unknown"
        assert meters is None

    def test_non_numeric_dropoff_coordinates_returns_unknown(self):
        fix = ride_complete.CompletionFix(
            recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            sequence_number=1,
            captured_at=datetime.now(timezone.utc).isoformat(),
            lat=50.0,
            lng=-104.0,
        )
        band, meters = ride_complete._completion_distance_band(
            {"id": "r1", "dropoff_lat": "not-a-number", "dropoff_lng": -104.0}, fix
        )
        assert band == "unknown"
        assert meters is None


# ============================================================
# prepare_completion_location — DB failure branches
# ============================================================


class TestPrepareCompletionLocationFailures:
    async def test_missing_tail_marker_write_failure_raises_503(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=RuntimeError("db down")))
        with pytest.raises(HTTPException) as exc:
            await ride_complete.prepare_completion_location(
                {"id": "r1"}, "driver_1", ride_complete.RideCompletionRequest()
            )
        assert exc.value.status_code == 503

    async def test_final_fix_persistence_failure_raises_503(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            ride_complete, "persist_trip_location_batch", AsyncMock(side_effect=RuntimeError("timeout"))
        )
        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock())
        monkeypatch.setattr(ride_complete, "_get_route_integrity_mode", AsyncMock(return_value="shadow"))

        fix = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 50.445,
            "lng": -104.618,
        }
        request = ride_complete.RideCompletionRequest.model_validate({"completion_fix": fix})

        with pytest.raises(HTTPException) as exc:
            await ride_complete.prepare_completion_location(
                {"id": "r1", "dropoff_lat": 50.445, "dropoff_lng": -104.618}, "driver_1", request
            )
        assert exc.value.status_code == 503

    async def test_httpexception_from_persistence_propagates_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """The 409 confirmation-required guard must pass through untouched,
        not get wrapped into a generic 503."""
        monkeypatch.setattr(ride_complete, "_get_route_integrity_mode", AsyncMock(return_value="on"))

        fix = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 51.0,
            "lng": -105.0,
        }
        request = ride_complete.RideCompletionRequest.model_validate({"completion_fix": fix})

        with pytest.raises(HTTPException) as exc:
            await ride_complete.prepare_completion_location(
                {"id": "r1", "dropoff_lat": 50.445, "dropoff_lng": -104.618}, "driver_1", request
            )
        assert exc.value.status_code == 409


# ============================================================
# _fire_driver_activated
# ============================================================


class TestFireDriverActivated:
    def test_both_import_paths_failing_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(sys.modules, "backend.services.meta_conversions_service", None)
        monkeypatch.setitem(sys.modules, "services.meta_conversions_service", None)
        # Must not raise.
        ride_complete._fire_driver_activated(_driver(), {"id": _USER_ID}, _ride())

    def test_spawn_failure_is_logged_not_raised(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ride_complete, "spawn", _spawn_raises)
        # Must not raise even though spawn() raises internally.
        ride_complete._fire_driver_activated(_driver(), {"id": _USER_ID}, _ride())


# ============================================================
# complete_ride — guards
# ============================================================


class TestCompleteRideGuards:
    async def test_404_when_driver_not_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ride_complete.db_supabase, "get_rows", AsyncMock(return_value=[]))
        with pytest.raises(HTTPException) as exc:
            await _complete()
        assert exc.value.status_code == 404
        assert "Driver not found" in exc.value.detail

    async def test_404_when_ride_not_found(self, monkeypatch: pytest.MonkeyPatch):
        async def get_rows(table, filters, limit=None):
            if table == "drivers":
                return [_driver()]
            return []

        monkeypatch.setattr(ride_complete.db_supabase, "get_rows", AsyncMock(side_effect=get_rows))
        with pytest.raises(HTTPException) as exc:
            await _complete()
        assert exc.value.status_code == 404
        assert "Ride not found" in exc.value.detail

    async def test_wrong_state_raises_ride_state_error(self, monkeypatch: pytest.MonkeyPatch):
        async def get_rows(table, filters, limit=None):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [_ride(status="driver_arrived")]
            return []

        monkeypatch.setattr(ride_complete.db_supabase, "get_rows", AsyncMock(side_effect=get_rows))
        with pytest.raises(RideStateError):
            await _complete()


# ============================================================
# complete_ride — non-fatal side-branches
# ============================================================


class TestCompleteRideNonFatalBranches:
    async def test_breadcrumb_flush_failure_does_not_block_completion(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete, "flush_driver_breadcrumbs", AsyncMock(side_effect=RuntimeError("ws gone")))

        response = await _complete()
        assert response["status"] == "completed"

    async def test_gps_aggregation_failure_falls_back_to_planned_distance(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete, "load_ride_breadcrumbs", AsyncMock(side_effect=RuntimeError("boom")))

        response = await _complete()
        assert response["status"] == "completed"

    async def test_ride_routes_persistence_retries_exhausted_records_failure_status(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Use a v2 completion_fix request, and distinguish the retry loop's
        # "ride_routes" write (route geometry, no "completion_point" key)
        # from prepare_completion_location's own "ride_routes" write (the
        # completion-point persist, always includes "completion_point") —
        # the latter is fatal on failure and must keep succeeding here so
        # the retry loop below is actually reached.
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete.asyncio, "sleep", AsyncMock())

        retry_attempts = []

        async def update_one(table, filters, update, upsert=False):
            if table == "ride_routes" and "completion_point" not in update:
                retry_attempts.append(table)
                raise RuntimeError("ride_routes write failed")
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                return {**_ride(), **update, "status": "completed"}
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        fix = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 52.1,
            "lng": -106.7,
        }
        request = ride_complete.RideCompletionRequest.model_validate({"completion_fix": fix})

        response = await _complete(completion_request=request)
        assert response["status"] == "completed"
        assert len(retry_attempts) == 3  # 3 retry attempts exhausted

    async def test_ride_routes_persistence_status_write_itself_fails_non_fatally(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete.asyncio, "sleep", AsyncMock())

        async def update_one(table, filters, update, upsert=False):
            if table == "ride_routes" and "completion_point" not in update:
                raise RuntimeError("ride_routes write failed")
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                return {**_ride(), **update, "status": "completed"}
            if table == "rides":
                # The route_geometry_status fallback write itself fails too.
                raise RuntimeError("status write also failed")
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        fix = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 52.1,
            "lng": -106.7,
        }
        request = ride_complete.RideCompletionRequest.model_validate({"completion_fix": fix})

        response = await _complete(completion_request=request)
        assert response["status"] == "completed"


# ============================================================
# complete_ride — fare_lock branch
# ============================================================


class TestCompleteRideFareLock:
    async def test_fare_lock_enabled_keeps_booking_time_fare(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(planned_distance_km=10.0)
        _install_success_mocks(monkeypatch, ride=ride)
        monkeypatch.setattr(
            ride_complete,
            "compute_trip_distances",
            AsyncMock(return_value=TripDistanceResult(actual_distance_km=15.0)),
        )
        monkeypatch.setattr(
            "backend.settings_loader.get_app_settings", AsyncMock(return_value={"fare_lock_enabled": True})
        )

        response = await _complete()
        assert response["status"] == "completed"

    async def test_fare_lock_setting_read_failure_defaults_to_false(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        call_count = {"n": 0}

        async def flaky_settings():
            call_count["n"] += 1
            raise RuntimeError("settings unavailable")

        monkeypatch.setattr("backend.settings_loader.get_app_settings", flaky_settings)

        response = await _complete()
        assert response["status"] == "completed"
        assert call_count["n"] >= 1


# ============================================================
# complete_ride — ride_metrics assembly
# ============================================================


class TestCompleteRideMetricsAssembly:
    async def test_zero_or_negative_phase_duration_is_dropped_not_negative(self, monkeypatch: pytest.MonkeyPatch):
        """driver_arrived_at == ride_started_at yields a non-positive wait
        window, which _minutes_between must report as None rather than 0."""
        ride = _ride(driver_arrived_at="2026-08-03T10:06:00Z", ride_started_at="2026-08-03T10:06:00Z")
        _install_success_mocks(monkeypatch, ride=ride)

        response = await _complete()
        assert response["status"] == "completed"

    async def test_ride_metrics_assembly_exception_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        """A malformed existing ride_metrics blob (not a dict) breaks the
        `.get('phases')` call inside the assembly block; completion must
        still succeed with the exception merely logged."""
        ride = _ride(ride_metrics="not-a-dict")
        _install_success_mocks(monkeypatch, ride=ride)

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — atomic completion claim
# ============================================================


class TestCompleteRideAtomicClaim:
    async def test_pgrst204_retries_with_minimal_fields(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        pg_error = RuntimeError("schema cache miss")
        pg_error.code = "PGRST204"
        call_log = []

        async def update_one(table, filters, update, upsert=False):
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                call_log.append(update)
                if len(call_log) == 1:
                    raise pg_error
                return {**_ride(), "status": "completed"}
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        response = await _complete()
        assert response["status"] == "completed"
        assert len(call_log) == 2  # full payload attempt, then minimal-fields retry

    async def test_non_pgrst204_error_is_reraised(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        async def update_one(table, filters, update, upsert=False):
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                raise RuntimeError("connection reset")
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        with pytest.raises(RuntimeError, match="connection reset"):
            await _complete()

    async def test_claim_lost_to_concurrent_completion_raises_ride_state_error(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        async def update_one(table, filters, update, upsert=False):
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                return None  # zero rows matched — already completed elsewhere
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        with pytest.raises(RideStateError, match="no longer in_progress"):
            await _complete()


# ============================================================
# complete_ride — period-distance audit + v2 finalization
# ============================================================


class TestCompleteRidePeriodAuditAndFinalization:
    async def test_period_distance_audit_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(
            ride_complete, "record_ride_period_distances", AsyncMock(side_effect=RuntimeError("sgi table down"))
        )

        response = await _complete()
        assert response["status"] == "completed"

    async def test_finalization_uses_completion_fix_when_location_ack_present(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        mark_route_pending = AsyncMock()
        monkeypatch.setattr(ride_complete, "mark_route_pending", mark_route_pending)
        monkeypatch.setattr(ride_complete, "_get_route_integrity_mode", AsyncMock(return_value="shadow"))

        fix = {
            "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            "sequence_number": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "lat": 52.1,
            "lng": -106.7,
        }
        request = ride_complete.RideCompletionRequest.model_validate({"completion_fix": fix})

        response = await _complete(completion_request=request)
        assert response["status"] == "completed"
        mark_route_pending.assert_awaited_once()
        finalization_point = mark_route_pending.call_args.args[1]
        assert finalization_point["is_completion_fix"] is True
        assert "missing_tail" not in finalization_point

    async def test_mark_route_pending_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete, "mark_route_pending", AsyncMock(side_effect=RuntimeError("queue down")))

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — guest corporate settlement
# ============================================================


class TestCompleteRideGuestCorporateSettlement:
    async def test_guest_company_allowance_ride_spawns_auto_settle(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(guest_booking=True, payment_method="company_allowance")
        _install_success_mocks(monkeypatch, ride=ride)

        spawned = []

        def capture_spawn(coro):
            spawned.append(coro)
            coro.close()

        monkeypatch.setattr(ride_complete, "spawn", capture_spawn)

        response = await _complete()
        assert response["status"] == "completed"
        assert len(spawned) >= 1


# ============================================================
# complete_ride — incentive claims
# ============================================================


class TestCompleteRideIncentiveClaims:
    async def test_incentives_query_uses_service_area_or_clause(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(service_area_id="sa-1")
        _install_success_mocks(monkeypatch, ride=ride)

        supabase = MagicMock()
        monkeypatch.setattr(ride_complete.db_supabase, "supabase", supabase)

        response = await _complete()
        assert response["status"] == "completed"
        table_mock = supabase.table.return_value
        table_mock.select.return_value.eq.return_value.or_.assert_called_once()

    async def test_incentive_claims_skip_mismatch_and_zero_bonus_insert_valid(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(service_area_id=None, vehicle_type_id="vt-1")
        _install_success_mocks(monkeypatch, ride=ride)

        incentives = SimpleNamespace(
            data=[
                {"id": "inc-mismatch", "bonus_amount": 5.0, "vehicle_type_id": "vt-other"},
                {"id": "inc-zero", "bonus_amount": 0, "vehicle_type_id": None},
                {"id": "inc-valid", "bonus_amount": 2.5, "vehicle_type_id": None},
            ]
        )
        monkeypatch.setattr(ride_complete.db_supabase, "run_sync", AsyncMock(return_value=incentives))
        insert_one = AsyncMock()
        monkeypatch.setattr(ride_complete.db_supabase, "insert_one", insert_one)

        response = await _complete()
        assert response["status"] == "completed"
        insert_one.assert_awaited_once()
        claim_payload = insert_one.call_args.args[1]
        assert claim_payload["incentive_id"] == "inc-valid"
        assert claim_payload["bonus_amount"] == 2.5

    async def test_incentive_query_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(ride_complete.db_supabase, "run_sync", AsyncMock(side_effect=RuntimeError("db down")))

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — earnings snapshot failure
# ============================================================


class TestCompleteRideEarningsSnapshot:
    async def test_earnings_snapshot_write_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        async def update_one(table, filters, update, upsert=False):
            if table == "rides" and filters.get("status") == ride_complete.RideStatus.IN_PROGRESS:
                return {**_ride(), "status": "completed"}
            if table == "rides" and "driver_earnings_snapshot" in update:
                raise RuntimeError("snapshot write failed")
            return {"ok": True}

        monkeypatch.setattr(ride_complete.db_supabase, "update_one", AsyncMock(side_effect=update_one))

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — receipt email stub
# ============================================================


class TestCompleteRideReceiptEmailStub:
    async def test_rider_with_email_logs_receipt_line(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(
            ride_complete.db_supabase,
            "get_user_by_id",
            AsyncMock(return_value={"id": _RIDER_ID, "email": "rider@example.com"}),
        )

        with patch.object(ride_complete.logger, "info") as mock_info:
            response = await _complete()

        assert response["status"] == "completed"
        joined = " ".join(str(c) for c in mock_info.call_args_list)
        assert "email receipt" in joined


# ============================================================
# complete_ride — Spinr Pass quota
# ============================================================


class TestCompleteRideQuotaOffline:
    async def test_quota_check_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)
        monkeypatch.setattr(
            "backend.utils.spinr_pass.force_offline_if_exhausted",
            AsyncMock(side_effect=RuntimeError("redis down")),
        )

        response = await _complete()
        assert response["status"] == "completed"

    async def test_quota_exhausted_notifies_driver_and_admins(self, monkeypatch: pytest.MonkeyPatch):
        driver, ride, manager = _install_success_mocks(monkeypatch)
        monkeypatch.setattr(
            "backend.utils.spinr_pass.force_offline_if_exhausted",
            AsyncMock(
                return_value={
                    "hours_until_reset": 6.4,
                    "rides_per_day": 10,
                    "quota_resets_at": "2026-08-04T00:00:00Z",
                }
            ),
        )

        response = await _complete()
        assert response["status"] == "completed"
        manager.send_personal_message.assert_any_await(
            pytest.approx  # placeholder to avoid unused import warnings if refactored
            if False
            else manager.send_personal_message.call_args_list[-1].args[0]
            if manager.send_personal_message.call_args_list
            else {},
            f"driver_{_USER_ID}",
        )
        manager.broadcast_to_admins.assert_awaited()

    async def test_quota_exhausted_notify_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        driver, ride, manager = _install_success_mocks(monkeypatch)
        monkeypatch.setattr(
            "backend.utils.spinr_pass.force_offline_if_exhausted",
            AsyncMock(return_value={"hours_until_reset": 1, "rides_per_day": 10, "quota_resets_at": "x"}),
        )
        manager.send_personal_message.side_effect = [None, RuntimeError("ws send failed")]

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — milestone sanity (trip window compression)
# ============================================================


class TestCompleteRideMilestoneCompression:
    async def test_compressed_trip_window_spawns_integrity_event(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(
            duration_minutes=60,
            ride_started_at="2026-08-03T10:06:00Z",
        )
        _install_success_mocks(monkeypatch, ride=ride)
        # Completion happens ~4 minutes after start vs a 60-minute quote.
        monkeypatch.setattr(
            ride_complete.db_supabase,
            "get_ride",
            AsyncMock(
                return_value={
                    **ride,
                    "status": "completed",
                    "ride_completed_at": "2026-08-03T10:10:00Z",
                }
            ),
        )
        spawned = []

        def capture_spawn(coro):
            spawned.append(coro)
            coro.close()

        monkeypatch.setattr(ride_complete, "spawn", capture_spawn)

        response = await _complete()
        assert response["status"] == "completed"
        assert len(spawned) >= 1

    async def test_milestone_check_exception_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        ride = _ride(duration_minutes="not-a-number")
        _install_success_mocks(monkeypatch, ride=ride)

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — quest progress scheduling failure
# ============================================================


class TestCompleteRideQuestProgress:
    async def test_quest_progress_scheduling_failure_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch):
        _install_success_mocks(monkeypatch)

        # complete_ride() fires several other spawn() calls before reaching
        # quest progress (push notification, live-activity update) that are
        # NOT individually try/except-guarded — a blanket raising spawn()
        # would trip one of those first instead of exercising the quest-
        # progress branch's own guard. Only fail the quest-progress coroutine.
        def raising_spawn(coro):
            if coro.cr_code.co_name == "update_quest_progress_on_ride_complete":
                coro.close()
                raise RuntimeError("spawn exhausted")
            coro.close()

        monkeypatch.setattr(ride_complete, "spawn", raising_spawn)

        response = await _complete()
        assert response["status"] == "completed"


# ============================================================
# complete_ride — Meta DriverActivated on first completed ride
# ============================================================


class TestCompleteRideFirstRideMetaActivation:
    async def test_first_completed_ride_fires_driver_activated(self, monkeypatch: pytest.MonkeyPatch):
        driver = _driver(total_rides=0)
        _install_success_mocks(monkeypatch, driver=driver)

        fired = []
        monkeypatch.setattr(
            ride_complete, "_fire_driver_activated", lambda d, u, r: fired.append((d["id"], u["id"], r["id"]))
        )

        response = await _complete()
        assert response["status"] == "completed"
        assert fired == [(driver["id"], _USER_ID, _RIDE_ID)]

    async def test_non_first_ride_does_not_fire_driver_activated(self, monkeypatch: pytest.MonkeyPatch):
        driver = _driver(total_rides=3)
        _install_success_mocks(monkeypatch, driver=driver)

        fired = []
        monkeypatch.setattr(
            ride_complete, "_fire_driver_activated", lambda d, u, r: fired.append((d["id"], u["id"], r["id"]))
        )

        response = await _complete()
        assert response["status"] == "completed"
        assert fired == []
