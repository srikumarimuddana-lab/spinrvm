"""Coverage-closure tests for routes/drivers/ride_complete.py (A1c Sub-tier C).

Written by reading the source only — pytest was never run for this file (nor
for anything else in this batch); a human/CI runs the full suite once at the
end. Test-only change — no application code modified.

ride_complete.py is the driver ride-completion endpoint: state transition
in_progress -> completed (the only valid transition out of in_progress per
CLAUDE.md), fare/route settlement, incentive-bonus claiming, the driver
earnings snapshot, and the Period 3 -> Period 1 SGI insurance-period
transition. At 400 statements / 113 missing this file is large; full 100%
was not attempted. Priority given (per the assignment) to:
  * money settlement (route-geometry retry/fallback, PGRST204 retry,
    incentive-claim loop, driver_earnings_snapshot)
  * the atomic in_progress -> completed state-transition guard and its
    concurrent-completion race path
  * the Period 3 -> Period 1 insurance transition call
  * the daily Spinr Pass quota "auto_offline" notification tail
  * the route-integrity / gps-filter settings-mode guards (_get_route_
    integrity_mode / _get_gps_distance_filter_mode)

Not chased here (smaller/scattered, lower marginal value per test written):
the "invalid_capture_time" branch of _completion_fix_rejection (structurally
unreachable through the public CompletionFix constructor — captured_at is a
required, pydantic-validated ``datetime``, so parse_iso_utc(fix.captured_at)
can only return None if fix.captured_at were somehow None), the meta-pixel
send-failure inner except in _fire_driver_activated, and the best-effort
admin-broadcast except at the very end of complete_ride (already marked
``# pragma: no cover - best effort`` by the author).

FOUND NOT FIXED (flagged, not fixed — see the comment on
test_complete_ride_incentive_claims_recorded_and_snapshot_includes_bonus
below): the active-incentives lookup (ride_complete.py:759-762) builds its
service_area_id OR-filter with a raw f-string interpolated directly into
`.or_()`, bypassing the repository layer's escaping helpers that CLAUDE.md's
"Query filters" convention requires for exactly this reason. Low
exploitability today (service_area_id is an internal UUID, not directly
user-supplied), but nothing pins it to UUID shape at that call site, and nothing
routes it through `_postgrest_or_value`.

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md, and the
docstring at the top of test_driver_ride_flow_coverage.py for the fuller
writeup):
- `db_supabase` is a *module reference* shared by every importer, so
  `patch("backend.routes.drivers._deps.db_supabase.<fn>")` affects both
  `db_supabase.<fn>(...)` and `ride_complete.db_supabase.<fn>(...)` call sites.
- `_deps.manager`, `_deps.record_period_transition`, `_deps.send_push_notification`
  are looked up dynamically as `_deps.<name>` inside ride_complete.py (it does
  NOT import them into its own namespace), so they are patched at
  `backend.routes.drivers._deps.<name>`.
- `spawn`, `flush_driver_breadcrumbs`, `send_live_activity_update`,
  `persist_trip_location_batch`, `mark_route_pending`, `load_ride_breadcrumbs`,
  `compute_trip_distances`, `record_ride_period_distances`,
  `_get_route_integrity_mode`, `_get_gps_distance_filter_mode` ARE imported
  directly into ride_complete.py's own namespace, so they are patched at
  `backend.routes.drivers.ride_complete.<name>`.
- `force_offline_if_exhausted`, `record_integrity_event`,
  `update_quest_progress_on_ride_complete`, `auto_settle_guest_corporate`,
  `send_driver_activated`, and the settings_loader `get_app_settings` used by
  the inline fare-lock check are imported *inside* the function body on every
  call (dual-import pattern) — patched at the *source* module via
  `_patch_both()` below, which patches both the `backend.<mod>` and bare
  `<mod>` spellings (whichever is importable in this process) with the same
  mock object so the assertion holds regardless of which import path the
  loaded ride_complete module resolves through.
"""

from __future__ import annotations

import importlib
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-rc-1"
_USER_ID = "user-rc-1"
_RIDE_ID = "ride-rc-1"
_RIDER_ID = "rider-rc-1"


class _Patches:
    """Enter a list of patch() context managers together (see
    test_driver_ride_flow_coverage.py for why this exists instead of a bare
    ``with (*cms, patch(...) as x):``)."""

    def __init__(self, *ctx_managers):
        self._ctx_managers = ctx_managers
        self._stack = None

    def __enter__(self):
        self._stack = ExitStack()
        return [self._stack.enter_context(cm) for cm in self._ctx_managers]

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _patch_both(dotted_attr: str, mock_obj):
    """patch() context managers for both the `backend.<mod>` and bare `<mod>`
    spellings of a dual-imported attribute, skipping whichever spelling isn't
    importable in this process. Both patched attributes share `mock_obj` so
    assertions work regardless of which import path ride_complete resolved."""
    mod_path, attr = dotted_attr.rsplit(".", 1)
    cms = []
    for prefix in ("backend.", ""):
        full_mod = prefix + mod_path
        try:
            importlib.import_module(full_mod)
        except ImportError:
            continue
        cms.append(patch(f"{full_mod}.{attr}", mock_obj))
    return cms


def _spawn_close(coro):
    """spawn() replacement that just closes the coroutine (no leaked-coro warning)."""
    coro.close()


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
        "dropoff_lat": 50.445,
        "dropoff_lng": -104.618,
        "planned_distance_km": 5.0,
        "distance_km": 5.0,
        "base_fare": 3.0,
        "distance_fare": 4.0,
        "time_fare": 2.0,
        "total_fare": 9.0,
        "booking_fee": 0.5,
        "driver_accepted_at": "2026-07-17T21:00:00Z",
        "ride_started_at": "2026-07-17T21:10:00Z",
        "duration_minutes": 15,
        "vehicle_type_id": "vt-1",
        "service_area_id": "sa-1",
    }
    base.update(kw)
    return base


def _distances_ns(**kw):
    base = dict(
        actual_distance_km=5.0,
        actual_distance_km_haversine=5.1,
        actual_distance_km_road=5.0,
        phase_distances={"navigating_to_pickup": 1.0, "trip_in_progress": 5.0},
        phase_durations={"navigating_to_pickup": 3, "trip_in_progress": 12},
        phase_polylines={},
        pickup_to_driver_km=1.0,
        road_polyline=[],
        gps_points_count=42,
        route_quality={"confidence": "high"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _default_update_one(ride):
    async def _impl(table, filters, updates, **kw):
        if table == "ride_routes":
            return {"ride_id": (filters or {}).get("ride_id")}
        if table == "rides" and (filters or {}).get("status") == "in_progress":
            return {**ride, **updates, "status": "completed"}
        return {"id": (filters or {}).get("id")}

    return _impl


def _base_patches(
    *,
    driver=None,
    ride=None,
    completed_ride=None,
    update_one_impl=None,
    incentives_data=None,
    force_offline_result=None,
    force_offline_exc=None,
    run_sync_impl=None,
    compute_trip_distances_result=None,
    compute_trip_distances_exc=None,
):
    driver = driver if driver is not None else _driver()
    ride = ride if ride is not None else _ride()
    completed_ride = (
        completed_ride
        if completed_ride is not None
        else {**ride, "status": "completed", "grand_total": ride.get("total_fare")}
    )

    async def fake_get_rows(table, filters=None, **kw):
        if table == "drivers":
            return [driver]
        if table == "rides":
            return [ride]
        return []

    update_one_calls: list = []
    _impl = update_one_impl or _default_update_one(ride)

    async def fake_update_one(table, filters, updates, **kw):
        update_one_calls.append((table, dict(filters or {}), dict(updates or {})))
        return await _impl(table, filters, updates, **kw)

    if run_sync_impl is None:
        incentives_result = SimpleNamespace(data=incentives_data if incentives_data is not None else [])

        async def run_sync_impl(fn):
            return incentives_result

    ffo = (
        AsyncMock(side_effect=force_offline_exc)
        if force_offline_exc is not None
        else AsyncMock(return_value=force_offline_result)
    )
    record_integrity_mock = AsyncMock()
    quest_mock = AsyncMock()
    guest_settle_mock = AsyncMock()
    driver_activated_mock = AsyncMock()

    if compute_trip_distances_exc is not None:
        ctd = AsyncMock(side_effect=compute_trip_distances_exc)
    else:
        ctd = AsyncMock(return_value=compute_trip_distances_result or _distances_ns())

    patches = [
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
        patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=run_sync_impl)),
        patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
        patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=completed_ride)),
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        patch(
            "backend.routes.drivers._deps.manager",
            MagicMock(
                send_personal_message=AsyncMock(),
                broadcast_ride_status=AsyncMock(),
                broadcast_to_admins=AsyncMock(),
            ),
        ),
        patch("backend.routes.drivers.ride_complete.flush_driver_breadcrumbs", AsyncMock()),
        patch("backend.routes.drivers.ride_complete.spawn", MagicMock(side_effect=_spawn_close)),
        patch("backend.routes.drivers.ride_complete.send_live_activity_update", AsyncMock()),
        patch("backend.routes.drivers.ride_complete.load_ride_breadcrumbs", AsyncMock(return_value=[])),
        patch("backend.routes.drivers.ride_complete.compute_trip_distances", ctd),
        patch("backend.routes.drivers.ride_complete.record_ride_period_distances", AsyncMock()),
        patch("backend.routes.drivers.ride_complete.mark_route_pending", AsyncMock()),
        patch("backend.routes.drivers.ride_complete._get_gps_distance_filter_mode", AsyncMock(return_value="off")),
        patch("backend.routes.drivers.ride_complete.asyncio.sleep", AsyncMock()),
        *_patch_both("utils.spinr_pass.force_offline_if_exhausted", ffo),
        *_patch_both("utils.distance_integrity.record_integrity_event", record_integrity_mock),
        *_patch_both("utils.quest_tracker.update_quest_progress_on_ride_complete", quest_mock),
        *_patch_both("services.payment_service.auto_settle_guest_corporate", guest_settle_mock),
        *_patch_both("services.meta_conversions_service.send_driver_activated", driver_activated_mock),
    ]
    extras = SimpleNamespace(
        update_one_calls=update_one_calls,
        force_offline=ffo,
        record_integrity=record_integrity_mock,
        quest=quest_mock,
        guest_settle=guest_settle_mock,
        driver_activated=driver_activated_mock,
    )
    return patches, driver, ride, completed_ride, extras


async def _call_complete_ride(driver, current_user_id=_USER_ID):
    from backend.routes.drivers import ride_complete

    return await ride_complete.complete_ride(ride_id=_RIDE_ID, current_user={"id": current_user_id})


# ============================================================
# _get_route_integrity_mode (lines 141-157)
# ============================================================


async def test_route_integrity_mode_settings_read_failure_raises_503():
    from fastapi import HTTPException

    from backend.routes.drivers import ride_complete

    gas = AsyncMock(side_effect=RuntimeError("settings db unreachable"))
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        with pytest.raises(HTTPException) as exc_info:
            await ride_complete._get_route_integrity_mode()
    assert exc_info.value.status_code == 503
    assert "temporarily unavailable" in exc_info.value.detail


async def test_route_integrity_mode_invalid_value_raises_503():
    from fastapi import HTTPException

    from backend.routes.drivers import ride_complete

    gas = AsyncMock(return_value={"route_integrity_v2_mode": "banana"})
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        with pytest.raises(HTTPException) as exc_info:
            await ride_complete._get_route_integrity_mode()
    assert exc_info.value.status_code == 503
    assert "invalid" in exc_info.value.detail.lower()


@pytest.mark.parametrize("mode", ["off", "shadow", "on", "ON", "Shadow"])
async def test_route_integrity_mode_valid_values_pass_through(mode):
    from backend.routes.drivers import ride_complete

    gas = AsyncMock(return_value={"route_integrity_v2_mode": mode})
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        result = await ride_complete._get_route_integrity_mode()
    assert result == mode.lower()


# ============================================================
# _get_gps_distance_filter_mode (lines 160-182) — read failure never blocks
# completion (deliberately different fail-open behavior from route-integrity)
# ============================================================


async def test_gps_filter_mode_settings_read_failure_defaults_to_off():
    from backend.routes.drivers import ride_complete

    gas = AsyncMock(side_effect=RuntimeError("settings db unreachable"))
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        result = await ride_complete._get_gps_distance_filter_mode()
    assert result == "off"


async def test_gps_filter_mode_invalid_value_defaults_to_off():
    from backend.routes.drivers import ride_complete

    gas = AsyncMock(return_value={"gps_distance_filter_mode": "not-a-mode"})
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        result = await ride_complete._get_gps_distance_filter_mode()
    assert result == "off"


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
async def test_gps_filter_mode_valid_values_pass_through(mode):
    from backend.routes.drivers import ride_complete

    gas = AsyncMock(return_value={"gps_distance_filter_mode": mode})
    with _Patches(*_patch_both("settings_loader.get_app_settings", gas)):
        result = await ride_complete._get_gps_distance_filter_mode()
    assert result == mode


# ============================================================
# _completion_fix_rejection — extra rejection branches not covered by
# test_ride_completion_location.py (which only exercises stale_capture and
# the distance-band happy paths)
# ============================================================


def _fix(**kw):
    from backend.routes.drivers import ride_complete

    base = dict(
        recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
        sequence_number=1,
        captured_at=datetime.now(timezone.utc),
        lat=50.445,
        lng=-104.618,
        accuracy=8,
    )
    base.update(kw)
    return ride_complete.CompletionFix(**base)


def test_completion_fix_rejection_mocked_location():
    from backend.routes.drivers import ride_complete

    fix = _fix(mocked=True)
    assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "mocked_location"


def test_completion_fix_rejection_low_accuracy():
    from backend.routes.drivers import ride_complete

    fix = _fix(accuracy=150)
    assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "low_accuracy"


def test_completion_fix_rejection_invalid_coordinate():
    from backend.routes.drivers import ride_complete

    fix = _fix(lat=0, lng=0)
    assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) == "invalid_coordinate"


def test_completion_fix_rejection_future_capture():
    from backend.routes.drivers import ride_complete

    now = datetime.now(timezone.utc)
    fix = _fix(captured_at=now + timedelta(seconds=60))
    assert ride_complete._completion_fix_rejection(fix, now) == "future_capture"


def test_completion_fix_rejection_accepts_a_clean_fix():
    from backend.routes.drivers import ride_complete

    fix = _fix()
    assert ride_complete._completion_fix_rejection(fix, datetime.now(timezone.utc)) is None


# ============================================================
# complete_ride — state machine + happy path
# ============================================================


async def test_complete_ride_happy_path_transitions_to_completed_and_period_1():
    """Pins the CLAUDE.md invariants: status -> completed, and the SGI
    Period 3 -> Period 1 insurance transition fires with no ride_id (the
    driver is free, no longer on any ride)."""
    patches, driver, ride, completed_ride, extras = _base_patches()
    with _Patches(*patches) as mocks:
        record_period_transition = mocks[7]  # index of the record_period_transition patch above
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    record_period_transition.assert_awaited_once_with(_DRIVER_ID, 1)
    # The atomic in_progress-filtered completion update must have happened.
    complete_calls = [c for c in extras.update_one_calls if c[0] == "rides" and c[1].get("status") == "in_progress"]
    assert len(complete_calls) == 1
    assert complete_calls[0][2]["status"] == "completed"


async def test_complete_ride_wrong_state_raises_ride_state_error():
    """Guards the state-machine invariant: only in_progress -> completed is valid."""
    from backend.utils.error_handling import RideStateError

    driver = _driver()
    ride = _ride(status="driver_arrived")
    patches, driver, ride, completed_ride, extras = _base_patches(driver=driver, ride=ride)
    with _Patches(*patches):
        with pytest.raises(RideStateError):
            await _call_complete_ride(driver)


async def test_complete_ride_concurrent_completion_raises_ride_state_error():
    """The atomic CAS filter ({'status': 'in_progress'}) matches zero rows when
    a concurrent request already completed/cancelled the ride — must surface
    as a RideStateError, not silently succeed a second settlement."""
    from backend.utils.error_handling import RideStateError

    ride = _ride()

    async def update_one_impl(table, filters, updates, **kw):
        if table == "ride_routes":
            return {"ride_id": filters.get("ride_id")}
        if table == "rides" and (filters or {}).get("status") == "in_progress":
            return None  # 0 rows matched — lost the race
        return {"id": (filters or {}).get("id")}

    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, update_one_impl=update_one_impl)
    with _Patches(*patches):
        with pytest.raises(RideStateError) as exc_info:
            await _call_complete_ride(driver)
    assert "already processed" in str(exc_info.value.message).lower() or "already processed" in str(exc_info.value)


# ============================================================
# Route geometry persistence retry + failure fallback (lines 458-493)
# ============================================================


async def test_complete_ride_route_geometry_save_fails_all_retries_records_failure_on_ride():
    """All 3 ride_routes upsert attempts fail -> route_geometry_status is
    recorded as 'failed' with the error text on the rides row (best-effort
    side write; must not fail settlement itself)."""
    ride = _ride()
    boom = RuntimeError("could not upsert ride_routes: connection reset")

    async def update_one_impl(table, filters, updates, **kw):
        if table == "ride_routes":
            # prepare_completion_location's own "missing tail" marker write
            # (default completion_request has no completion_fix) also targets
            # "ride_routes" and must be left healthy — only the route-geometry
            # payload write (identified by its "save_status" key) is made to
            # fail here, otherwise prepare_completion_location's own
            # try/except would raise a 503 before completion even starts.
            if "save_status" in (updates or {}):
                raise boom
            return {"ride_id": (filters or {}).get("ride_id")}
        if table == "rides" and (filters or {}).get("status") == "in_progress":
            return {**ride, **updates, "status": "completed"}
        return {"id": (filters or {}).get("id")}

    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, update_one_impl=update_one_impl)
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    # The fallback failure-record write: table="rides", no status filter, and
    # it must carry the failed status + truncated error text.
    fallback_calls = [
        c
        for c in extras.update_one_calls
        if c[0] == "rides" and "route_geometry_status" in c[2] and "status" not in c[1]
    ]
    assert len(fallback_calls) == 1
    assert fallback_calls[0][2]["route_geometry_status"] == "failed"
    assert "connection reset" in fallback_calls[0][2]["route_geometry_error"]
    # 3 retry attempts against the route-geometry payload write specifically
    # (the healthy missing-tail marker write is a separate "ride_routes" call
    # and is excluded here).
    route_geometry_attempts = [c for c in extras.update_one_calls if c[0] == "ride_routes" and "save_status" in c[2]]
    assert len(route_geometry_attempts) == 3


# ============================================================
# PGRST204 schema-cache-miss retry on the main completion update
# (lines 639-664)
# ============================================================


async def test_complete_ride_pgrst204_retry_falls_back_to_minimal_fields():
    """A PGRST204 ('column not found' — schema cache lag on an older deploy)
    on the full-field completion update must retry with only the safe/legacy
    column set rather than failing the driver's completion outright."""
    ride = _ride()
    call_count = {"rides_complete": 0}

    class _FakeDbError(Exception):
        pass

    async def update_one_impl(table, filters, updates, **kw):
        if table == "ride_routes":
            return {"ride_id": filters.get("ride_id")}
        if table == "rides" and (filters or {}).get("status") == "in_progress":
            call_count["rides_complete"] += 1
            if call_count["rides_complete"] == 1:
                raise _FakeDbError("Could not find the 'phase_distances' column of 'rides' in the schema cache")
            # Retry: only "safe_keys" should have been passed this time.
            assert set(updates.keys()) <= {"status", "ride_completed_at", "payment_status", "updated_at", "distance_km"}
            return {**ride, **updates, "status": "completed"}
        return {"id": (filters or {}).get("id")}

    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, update_one_impl=update_one_impl)
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    assert call_count["rides_complete"] == 2


# ============================================================
# Incentive claims (lines 744-795) — driver_earnings_snapshot money math
# ============================================================


async def test_complete_ride_incentive_claims_recorded_and_snapshot_includes_bonus():
    # Fixed (2026-08-03): the active-incentives lookup's service_area_id
    # OR-filter now routes `sa_id` through `_postgrest_or_value`
    # (repositories/_base.py) instead of a raw f-string, per CLAUDE.md's
    # "Query filters" convention. `ride["service_area_id"] = "sa-1"` below
    # exercises this code path; a plain UUID has no reserved characters so
    # the resulting or-clause is unchanged by the fix (this test doesn't
    # assert on the raw clause string, only on the end-to-end claim result).
    ride = _ride()
    incentives = [
        {"id": "inc-1", "bonus_amount": 2.5, "vehicle_type_id": None},  # matches (no type restriction)
        {"id": "inc-2", "bonus_amount": 1.0, "vehicle_type_id": "vt-1"},  # matches driver's vehicle type
        {"id": "inc-3", "bonus_amount": 0, "vehicle_type_id": None},  # zero bonus — skipped
    ]
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, incentives_data=incentives)
    insert_calls = []

    async def fake_insert_one(table, doc):
        insert_calls.append((table, doc))
        return doc

    with _Patches(*patches):
        # Override the default insert_one mock with one that records claim rows.
        with patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert_one)):
            response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    assert len(insert_calls) == 2
    claimed_ids = {doc["incentive_id"] for _, doc in insert_calls}
    assert claimed_ids == {"inc-1", "inc-2"}
    for _, doc in insert_calls:
        assert doc["driver_id"] == _DRIVER_ID
        assert doc["ride_id"] == _RIDE_ID
    # driver_earnings_snapshot write should reflect the $3.50 total bonus.
    snapshot_calls = [c for c in extras.update_one_calls if c[0] == "rides" and "driver_earnings_snapshot" in c[2]]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0][2]["driver_earnings_snapshot"]["incentive"] == pytest.approx(3.5)


async def test_complete_ride_incentive_vehicle_type_mismatch_is_skipped():
    ride = _ride(vehicle_type_id="vt-1")
    incentives = [{"id": "inc-suv-only", "bonus_amount": 5.0, "vehicle_type_id": "vt-suv"}]
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, incentives_data=incentives)
    insert_calls = []

    async def fake_insert_one(table, doc):
        insert_calls.append((table, doc))
        return doc

    with _Patches(*patches):
        with patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert_one)):
            response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    assert insert_calls == []


async def test_complete_ride_incentive_lookup_failure_degrades_to_zero_bonus():
    """A DB error fetching active incentives must not fail settlement — the
    ride still completes with zero bonus (best-effort side feature)."""
    ride = _ride()

    async def failing_run_sync(fn):
        raise RuntimeError("ride_incentives table unreachable")

    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, run_sync_impl=failing_run_sync)
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    snapshot_calls = [c for c in extras.update_one_calls if c[0] == "rides" and "driver_earnings_snapshot" in c[2]]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0][2]["driver_earnings_snapshot"]["incentive"] == 0


# ============================================================
# GPS aggregation failure (lines 420-441) — falls back to planned distance
# ============================================================


async def test_complete_ride_gps_aggregation_failure_falls_back_to_planned_distance():
    ride = _ride(planned_distance_km=7.25)
    patches, driver, ride, completed_ride, extras = _base_patches(
        ride=ride, compute_trip_distances_exc=RuntimeError("breadcrumb aggregation crashed")
    )
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    complete_calls = [c for c in extras.update_one_calls if c[0] == "rides" and c[1].get("status") == "in_progress"]
    assert complete_calls[0][2]["actual_distance_km"] == 7.25
    assert complete_calls[0][2]["route_quality"] == {"confidence": "low", "reason": "no_gps_breadcrumbs"}


# ============================================================
# Milestone sanity: trip-window compression detection (lines 904-922)
# ============================================================


async def test_complete_ride_milestone_compression_flagged_when_window_far_short_of_quote():
    started = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    ride = _ride(ride_started_at=started, duration_minutes=15)  # ~1.5 min actual vs 15 quoted
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride)
    with _Patches(*patches):
        await _call_complete_ride(driver)

    # record_integrity_event(...) is handed to spawn() (patched to close the
    # coroutine without awaiting it, per _spawn_close above), so the mock is
    # only ever *called*, never *awaited* — assert on the call, not the await.
    extras.record_integrity.assert_called_once()
    args = extras.record_integrity.call_args.args
    assert args[0] == _RIDE_ID
    assert args[1] == "trip_window_compressed"
    assert args[2]["quoted_duration_minutes"] == 15


async def test_complete_ride_milestone_compression_not_flagged_for_normal_window():
    started = (datetime.now(timezone.utc) - timedelta(minutes=14)).isoformat()
    ride = _ride(ride_started_at=started, duration_minutes=15)  # ~14 min actual vs 15 quoted — not compressed
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride)
    with _Patches(*patches):
        await _call_complete_ride(driver)

    extras.record_integrity.assert_not_called()


# ============================================================
# Daily Spinr Pass quota exhaustion notify tail (lines 855-983)
# ============================================================


async def test_complete_ride_quota_exhausted_notifies_driver_and_admins():
    quota_offline = {
        "rides_per_day": 10,
        "hours_until_reset": 5.4,
        "quota_resets_at": "2026-08-04T00:00:00Z",
    }
    patches, driver, ride, completed_ride, extras = _base_patches(force_offline_result=quota_offline)
    with _Patches(*patches) as mocks:
        manager_mock = mocks[9]  # index of the _deps.manager patch above
        push_mock = mocks[8]  # index of the _deps.send_push_notification patch above
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    auto_offline_calls = [
        c for c in manager_mock.send_personal_message.await_args_list if c.args[0].get("type") == "auto_offline"
    ]
    assert len(auto_offline_calls) == 1
    assert auto_offline_calls[0].args[1] == f"driver_{_USER_ID}"
    status_changed_calls = [
        c for c in manager_mock.broadcast_to_admins.await_args_list if c.args[0].get("type") == "driver_status_changed"
    ]
    assert len(status_changed_calls) == 1
    assert status_changed_calls[0].args[0]["is_online"] is False
    # Push notification for the quota exhaustion (called, not necessarily
    # awaited — the endpoint hands it to spawn() which we replace with a
    # close()-only stub so it never actually runs).
    quota_push_calls = [
        c for c in push_mock.call_args_list if c.kwargs.get("data", {}).get("type") == "quota_exhausted"
    ]
    assert len(quota_push_calls) == 1


async def test_complete_ride_quota_check_failure_is_swallowed():
    """A transient error in force_offline_if_exhausted must not block
    completion — per CLAUDE.md this is a best-effort side feature, not the
    settlement itself."""
    patches, driver, ride, completed_ride, extras = _base_patches(force_offline_exc=RuntimeError("redis down"))
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"


# ============================================================
# Guest corporate auto-settlement fire-and-forget (lines 733-742)
# ============================================================


async def test_complete_ride_guest_corporate_triggers_auto_settle():
    ride = _ride(guest_booking=True, payment_method="company_allowance")
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride)
    with _Patches(*patches):
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    extras.guest_settle.assert_called_once_with(_RIDE_ID)


async def test_complete_ride_non_guest_skips_auto_settle():
    ride = _ride(guest_booking=False)
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride)
    with _Patches(*patches):
        await _call_complete_ride(driver)

    extras.guest_settle.assert_not_called()


# ============================================================
# First-completed-ride Meta DriverActivated fire (lines 332-351, 847-853)
# ============================================================


async def test_complete_ride_first_completion_fires_driver_activated():
    driver = _driver(total_rides=0)
    patches, driver, ride, completed_ride, extras = _base_patches(driver=driver)
    with _Patches(*patches):
        await _call_complete_ride(driver)

    extras.driver_activated.assert_called_once()


async def test_complete_ride_non_first_completion_skips_driver_activated():
    driver = _driver(total_rides=3)
    patches, driver, ride, completed_ride, extras = _base_patches(driver=driver)
    with _Patches(*patches):
        await _call_complete_ride(driver)

    extras.driver_activated.assert_not_called()


# ============================================================
# Rider-notification gating
# ============================================================


async def test_complete_ride_no_rider_id_skips_rider_notification_block():
    """When a ride somehow has no rider_id, the rider WS/push notification
    block (lines 872-892) must be skipped without error, but the admin
    broadcast (which does not gate on rider_id) still fires."""
    ride = _ride(rider_id=None)
    completed_ride = {**ride, "status": "completed", "rider_id": None}
    patches, driver, ride, completed_ride, extras = _base_patches(ride=ride, completed_ride=completed_ride)
    with _Patches(*patches) as mocks:
        manager_mock = mocks[9]
        response = await _call_complete_ride(driver)

    assert response["status"] == "completed"
    ride_completed_personal = [
        c for c in manager_mock.send_personal_message.await_args_list if c.args[0].get("type") == "ride_completed"
    ]
    assert ride_completed_personal == []
