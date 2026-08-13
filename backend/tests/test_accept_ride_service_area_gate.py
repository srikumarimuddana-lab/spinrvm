"""
Route-level tests for the accept-time cross-service-area gate.

The dispatch filter (``routes/rides/matching.py``) is the primary guard, but it
is not the only way a driver reaches ``POST /drivers/rides/{id}/accept``: a stale
``ride_offers`` row from before the driver's area changed, a dispatch that ran
while the area flag was off, or a driver calling the endpoint with a ride_id
learned another way. This gate is the backstop, mirroring how the Spinr Pass
subscription guard is enforced at go-online, dispatch AND accept.

These exercise the real ``accept_ride`` coroutine — not a replayed copy of its
logic — so the route wiring is covered too.

Assertion discipline: the allow-path tests do NOT merely assert "no 403". An
exception raised *before* the gate (a bad patch target, say) also has no 403 and
would make them pass vacuously. Instead they assert the quota gate — the very
next statement after the area gate — was actually reached. Block-path tests
assert the converse: 403 raised AND the quota gate never reached.
"""

import contextlib
import importlib
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.anyio

RIDE_ID = "ride_area_gate"
DRIVER_ID = "drv_area_gate"
DRIVER_USER_ID = "user_area_gate"

AREAS = {
    "regina": {"id": "regina", "is_active": True},
    "regina_airport": {"id": "regina_airport", "is_active": True, "parent_service_area_id": "regina"},
    "saskatoon": {"id": "saskatoon", "is_active": True},
}


def _ride(service_area_id="regina"):
    return {
        "id": RIDE_ID,
        "rider_id": "rider_1",
        "pickup_lat": 50.4452,
        "pickup_lng": -104.6189,
        "dropoff_lat": 50.4500,
        "dropoff_lng": -104.6100,
        "vehicle_type_id": "economy",
        "requires_wav": False,
        "status": "searching",
        "driver_id": None,
        "service_area_id": service_area_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _driver(service_area_id):
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "service_area_id": service_area_id,
        "vehicle_type_id": "economy",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": 50.4452,
        "lng": -104.6189,
    }


def _quota_patch_targets():
    """Patch ``assert_quota_available`` on whichever spelling of the module is
    live. ``utils.spinr_pass`` and ``backend.utils.spinr_pass`` can both exist as
    distinct objects under this repo's dual-import pattern, and accept_ride
    imports the symbol inside the function body — so the source module attribute
    is what must be replaced, on every spelling present."""
    targets = []
    for name in ("backend.utils.spinr_pass", "utils.spinr_pass"):
        try:
            importlib.import_module(name)
        except ImportError:
            continue
        targets.append(f"{name}.assert_quota_available")
    assert targets, "neither spelling of utils.spinr_pass is importable"
    return targets


async def _call_accept(driver_area, *, ride_area="regina", settings=None):
    """Invoke the real accept_ride. Returns the quota-gate mock so callers can
    assert whether execution got past the area gate."""
    from backend.routes import drivers as drivers_mod

    settings = settings if settings is not None else {"enforce_driver_service_area": True}
    driver_row = _driver(driver_area)

    async def _get_rows(table, filters=None, **kwargs):
        if table == "drivers":
            return [driver_row]
        if table == "service_areas":
            # One whole-table read; the resolver builds the tree in memory.
            return [{"id": a["id"], "parent_service_area_id": a.get("parent_service_area_id")} for a in AREAS.values()]
        return []

    async def _find_one(table, filters=None, **kwargs):
        if table == "service_areas":
            return AREAS.get((filters or {}).get("id"))
        return None

    quota_mock = AsyncMock()

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride(ride_area)))
        )
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows))
        )
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(side_effect=_find_one))
        )
        # Gates that run BEFORE the area gate — neutralised so they can't mask it.
        stack.enter_context(patch("backend.routes.drivers.ride_flow.check_driver_documents_current", AsyncMock()))
        stack.enter_context(patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)))
        with contextlib.suppress(ModuleNotFoundError, AttributeError):
            stack.enter_context(patch("settings_loader.get_app_settings", AsyncMock(return_value=settings)))
        # The probe: first statement after the area gate.
        for target in _quota_patch_targets():
            stack.enter_context(patch(target, quota_mock))
        # NOTE: deliberately no patches on ``_deps.db.*`` here. ``_deps.db`` IS
        # ``_deps.db_supabase`` (same module object, two names), so patching
        # ``_deps.db.find_one`` silently replaces the ``db_supabase.find_one``
        # mock installed above — which made the area-tree walk see None for
        # every service_areas row and wrongly blocked in-area drivers. The
        # accept path is allowed to fail past the gate; the quota probe, not a
        # successful accept, is what these tests assert on.
        stack.enter_context(patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()))
        stack.enter_context(patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()))
        stack.enter_context(patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()))
        stack.enter_context(patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()))

        raised = None
        try:
            await drivers_mod.accept_ride(ride_id=RIDE_ID, current_user={"id": DRIVER_USER_ID})
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            raised = exc
    return quota_mock, raised


def _status_of(exc):
    return getattr(exc, "status_code", None)


def _assert_blocked_by_area_gate(quota_mock, raised):
    assert raised is not None, "expected the area gate to reject this accept"
    assert _status_of(raised) == 403, f"expected 403 from the area gate, got {raised!r}"
    assert "service area" in str(getattr(raised, "message", raised)).lower()
    assert quota_mock.await_count == 0, "execution continued past the area gate"


def _assert_passed_area_gate(quota_mock, raised):
    """Assert the area gate allowed this accept through.

    The quota gate is the statement immediately after the area gate, so one
    await of it is positive proof the area gate was reached and cleared. This is
    the load-bearing assertion — a "no 403" check alone would also pass if an
    earlier gate or a bad patch target aborted the call before the area gate ran.

    accept_ride legitimately fails *later* in these tests (there is no
    ride_offers row, so the assignment check raises its own 403); that is past
    the gate and out of scope here.
    """
    assert quota_mock.await_count == 1, (
        f"never reached the statement after the area gate (raised={raised!r}) — "
        "the allow path was not actually exercised"
    )
    assert "service area" not in str(getattr(raised, "message", raised) or "").lower(), (
        f"rejected for service area despite passing the gate: {raised!r}"
    )


class TestAcceptRideServiceAreaGate:
    async def test_out_of_area_driver_is_blocked(self):
        """The reported scenario, at accept time: Saskatoon driver, Regina ride."""
        _assert_blocked_by_area_gate(*await _call_accept("saskatoon"))

    async def test_in_area_driver_passes_the_gate(self):
        _assert_passed_area_gate(*await _call_accept("regina"))

    async def test_child_area_driver_passes_for_parent_area_ride(self):
        _assert_passed_area_gate(*await _call_accept("regina_airport", ride_area="regina"))

    async def test_parent_area_driver_passes_for_child_area_ride(self):
        _assert_passed_area_gate(*await _call_accept("regina", ride_area="regina_airport"))

    async def test_unassigned_driver_allowed_by_default(self):
        """NULL service_area_id must not 403 before the backfill lands."""
        _assert_passed_area_gate(*await _call_accept(None))

    async def test_unassigned_driver_blocked_once_lockdown_enabled(self):
        _assert_blocked_by_area_gate(
            *await _call_accept(
                None,
                settings={
                    "enforce_driver_service_area": True,
                    "service_area_allow_unassigned_drivers": False,
                },
            )
        )

    async def test_kill_switch_off_lets_cross_area_accept_through(self):
        """Flag off ⇒ gate bypassed entirely (the rollback path)."""
        _assert_passed_area_gate(*await _call_accept("saskatoon", settings={"enforce_driver_service_area": False}))

    async def test_ride_without_service_area_skips_the_gate(self):
        _assert_passed_area_gate(*await _call_accept("saskatoon", ride_area=None))
