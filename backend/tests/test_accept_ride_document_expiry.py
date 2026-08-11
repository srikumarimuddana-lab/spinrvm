"""P1 #12: accept_ride must re-check document/subscription-adjacent expiry
mid-session, not rely solely on the 12h document_expiry / 6h subscription
background sweeps.

Prior behaviour: accept_ride only checked ``driver.status == "suspended"``.
A driver whose license/insurance/vehicle-inspection/CRC-VSC document expired
while already online could keep accepting NEW rides until the next sweep
tick (up to 12h) — a regulatory + insurance-liability gap, not just UX.

Fix under test: ``backend/routes/drivers/_shared.py::check_driver_documents_current``,
wired into ``accept_ride`` (backend/routes/drivers/ride_flow.py) right after
the existing suspended-status gate.

See docs/change-log/2026-08-11-accept-ride-mid-session-expiry-check.md.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

RIDER_ID = "rider-p1-12"
DRIVER_USER_ID = "driver-user-p1-12"
DRIVER_ID = "driver-row-p1-12"
RIDE_ID = "ride-p1-12"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _driver_row(**extra) -> dict:
    row = {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "P1-12 Driver",
        "status": "active",
        "is_online": True,
        "is_available": True,
        "rating": 4.9,
        "lat": 50.41,
        "lng": -104.65,
        "license_expiry_date": None,
        "insurance_expiry_date": None,
        "vehicle_inspection_expiry_date": None,
        "background_check_expiry_date": None,
    }
    row.update(extra)
    return row


def _ride_row(status: str = "driver_assigned", driver_id=DRIVER_ID) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "driver_id": driver_id,
        "pickup_lat": 50.41,
        "pickup_lng": -104.65,
        "dropoff_lat": 50.45,
        "dropoff_lng": -104.60,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _run_accept():
    from backend.routes import drivers as drivers_mod

    return asyncio.run(drivers_mod.accept_ride(ride_id=RIDE_ID, current_user={"id": DRIVER_USER_ID}))


def _patched(get_rows_side_effect, update_one_mock=None):
    """Common patch set for accept_ride, parameterised on the get_rows
    side_effect (drives what 'drivers' / 'driver_documents' queries see).

    Returns an already-composed ``ExitStack`` (itself a context manager) so
    callers can do ``with patches:`` without juggling a tuple of individual
    ``patch(...)`` objects.
    """
    update_one_mock = update_one_mock or AsyncMock(return_value=None)
    stack = ExitStack()
    stack.enter_context(patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride_row())))
    stack.enter_context(
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect))
    )
    stack.enter_context(patch("backend.routes.drivers._deps.db.update_one", update_one_mock))
    stack.enter_context(
        patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=_ride_row("driver_accepted")))
    )
    stack.enter_context(patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()))
    stack.enter_context(patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()))
    stack.enter_context(patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()))
    return stack, update_one_mock


class TestAcceptRideRejectsExpiredLegacyDocumentField:
    def test_expired_license_blocks_accept_and_suspends_driver(self):
        from backend.utils.error_handling import SpinrException
        from backend.utils.error_keys import ErrorKeys

        expired = _iso(datetime.now(timezone.utc) - timedelta(days=1))
        driver = _driver_row(license_expiry_date=expired)

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                return []
            return []

        patches, update_one_mock = _patched(_get_rows)
        with patches:
            with pytest.raises(SpinrException) as excinfo:
                _run_accept()

        assert excinfo.value.status_code == 400
        assert excinfo.value.message_key == ErrorKeys.DRIVER_DOCUMENTS_EXPIRED

        # Mirrors the background sweep: driver flipped to suspended so a
        # retried accept (or the next go-online) shows a clear explanation
        # instead of repeating this same rejection forever.
        suspend_calls = [
            c
            for c in update_one_mock.call_args_list
            if c.args[0] == "drivers" and c.args[2].get("status") == "suspended"
        ]
        assert suspend_calls, f"expected a suspend write to 'drivers'; got {update_one_mock.call_args_list}"
        assert suspend_calls[0].args[2]["is_online"] is False
        assert suspend_calls[0].args[2]["is_available"] is False

    def test_expired_insurance_blocks_accept(self):
        from backend.utils.error_handling import SpinrException
        from backend.utils.error_keys import ErrorKeys

        expired = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        driver = _driver_row(insurance_expiry_date=expired)

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                return []
            return []

        patches, _ = _patched(_get_rows)
        with patches:
            with pytest.raises(SpinrException) as excinfo:
                _run_accept()
        assert excinfo.value.status_code == 400
        assert excinfo.value.message_key == ErrorKeys.DRIVER_DOCUMENTS_EXPIRED

    def test_expired_vehicle_inspection_blocks_accept(self):
        from backend.utils.error_handling import SpinrException

        expired = _iso(datetime.now(timezone.utc) - timedelta(days=30))
        driver = _driver_row(vehicle_inspection_expiry_date=expired)

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            return []

        patches, _ = _patched(_get_rows)
        with patches:
            with pytest.raises(SpinrException) as excinfo:
                _run_accept()
        assert excinfo.value.status_code == 400

    def test_expired_background_check_blocks_accept(self):
        from backend.utils.error_handling import SpinrException

        expired = _iso(datetime.now(timezone.utc) - timedelta(days=400))
        driver = _driver_row(background_check_expiry_date=expired)

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            return []

        patches, _ = _patched(_get_rows)
        with patches:
            with pytest.raises(SpinrException) as excinfo:
                _run_accept()
        assert excinfo.value.status_code == 400


class TestAcceptRideRejectsExpiredApprovedDocumentUpload:
    def test_expired_approved_driver_documents_row_blocks_accept(self):
        """The dynamic driver_documents table (a re-uploaded, re-approved doc)
        takes precedence over the legacy column — same as go_online. An
        approved-but-since-expired row here must block even when the legacy
        column was never populated."""
        from backend.utils.error_handling import SpinrException
        from backend.utils.error_keys import ErrorKeys

        driver = _driver_row()  # all legacy fields None
        expired = _iso(datetime.now(timezone.utc) - timedelta(days=2))
        approved_docs = [
            {
                "driver_id": DRIVER_ID,
                "status": "approved",
                "document_type": "Vehicle Insurance",
                "expiry_date": expired,
                "uploaded_at": _iso(datetime.now(timezone.utc) - timedelta(days=100)),
            }
        ]

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                return approved_docs
            return []

        patches, _ = _patched(_get_rows)
        with patches:
            with pytest.raises(SpinrException) as excinfo:
                _run_accept()
        assert excinfo.value.status_code == 400
        assert excinfo.value.message_key == ErrorKeys.DRIVER_DOCUMENTS_EXPIRED


class TestAcceptRideAllowsValidDriver:
    def test_no_expiry_issues_accept_proceeds_unaffected(self):
        """Regression: a driver with no expired documents (legacy fields all
        None, no driver_documents rows) must accept exactly as before — the
        new gate must not introduce a false positive on the happy path."""
        driver = _driver_row()

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                return []
            return []

        guard_ok = type("_Guard", (), {"modified_count": 1})()
        update_one_mock = AsyncMock(return_value=guard_ok)
        patches, _ = _patched(_get_rows, update_one_mock=update_one_mock)
        with patches:
            result = _run_accept()

        assert result["success"] is True

    def test_future_expiry_and_valid_approved_doc_accept_proceeds(self):
        """Both a not-yet-expired legacy field AND a valid (future-expiry)
        approved driver_documents row must not block acceptance."""
        future = _iso(datetime.now(timezone.utc) + timedelta(days=200))
        driver = _driver_row(license_expiry_date=future)
        approved_docs = [
            {
                "driver_id": DRIVER_ID,
                "status": "approved",
                "document_type": "Vehicle Insurance",
                "expiry_date": future,
                "uploaded_at": _iso(datetime.now(timezone.utc) - timedelta(days=10)),
            }
        ]

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                return approved_docs
            return []

        guard_ok = type("_Guard", (), {"modified_count": 1})()
        update_one_mock = AsyncMock(return_value=guard_ok)
        patches, _ = _patched(_get_rows, update_one_mock=update_one_mock)
        with patches:
            result = _run_accept()

        assert result["success"] is True


class TestAcceptRideFailsClosedOnCheckError:
    def test_driver_documents_lookup_error_returns_503_not_silent_success(self):
        """A DB error during the mid-session expiry re-check must fail CLOSED
        (503) — never let the accept through unverified. Same posture as
        go_online's own document-check error handling."""

        driver = _driver_row()

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "driver_documents":
                raise RuntimeError("simulated DB outage")
            return []

        patches, update_one_mock = _patched(_get_rows)
        with patches:
            with pytest.raises(HTTPException) as excinfo:
                _run_accept()

        assert excinfo.value.status_code == 503
        # No accept-claim write must have happened — the failure must block
        # before the ride is touched, not after a half-completed accept.
        claim_writes = [c for c in update_one_mock.call_args_list if c.args[0] == "rides"]
        assert not claim_writes, "accept must not reach the ride claim write when the expiry check fails closed"
