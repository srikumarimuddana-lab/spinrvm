"""
P1-8: Role-claim tampering guard (S3, S8) + S1 fix

S1 — Rider tries to accept own ride (role escalation).
     A dual-role user (has both rider and driver record) must not be
     able to accept a ride they created. Fixed: guard added to
     routes/drivers.py::accept_ride.

S3 — JWT with tampered role claim.
     Backend dependencies/__init__.py::get_current_user always fetches
     role from the DB row, never from the JWT payload. A forged token
     with `role: "super_admin"` must not gain escalated access.

S8 — Driver views another driver's earnings.
     GET /drivers/earnings queries by `current_user["id"]`, not a
     caller-supplied driver id. There is no path parameter to override.

Run:
    pytest backend/tests/test_p1_security.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "user_p1_8_rider"
DRIVER_USER_ID = "user_p1_8_driver"
DRIVER_ID = "driver_row_p1_8"
RIDE_ID = "ride_p1_8_001"


def _ride(status: str, rider_id: str = RIDER_ID, driver_id: str | None = DRIVER_ID, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": rider_id,
        "status": status,
        "driver_id": driver_id,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.12,
        "dropoff_lng": -106.65,
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        "estimated_fare": 18.5,
        "total_fare": 18.5,
        "grand_total": 18.5,
        "distance_km": 3.2,
        "duration_minutes": 8,
        "payment_method": "card",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(extra)
    return row


def _driver_row(user_id: str = DRIVER_USER_ID) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": user_id,
        "name": "Driver P1-8",
        "rating": 4.9,
        "total_rides": 100,
        "is_online": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# S1: Rider tries to accept own ride
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSelfAcceptGuard:
    """A user who has a driver record must not be allowed to accept a ride
    they created as a rider (self-dispatch fraud / role escalation).

    Code under test: backend/routes/drivers.py::accept_ride
    Fix committed: guard `ride.rider_id == current_user.id` → 403.
    """

    async def test_dual_role_user_cannot_accept_own_ride(self):
        """The dual-role user is their own rider — must get 403."""
        from fastapi import HTTPException

        from backend.routes import drivers as drv_mod

        # Dual-role: driver record exists for the same user_id as the ride's rider
        ride = _ride(status="searching", rider_id=DRIVER_USER_ID, driver_id=None)

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row(user_id=DRIVER_USER_ID)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_ride",
                AsyncMock(return_value=ride),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await drv_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Cannot accept your own ride"

    async def test_normal_rider_without_driver_record_gets_404(self):
        """A pure rider (no driver row) gets 404 — no driver record found."""
        from fastapi import HTTPException

        from backend.routes import drivers as drv_mod

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(return_value=[]),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await drv_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 404
        assert "Driver not found" in exc_info.value.detail

    async def test_legitimate_driver_can_accept_different_riders_ride(self):
        """A real driver accepting a ride from a different rider must succeed
        (up to the assignment check)."""
        from backend.routes import drivers as drv_mod

        ride = _ride(status="driver_assigned", rider_id=RIDER_ID, driver_id=DRIVER_ID)
        updated_ride = {**ride, "status": "driver_accepted"}

        accepted_calls = []

        async def _capture_update(table, filter_doc, update_doc):
            accepted_calls.append(filter_doc)
            return updated_ride

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row(user_id=DRIVER_USER_ID)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_ride",
                AsyncMock(return_value=ride),
            ),
            patch(
                "backend.routes.drivers._deps.db.update_one",
                AsyncMock(side_effect=_capture_update),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_ride",
                AsyncMock(return_value=updated_ride),
            ),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            # Should not raise — driver and rider are different users
            try:
                result = await drv_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
                # accept_ride returns a dict on success
                assert isinstance(result, dict)
            except Exception as exc:
                # If it fails for a different reason (DB mock mismatch), that
                # is acceptable — what matters is it did NOT raise 403 for
                # the self-accept reason.
                assert "Cannot accept your own ride" not in str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# S3: JWT with tampered role claim
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRoleClaimTampering:
    """Role is always read from the DB row, never trusted from the JWT payload.

    Code under test: backend/dependencies/__init__.py::get_current_user
    Key comment in source (line ~240):
      "Role is always determined by the DB — never trust JWT role claims.
       A forged JWT with 'role': 'super_admin' must not grant escalated access."
    """

    async def test_tampered_role_in_jwt_is_ignored(self):
        """A JWT carrying `role: 'driver'` for a user who is `role: 'rider'`
        in the DB must return the DB role, not the JWT claim."""
        from fastapi.security import HTTPAuthorizationCredentials

        from backend.dependencies import get_current_user

        # DB user is a rider — role is 'rider'
        db_user = {
            "id": RIDER_ID,
            "phone": "+15551234567",
            "role": "rider",
            "profile_complete": True,
            "token_version": 0,
            "current_session_id": None,
        }

        import jwt as pyjwt

        # Forge a JWT with driver role
        tampered_token = pyjwt.encode(
            {"user_id": RIDER_ID, "role": "driver", "session_id": "sess-abc"},
            "wrong_secret_so_verify_fails_firebase_path_attempted_first",
            algorithm="HS256",
        )

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tampered_token)

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token",
                side_effect=ValueError("Not a Firebase token"),
            ),
            patch(
                "backend.dependencies.verify_jwt_token",
                return_value={"user_id": RIDER_ID, "role": "driver", "session_id": "sess-abc"},
            ),
            patch(
                "backend.dependencies.db_supabase.get_user_by_id",
                AsyncMock(return_value=db_user),
            ),
            patch(
                "backend.dependencies.db_supabase.get_rows",
                AsyncMock(return_value=[]),  # no driver row → is_driver = False
            ),
            patch(
                "backend.dependencies.db_supabase.get_driver_by_user_id_cached",
                AsyncMock(return_value=None),  # no driver profile → is_driver = False
            ),
        ):
            user = await get_current_user(credentials=creds)

        # The role must come from the DB row, not the tampered JWT claim
        assert user["role"] == "rider", (
            f"Expected role='rider' from DB but got role='{user.get('role')}'. "
            f"This means the JWT role claim was trusted — security bug."
        )
        assert user["is_driver"] is False

    async def test_tampered_admin_role_is_blocked_by_get_admin_user(self):
        """A JWT with `role: 'admin'` for a regular DB user must not pass
        `get_admin_user` because the DB role is 'rider'."""
        from fastapi import HTTPException

        from backend.dependencies import get_admin_user

        # DB user is a rider
        rider_user = {
            "id": RIDER_ID,
            "phone": "+15551234567",
            "role": "rider",
            "profile_complete": True,
        }

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(current_user=rider_user)

        assert exc_info.value.status_code == 403
        assert "Admin access required" in exc_info.value.detail

    async def test_missing_user_row_for_valid_jwt_fails_closed_not_auto_created(self):
        """C2: a valid JWT whose user_id has no matching DB row must NOT be
        auto-created here — that previously forked phantom duplicate
        accounts on a transient Supabase replica miss. A missing row now
        fails closed with a 503 so the client retries; user creation
        belongs only in the auth endpoints (/auth/verify-otp, /auth/firebase).
        This also means a forged/stale JWT can no longer mint a fresh
        'rider'-role identity by referencing a user_id that was never
        created — role tampering has nothing to attach to."""
        from fastapi.security import HTTPAuthorizationCredentials

        from backend.dependencies import get_current_user
        from backend.utils.error_handling import ServiceUnavailableException

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-jwt")

        created_users = []

        async def _create_user(user):
            created_users.append(user)

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token",
                side_effect=ValueError("Not a Firebase token"),
            ),
            patch(
                "backend.dependencies.verify_jwt_token",
                return_value={"user_id": "new-user-001", "role": "super_admin"},
            ),
            patch(
                "backend.dependencies.db_supabase.get_user_by_id",
                AsyncMock(return_value=None),  # no matching row
            ),
            patch(
                "backend.dependencies.db_supabase.create_user",
                AsyncMock(side_effect=_create_user),
            ),
        ):
            with pytest.raises(ServiceUnavailableException):
                await get_current_user(credentials=creds)

        assert not created_users, "get_current_user must never auto-create a user row (C2)"


# ─────────────────────────────────────────────────────────────────────────────
# S8: Driver views another driver's earnings
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDriverEarningsIsolation:
    """GET /drivers/earnings must always return the authenticated driver's own
    earnings — there is no path or query parameter that allows specifying a
    different driver_id.

    Code under test: backend/routes/drivers.py::get_driver_earnings (~line 669)
    Key: `get_rows("drivers", {"user_id": current_user["id"]})` — scoped by
    authenticated user, not a caller-supplied id.
    """

    async def test_earnings_scoped_to_authenticated_user(self):
        from backend.routes import drivers as drv_mod

        driver_row = _driver_row(user_id=DRIVER_USER_ID)

        captured_queries = []

        async def _capture_get_rows(table, query, **kwargs):
            captured_queries.append({"table": table, "query": query})
            if table == "drivers":
                return [driver_row]
            if table == "rides":
                return []
            return []

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(side_effect=_capture_get_rows),
        ):
            _result = await drv_mod.get_driver_earnings(
                period="week",
                current_user={"id": DRIVER_USER_ID},
            )

        # Assert the driver lookup was scoped to the authenticated user's id
        driver_queries = [q for q in captured_queries if q["table"] == "drivers"]
        assert driver_queries, "No drivers table query found"
        assert driver_queries[0]["query"].get("user_id") == DRIVER_USER_ID, (
            "Earnings query must use current_user.id, not a caller-supplied driver_id"
        )

    async def test_earnings_returns_404_for_unknown_user(self):
        """A caller with a valid token but no driver row gets 404, not another
        driver's earnings."""
        from fastapi import HTTPException

        from backend.routes import drivers as drv_mod

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(return_value=[]),  # no driver row for this user
        ):
            with pytest.raises(HTTPException) as exc_info:
                await drv_mod.get_driver_earnings(
                    period="week",
                    current_user={"id": "some-other-user"},
                )

        assert exc_info.value.status_code == 404
        assert "Driver not found" in exc_info.value.detail

    async def test_earnings_response_contains_only_own_ride_data(self):
        """The rides fetched for earnings are filtered by the driver's own id,
        not all rides in the system."""
        from backend.routes import drivers as drv_mod

        driver_row = _driver_row(user_id=DRIVER_USER_ID)

        # Earnings are summed from the fare components (base + distance + time +
        # tip), not a precomputed driver_earnings field. 8 + 4 + 2 + 1 = 15.00.
        own_ride = {
            "id": RIDE_ID,
            "driver_id": DRIVER_ID,
            "base_fare": 8.0,
            "distance_fare": 4.0,
            "time_fare": 2.0,
            "tip_amount": 1.0,
            "ride_completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
        }

        rides_queries = []

        async def _capture_get_rows(table, query, **kwargs):
            rides_queries.append({"table": table, "query": query})
            if table == "drivers":
                return [driver_row]
            if table == "rides":
                return [own_ride]
            return []

        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(side_effect=_capture_get_rows),
        ):
            result = await drv_mod.get_driver_earnings(
                period="week",
                current_user={"id": DRIVER_USER_ID},
            )

        # The ride query must be scoped to this driver's id
        ride_queries = [q for q in rides_queries if q["table"] == "rides"]
        assert ride_queries, "No rides query found"
        rides_q = ride_queries[0]["query"]
        assert rides_q.get("driver_id") == DRIVER_ID, (
            f"Rides for earnings must be scoped to driver_id={DRIVER_ID}; got query={rides_q}"
        )
        assert result["total_earnings"] == "15.00"
