"""Pickup-OTP brute-force hardening (2026-09-05 director review §1.6).

Before this, `POST /drivers/rides/{id}/verify-otp` had:
  * no field validation — `otp: str` accepted "", non-digits, any length;
  * no per-ride attempt limit — only the global 100/min-per-IP default, so the
    *assigned* driver could walk the 4-digit space in well under an hour and
    open Period 3 (passenger aboard, full TNC commercial coverage) with a
    running meter and no rider in the car;
  * a crash on a NULL `pickup_otp` — `hmac.compare_digest(None, ...)` raises
    TypeError -> 500, and a blank stored code matched a blank submitted one.

These tests pin all three, plus the production gate on the rider-router's
OTP-free `POST /rides/{id}/start` twin, which was the open bypass that made the
lockout meaningless on its own.

Patch-target conventions: see the docstring of test_driver_ride_flow_coverage.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-otp-1"
_USER_ID = "user-otp-1"
_RIDER_ID = "rider-otp-1"
_RIDE_ID = "ride-otp-1"


@pytest.fixture(autouse=True)
def _clear_redis_local():
    """The Redis client falls back to an in-process dict when REDIS_URL is
    unset (CLAUDE.md, "Redis transparency"). Clear it between tests so one
    test's failure counter cannot lock the next test's ride."""
    from backend.utils import redis_client

    redis_client._local.clear()
    yield
    redis_client._local.clear()


def _driver(**kw):
    base = {"id": _DRIVER_ID, "user_id": _USER_ID, "status": "active", "is_online": True}
    base.update(kw)
    return base


def _ride(**kw):
    base = {
        "id": _RIDE_ID,
        "status": "driver_arrived",
        "driver_id": _DRIVER_ID,
        "rider_id": _RIDER_ID,
        "pickup_otp": "9999",
    }
    base.update(kw)
    return base


def _rows_for(ride):
    async def fake_get_rows(table, filters=None, **kw):
        return [_driver()] if table == "drivers" else [ride]

    return fake_get_rows


# ============================================================
# Field validation — the model rejects before the handler runs
# ============================================================


class TestRideOTPRequestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty — used to match a blank/NULL stored OTP
            "123",  # too short
            "1234567",  # too long
            "12a4",  # non-digit
            "12 4",  # whitespace
            "‍1234",  # zero-width joiner prefix
            "١٢٣٤",  # Arabic-Indic digits: str.isdigit() is True, \d must reject
        ],
    )
    def test_rejects_non_four_to_six_digit_input(self, bad):
        from pydantic import ValidationError

        from backend.routes.drivers._shared import RideOTPRequest

        with pytest.raises(ValidationError):
            RideOTPRequest(otp=bad)

    @pytest.mark.parametrize("good", ["1234", "0000", "123456"])
    def test_accepts_plain_digit_codes(self, good):
        from backend.routes.drivers._shared import RideOTPRequest

        assert RideOTPRequest(otp=good).otp == good


# ============================================================
# NULL / blank stored OTP -> 409, never 500
# ============================================================


class TestMissingStoredOtp:
    @pytest.mark.parametrize("stored", [None, ""])
    async def test_409_not_500_when_ride_has_no_pickup_otp(self, stored):
        from backend.routes.drivers._shared import RideOTPRequest
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride(pickup_otp=stored)
        with patch(
            "backend.routes.drivers._deps.db_supabase.get_rows",
            AsyncMock(side_effect=_rows_for(ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(
                    ride_id=_RIDE_ID,
                    request=RideOTPRequest(otp="1234"),
                    current_user={"id": _USER_ID},
                )
        assert exc.value.status_code == 409

    async def test_missing_otp_does_not_transition_the_ride(self):
        """The 409 must be raised before the CAS — a ride with no code must
        never reach in_progress / Period 3."""
        from backend.routes.drivers._shared import RideOTPRequest
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride(pickup_otp=None)
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=_rows_for(ride)),
            ),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock()) as upd,
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()) as period,
        ):
            with pytest.raises(HTTPException):
                await verify_pickup_otp(
                    ride_id=_RIDE_ID,
                    request=RideOTPRequest(otp="1234"),
                    current_user={"id": _USER_ID},
                )
        upd.assert_not_awaited()
        period.assert_not_awaited()


# ============================================================
# Per-ride failure counter -> lockout
# ============================================================


class TestPickupOtpLockout:
    async def _attempt(self, otp="1234", ride=None):
        from backend.routes.drivers._shared import RideOTPRequest
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = ride if ride is not None else _ride()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=_rows_for(ride)),
            ),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=lambda c: c.close()),
        ):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(
                    ride_id=_RIDE_ID,
                    request=RideOTPRequest(otp=otp),
                    current_user={"id": _USER_ID},
                )
        return exc.value

    async def test_fifth_wrong_code_locks_the_ride(self):
        from backend.routes.drivers._shared import _PICKUP_OTP_MAX_FAILURES

        for _ in range(_PICKUP_OTP_MAX_FAILURES - 1):
            assert (await self._attempt()).status_code == 400
        # The attempt that reaches the threshold is itself refused as locked.
        assert (await self._attempt()).status_code == 429

    async def test_locked_ride_refuses_even_the_correct_code(self):
        """The whole point: once locked, brute force cannot continue — the
        attacker does not get to keep guessing past the limit."""
        from backend.routes.drivers._shared import _PICKUP_OTP_MAX_FAILURES

        for _ in range(_PICKUP_OTP_MAX_FAILURES):
            await self._attempt()
        exc = await self._attempt(otp="9999")  # the *correct* code
        assert exc.status_code == 429
        assert exc.headers["Retry-After"]
        assert exc.headers["RateLimit-Remaining"] == "0"

    async def test_lockout_notifies_the_rider(self):
        from backend.routes.drivers._shared import _PICKUP_OTP_MAX_FAILURES, RideOTPRequest
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride()
        for _ in range(_PICKUP_OTP_MAX_FAILURES - 1):
            await self._attempt()

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=_rows_for(ride)),
            ),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()) as ws,
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()) as push,
            patch("backend.routes.drivers._deps.spawn", side_effect=lambda c: c.close()),
        ):
            with pytest.raises(HTTPException):
                await verify_pickup_otp(
                    ride_id=_RIDE_ID,
                    request=RideOTPRequest(otp="1234"),
                    current_user={"id": _USER_ID},
                )
        ws.assert_called_once()
        assert ws.call_args[0][0]["type"] == "pickup_otp_locked"
        assert ws.call_args[0][1] == f"rider_{_RIDER_ID}"
        push.assert_called_once()
        # PIPEDA: the notification must not carry the code itself.
        assert "9999" not in str(push.call_args)

    async def test_counter_is_scoped_per_ride(self):
        """A driver who fat-fingers one ride's code is not penalised on the
        next ride — the key is the ride, not the driver."""
        from backend.routes.drivers._shared import (
            _PICKUP_OTP_MAX_FAILURES,
            check_pickup_otp_lockout,
        )

        for _ in range(_PICKUP_OTP_MAX_FAILURES):
            await self._attempt()
        # Same driver, a different ride: not locked.
        await check_pickup_otp_lockout("ride-otp-2")

    async def test_correct_code_clears_the_counter(self):
        from backend.routes.drivers._shared import RideOTPRequest, check_pickup_otp_lockout
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        await self._attempt()  # one miss on the record
        ride = _ride()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=_rows_for(ride)),
            ),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=lambda c: c.close()),
        ):
            result = await verify_pickup_otp(
                ride_id=_RIDE_ID,
                request=RideOTPRequest(otp="9999"),
                current_user={"id": _USER_ID},
            )
        assert result == {"success": True}
        from backend.utils import redis_client

        assert redis_client._local.get(f"spinr:ride:{_RIDE_ID}:otp_fail") is None
        await check_pickup_otp_lockout(_RIDE_ID)

    async def test_redis_outage_does_not_block_trip_starts(self):
        """A 503 here would strand every rider in every car: the pickup OTP is
        the only production path from driver_arrived -> in_progress now that
        both /start routes are 410 there. A cache outage must not become a
        fleet-wide trip-start outage."""
        from backend.routes.drivers._shared import check_pickup_otp_lockout

        with patch(
            "backend.utils.redis_client.redis_get",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            await check_pickup_otp_lockout(_RIDE_ID)  # must not raise

    async def test_counter_still_works_during_a_redis_outage(self):
        """Degraded, not disabled: the limit survives per-replica."""
        from backend.routes.drivers._shared import (
            _PICKUP_OTP_MAX_FAILURES,
            check_pickup_otp_lockout,
            record_pickup_otp_failure,
        )

        with (
            patch(
                "backend.utils.redis_client.redis_incr",
                AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            patch(
                "backend.utils.redis_client.redis_set",
                AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            patch(
                "backend.utils.redis_client.redis_get",
                AsyncMock(side_effect=RuntimeError("redis down")),
            ),
        ):
            counts = [await record_pickup_otp_failure(_RIDE_ID, _DRIVER_ID) for _ in range(_PICKUP_OTP_MAX_FAILURES)]
            # The in-process counter really counts, rather than returning 0.
            assert counts == list(range(1, _PICKUP_OTP_MAX_FAILURES + 1))
            with pytest.raises(HTTPException) as exc:
                await check_pickup_otp_lockout(_RIDE_ID)
        assert exc.value.status_code == 429

    async def test_a_correct_code_clears_the_degraded_lock_too(self):
        """Otherwise a ride locked during an outage stays locked in-process
        after Redis returns, blocking a driver on a code they got right."""
        from backend.routes.drivers._shared import (
            _PICKUP_OTP_MAX_FAILURES,
            check_pickup_otp_lockout,
            clear_pickup_otp_failures,
            record_pickup_otp_failure,
        )

        with (
            patch("backend.utils.redis_client.redis_incr", AsyncMock(side_effect=RuntimeError("down"))),
            patch("backend.utils.redis_client.redis_set", AsyncMock(side_effect=RuntimeError("down"))),
        ):
            for _ in range(_PICKUP_OTP_MAX_FAILURES):
                await record_pickup_otp_failure(_RIDE_ID, _DRIVER_ID)

        # Redis is back; the correct code must clear both stores.
        await clear_pickup_otp_failures(_RIDE_ID)
        await check_pickup_otp_lockout(_RIDE_ID)  # must not raise

    async def test_failure_record_never_turns_a_wrong_code_into_a_500(self):
        """Even if the in-process fallback itself blows up, the caller gets its
        400, not an exception."""
        from backend.routes.drivers._shared import record_pickup_otp_failure

        with (
            patch("backend.utils.redis_client.redis_incr", AsyncMock(side_effect=RuntimeError("down"))),
            patch("backend.utils.redis_client._local_incr", side_effect=RuntimeError("boom")),
        ):
            assert await record_pickup_otp_failure(_RIDE_ID, _DRIVER_ID) == 0


# ============================================================
# The OTP-free bypass route
# ============================================================


class TestRiderRouterStartRideProductionGate:
    async def test_rides_start_is_410_in_production(self):
        """POST /rides/{id}/start let the assigned driver reach in_progress and
        Period 3 with no OTP at all. Its /drivers/... twin was already gated;
        this one was not."""
        from backend.routes.rides.lifecycle import rider_start_ride

        with patch("backend.core.config.settings.ENV", "production"):
            with pytest.raises(HTTPException) as exc:
                await rider_start_ride(ride_id=_RIDE_ID, request=None, current_user={"id": _USER_ID, "is_driver": True})
        assert exc.value.status_code == 410
        assert "verify-otp" in exc.value.detail

    async def test_still_available_outside_production(self):
        """Dev/staging E2E fallback is unchanged: the gate must not fire, so
        the request proceeds to the normal driver/authorisation checks."""
        from backend.routes.rides.lifecycle import rider_start_ride

        with patch("backend.core.config.settings.ENV", "development"):
            with pytest.raises(HTTPException) as exc:
                await rider_start_ride(
                    ride_id=_RIDE_ID, request=None, current_user={"id": _USER_ID, "is_driver": False}
                )
        assert exc.value.status_code == 403
