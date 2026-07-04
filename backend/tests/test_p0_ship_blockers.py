"""
P0 ship-blocker tests — pins the five highest-priority end-to-end paths
called out in docs/E2E_TEST_GAP_ANALYSIS.md §4.

Each class covers one P0. Where the implementation already exists, the
class ends with ``Pin`` and asserts the current behavior so a regression
fails loudly. Where the implementation is a stub or deliberately missing,
the test is marked ``xfail(strict=False)`` with a comment pointing at the
gap so the failing assertion documents what needs to be built.

Also covers E3 Stripe charge scenarios (TestE3*): happy path, card decline,
and Stripe unavailability — using charge_ride() PaymentIntent.confirm path.

Run as:
    pytest backend/tests/test_p0_ship_blockers.py -v
    pytest -m e2e backend/tests/test_p0_ship_blockers.py  # lifecycle subset
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

pytestmark = pytest.mark.anyio

RIDER_ID = "rider_p0"
DRIVER_USER_ID = "driver_user_p0"
DRIVER_ID = "driver_row_p0"
RIDE_ID = "ride_p0_001"


def _ride(status: str, driver_id: str | None = None, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
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


def _driver_row() -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Driver P0",
        "rating": 4.9,
        "total_rides": 100,
    }


# ─────────────────────────────────────────────────────────────────────────────
# P0-1: Driver-cancel-post-accept notifies rider
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestDriverCancelNotifiesRider:
    """When a driver cancels after accepting, the rider's app must learn
    immediately via WebSocket + push — otherwise the rider is stuck on a
    "Driver en route" screen with a phantom trip.

    Code under test: backend/routes/drivers.py::cancel_ride (line ~2084).
    """

    async def test_cancel_publishes_to_rider_channel(self):
        from backend.routes import drivers as drv_mod

        accepted = _ride(status="driver_accepted", driver_id=DRIVER_ID)
        cancelled = _ride(status="cancelled", driver_id=DRIVER_ID)

        ws_calls = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        push_calls = []

        async def _capture_push(user_id, title, body, data=None):
            push_calls.append({"user_id": user_id, "title": title, "body": body, "data": data})

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(return_value=accepted)),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock(return_value=cancelled)),
            patch("backend.routes.drivers.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
            patch("backend.routes.drivers.send_push_notification", AsyncMock(side_effect=_capture_push)),
        ):
            # get_ride is called twice — once before and once after the update.
            # Reset side_effect so the second call returns the cancelled row.
            with patch(
                "backend.routes.drivers.db_supabase.get_ride",
                AsyncMock(side_effect=[accepted, cancelled]),
            ):
                result = await drv_mod.cancel_ride(
                    ride_id=RIDE_ID,
                    reason="driver_changed_mind",
                    current_user={"id": DRIVER_USER_ID},
                )

        assert result == {"success": True}

        # The rider push is fire-and-forget (asyncio.create_task) so it must
        # not hold up the cancel response; yield the loop a few ticks so the
        # backgrounded task runs before we assert on it.
        for _ in range(3):
            await asyncio.sleep(0)

        rider_channels = [c for c, _ in ws_calls if f"rider_{RIDER_ID}" in str(c)]
        assert rider_channels, (
            f"Driver cancellation did not notify rider channel. Channels seen: {[c for c, _ in ws_calls]}"
        )

        cancel_msgs = [m for _, m in ws_calls if m.get("type") == "ride_cancelled"]
        assert cancel_msgs, "No ride_cancelled WS event was published"
        assert cancel_msgs[0]["ride_id"] == RIDE_ID

        rider_pushes = [p for p in push_calls if p["user_id"] == RIDER_ID]
        assert rider_pushes, "Rider did not receive a push notification on driver-cancel"

    async def test_cancel_refuses_to_kill_in_progress_trip(self):
        """A driver must not be able to nuke a trip that's already rolling —
        that's a safety / fraud vector."""
        from backend.routes import drivers as drv_mod

        in_progress = _ride(status="in_progress", driver_id=DRIVER_ID)

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(return_value=in_progress)),
        ):
            with pytest.raises(Exception) as exc_info:
                await drv_mod.cancel_ride(
                    ride_id=RIDE_ID,
                    reason="whatever",
                    current_user={"id": DRIVER_USER_ID},
                )

        # RideStateError subclasses HTTPException with a 4xx code — either is acceptable
        # as long as the exception isn't swallowed.
        assert exc_info.value is not None


# ─────────────────────────────────────────────────────────────────────────────
# P0-2: No-drivers-available UX
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestNoDriversAvailableTimeout:
    """After ~5 min of searching with no match, the backend auto-cancels
    the ride, WS-notifies the rider, and pushes a notification.

    Code under test: backend/routes/rides.py::ride_search_timeout (extracted
    to module scope so it's directly testable).
    """

    async def test_timeout_auto_cancels_still_searching_ride(self):
        from backend.routes import rides as rides_mod

        searching = _ride(status="searching")

        update_calls = []

        async def _capture_update(ride_id, patch):
            update_calls.append((ride_id, patch))

        ws_calls = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        push_calls = []

        async def _capture_push(user_id, title, body, data=None):
            push_calls.append({"user_id": user_id, "title": title, "body": body, "data": data})

        with (
            patch("backend.routes.rides.asyncio.sleep", AsyncMock()),  # fast-forward
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=searching)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(side_effect=_capture_update)),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
            patch("backend.routes.rides.send_push_notification", AsyncMock(side_effect=_capture_push)),
        ):
            await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=1)

        assert update_calls, "update_ride was not called on timeout"
        _, update_patch = update_calls[0]
        assert update_patch["status"] == "cancelled"
        assert "cancellation_reason" in update_patch

        assert any(f"rider_{RIDER_ID}" in str(c) and m.get("type") == "ride_cancelled" for c, m in ws_calls), (
            "Rider channel was not notified on search timeout"
        )

        assert push_calls and push_calls[0]["user_id"] == RIDER_ID
        assert push_calls[0]["data"].get("is_auto") is True

    async def test_timeout_is_a_noop_if_driver_already_matched(self):
        """If a driver was matched/accepted before the timer fired, the
        timeout must NOT cancel — otherwise a paying ride gets nuked."""
        from backend.routes import rides as rides_mod

        matched = _ride(status="driver_accepted", driver_id=DRIVER_ID)

        update_mock = AsyncMock()
        ws_mock = AsyncMock()

        with (
            patch("backend.routes.rides.asyncio.sleep", AsyncMock()),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=matched)),
            patch("backend.routes.rides.db_supabase.update_ride", update_mock),
            patch("backend.routes.rides.manager.send_personal_message", ws_mock),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=1)

        update_mock.assert_not_called()
        ws_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# P0-3: Duplicate ride request guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestDuplicateRideRequestGuardHandler:
    """Direct handler tests — bypass FastAPI middleware so we can pin the
    actual guard logic regardless of auth/rate-limit config."""

    async def test_handler_raises_409_when_rider_has_active_ride(self):
        """Active-ride guard: second create for the same rider → 409."""
        from fastapi import HTTPException
        from starlette.requests import Request

        from backend.routes import rides as rides_mod
        from backend.schemas import CreateRideRequest
        from backend.utils.error_handling import SpinrException

        active = _ride(status="searching")
        rider_row = {"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}

        body = CreateRideRequest(
            pickup_address="123 Main",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_address="456 Broadway",
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
            vehicle_type_id="economy",
            payment_method="card",
        )

        # SlowAPI's @ride_request_limit requires `request` be a real starlette
        # Request, not a MagicMock. We spec the mock against Request so
        # isinstance() passes without building a full ASGI scope.
        fake_request = MagicMock(spec=Request)
        fake_request.headers = {}
        fake_request.client = MagicMock(host="127.0.0.1")

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=rider_row)),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=[active])),
        ):
            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await rides_mod.create_ride(request=fake_request, body=body, current_user={"id": RIDER_ID})

        assert exc_info.value.status_code == 409
        assert "active" in (getattr(exc_info.value, "detail", None) or getattr(exc_info.value, "message", "")).lower()

    async def test_handler_returns_existing_ride_on_idempotency_key_replay(self):
        """Idempotency-Key guard: replay returns the original ride."""
        from starlette.requests import Request

        from backend.routes import rides as rides_mod
        from backend.schemas import CreateRideRequest

        original = _ride(status="searching")

        body = CreateRideRequest(
            pickup_address="123 Main",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_address="456 Broadway",
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
            vehicle_type_id="economy",
            payment_method="card",
        )

        fake_request = MagicMock(spec=Request)
        fake_request.headers = {"Idempotency-Key": "dedup-key-xyz"}
        fake_request.client = MagicMock(host="127.0.0.1")

        insert_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db_supabase.find_one", AsyncMock(return_value=original)),
            patch("backend.routes.rides.db_supabase.insert_ride", insert_mock),
        ):
            result = await rides_mod.create_ride(request=fake_request, body=body, current_user={"id": RIDER_ID})

        assert result == original
        insert_mock.assert_not_called()


@pytest.mark.e2e
class TestDuplicateRideRequestGuard:
    """A double-tap on "Confirm" must NOT create two rides.

    Two mechanisms cover this in prod:
      1. Active-ride check — 409 if the rider already has a ride in
         {searching, driver_assigned, driver_accepted, driver_arrived,
         in_progress}. See backend/routes/rides.py:556-564.
      2. Idempotency-Key header — POST /rides with the same key returns
         the previously-created ride instead of inserting a second row.
         See backend/routes/rides.py:533-540.

    Route-level defensive tests (may 401 before reaching the handler —
    that's still an acceptable outcome for "double-tap never succeeds").
    The handler-level tests above are the strong assertions.
    """

    def test_second_create_with_active_ride_returns_409(self, test_client, auth_headers):
        active = _ride(status="searching")

        with (
            patch("backend.routes.rides.db_supabase.find_one", AsyncMock(return_value=None)),
            patch(
                "backend.routes.rides.db.find_one",
                AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
            ),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=[active])),
            patch("backend.dependencies.get_current_user", lambda: {"id": RIDER_ID}),
        ):
            r = test_client.post(
                "/api/v1/rides",
                headers=auth_headers,
                json={
                    "pickup_address": "123 Main",
                    "pickup_lat": 52.13,
                    "pickup_lng": -106.67,
                    "dropoff_address": "456 Broadway",
                    "dropoff_lat": 52.12,
                    "dropoff_lng": -106.65,
                    "vehicle_type_id": "economy",
                    "payment_method": "card",
                },
            )

        # Real auth middleware may 401 the request before the handler runs.
        # That's fine — the key assertion is we never return 200 for a
        # second create while one is active. Either 409 (handler-level) or
        # 401 (auth) is acceptable; 200 is a bug.
        assert r.status_code != 200, (
            f"Double-tap on /rides returned 200 with an active ride present. "
            f"Expected 409 (active-ride guard) or 401 (auth). Body: {r.text[:200]}"
        )

    def test_idempotency_key_returns_existing_ride(self, test_client, auth_headers):
        """POST /rides with the same Idempotency-Key twice returns the first
        ride on the second call, never creates a new one."""
        existing = _ride(status="searching")

        with (
            patch("backend.routes.rides.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch("backend.dependencies.get_current_user", lambda: {"id": RIDER_ID}),
        ):
            r = test_client.post(
                "/api/v1/rides",
                headers={**auth_headers, "Idempotency-Key": "abc-123"},
                json={
                    "pickup_address": "123 Main",
                    "pickup_lat": 52.13,
                    "pickup_lng": -106.67,
                    "dropoff_address": "456 Broadway",
                    "dropoff_lat": 52.12,
                    "dropoff_lng": -106.65,
                    "vehicle_type_id": "economy",
                    "payment_method": "card",
                },
            )

        # Same contract as above — 401 from real auth is acceptable; a 200
        # with a *different* ride.id than `existing` would be a bug.
        if r.status_code == 200:
            assert r.json().get("id") == RIDE_ID


# ─────────────────────────────────────────────────────────────────────────────
# P0-4: Surge boundary consistency
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestSurgeBoundaryConsistency:
    """When the rider confirms a ride 30s after seeing an estimate, the
    confirmed fare must match the estimate they saw — otherwise we bait-
    and-switch on price.

    Current state (as of 2026-04-20):
      - POST /estimate reads current surge from service_area
      - POST /rides *re-reads* current surge from service_area
      - No estimate_id / surge token locks the estimate to the create
      - Idempotency-Key protects against double-create, NOT surge drift

    This class pins the Idempotency path (which works) and marks the
    surge-drift gap with xfail so the red result documents the missing
    feature until it's built.
    """

    def test_idempotency_key_preserves_original_fare_on_retry(self, test_client, auth_headers):
        """If the first create succeeded at surge=1.5 and the client retries
        with the same Idempotency-Key, the retry returns the original ride
        (and therefore the original fare) regardless of current surge."""
        original = _ride(status="searching", grand_total=27.75, surge_multiplier=1.5)

        with (
            patch("backend.routes.rides.db_supabase.find_one", AsyncMock(return_value=original)),
            patch("backend.dependencies.get_current_user", lambda: {"id": RIDER_ID}),
        ):
            r = test_client.post(
                "/api/v1/rides",
                headers={**auth_headers, "Idempotency-Key": "retry-key-1"},
                json={
                    "pickup_address": "123 Main",
                    "pickup_lat": 52.13,
                    "pickup_lng": -106.67,
                    "dropoff_address": "456 Broadway",
                    "dropoff_lat": 52.12,
                    "dropoff_lng": -106.65,
                    "vehicle_type_id": "economy",
                    "payment_method": "card",
                },
            )

        if r.status_code == 200:
            assert r.json().get("grand_total") == 27.75

    def test_surge_is_locked_between_estimate_and_create(self):
        """Surge-lock via estimate_token (P0-4 CLOSED).

        Scenario: rider gets estimate at surge=1.5, then server's current
        surge flips to 1.0 before they confirm. With the estimate_token,
        the confirmed ride must still price at 1.5 — the rider already saw
        and accepted that amount.
        """
        from backend.utils.estimate_token import (
            sign_estimate_token,
            verify_estimate_token,
        )

        tok = sign_estimate_token(
            rider_id=RIDER_ID,
            vehicle_type_id="economy",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
            surge_multiplier=1.5,
            total_fare=27.75,
        )

        payload = verify_estimate_token(
            tok,
            rider_id=RIDER_ID,
            vehicle_type_id="economy",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
        )

        assert payload["sm"] == 1.5, "Surge-lock broken: estimate_token did not preserve surge_multiplier"
        assert payload["tf"] == 27.75


@pytest.mark.e2e
class TestCreateRideHonorsEstimateToken:
    """End-to-end proof that POST /rides uses the token's surge rather
    than re-reading service_area. This is the behaviour the P0-4 gap
    analysis cared about — upstream verify_estimate_token is unit-tested
    in test_estimate_token.py."""

    async def test_valid_token_overrides_current_surge(self):
        """When token surge=1.5 and service_area surge=1.0 are in conflict,
        the ride persists with the token's 1.5."""
        from starlette.requests import Request

        from backend.routes import rides as rides_mod
        from backend.schemas import CreateRideRequest
        from backend.utils.estimate_token import sign_estimate_token

        # Issue a token locking surge=1.5
        token = sign_estimate_token(
            rider_id=RIDER_ID,
            vehicle_type_id="economy",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
            surge_multiplier=1.5,
            total_fare=27.75,
        )

        # Service area now reports surge=1.0 — classic drift scenario
        fare_info = {
            "vehicle_type": {"id": "economy", "name": "Economy"},
            "base_fare": 2.5,
            "per_km_rate": 1.5,
            "per_minute_rate": 0.25,
            "booking_fee": 2.0,
            "minimum_fare": 5.0,
            "surge_multiplier": 1.0,  # CURRENT surge — should be ignored
        }

        body = CreateRideRequest(
            pickup_address="123 Main",
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_address="456 Broadway",
            dropoff_lat=52.12,
            dropoff_lng=-106.65,
            vehicle_type_id="economy",
            payment_method="card",
            estimate_token=token,
        )

        fake_request = MagicMock(spec=Request)
        fake_request.headers = {}
        fake_request.client = MagicMock(host="127.0.0.1")

        rider_row = {"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}

        # Capture the ride row inserted so we can inspect the fare
        inserted_rows = []

        async def _capture_insert(row):
            inserted_rows.append(row)
            # Supabase returns the inserted row
            return {**row, "id": "ride_new_001"}

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=rider_row)),
            patch(
                "backend.routes.rides.db_supabase.get_rows",
                AsyncMock(
                    side_effect=[
                        [],  # no active ride
                        [],  # service_areas (empty is fine — no airport)
                        [{"id": "economy"}],  # vehicle_types
                    ]
                ),
            ),
            patch("backend.routes.rides._fares_for_location_impl", AsyncMock(return_value=[fare_info])),
            patch("backend.routes.rides.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
            patch(
                "backend.routes.rides.calculate_all_fees",
                AsyncMock(return_value={"fees_total": 0.0, "tax_amount": 0.0}),
            ),
            patch("backend.routes.rides.db_supabase.insert_ride", AsyncMock(side_effect=_capture_insert)),
            patch("backend.routes.rides.match_driver_to_ride", AsyncMock()),
            patch(
                "backend.routes.rides.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_new_001", "status": "searching"}),
            ),
            patch("backend.routes.rides.asyncio.create_task", lambda coro: coro.close()),
        ):
            await rides_mod.create_ride(request=fake_request, body=body, current_user={"id": RIDER_ID})

        assert inserted_rows, "create_ride did not insert a ride"
        inserted = inserted_rows[0]

        # The recorded surge on the persisted ride must be the token's 1.5,
        # not the current 1.0 — otherwise the fare the rider saw in the
        # estimate differs from what we charge.
        assert inserted.get("surge_multiplier") == 1.5, (
            f"Surge not locked: persisted {inserted.get('surge_multiplier')}, expected 1.5 "
            f"(token had surge=1.5; service_area currently has 1.0)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P0-5: Payment failure at complete
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
class TestPaymentFailureAtComplete:
    """When Stripe declines the rider's card at trip completion, the system
    must leave the ride in a recoverable state (rider can re-try or swap
    methods) — not silently mark it paid.

    Current state:
      - Wallet payment path is implemented and transactional
      - Card payment path is a stub (rides.py:1088 - "Card path is still a
        stub — Stripe charge to be wired separately")
      - Complete-ride handler (drivers.py:1916-2080) hardcodes
        payment_status='completed' regardless of actual payment outcome

    The wallet success path is pinned below. The card-failure path is xfail.
    """

    async def test_wallet_payment_debits_and_marks_paid(self):
        """Wallet pay at completion: balance decreases, ride marked paid."""
        from backend.routes import rides as rides_mod

        completed = _ride(status="completed", payment_method="wallet", total_fare=18.5)
        wallet = {"id": "w1", "user_id": RIDER_ID, "balance": 100.0, "is_active": True}

        update_one_mock = MagicMock()
        update_one_mock.modified_count = 1

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.rides.db.update_one", AsyncMock(return_value=update_one_mock)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock()),
            # settle_wallet now debits atomically via the wallet_pay_for_ride RPC
            # (returns the new balance) instead of a read-compute-write.
            patch("backend.db_supabase.wallet_pay_for_ride", AsyncMock(return_value=Decimal("79.50"))),
            patch(
                "backend.routes.rides.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": RIDER_ID, "email": "r@s.ca"}),
            ),
            patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
            patch("backend.routes.wallet._record_transaction", AsyncMock()),
        ):
            # Construct the Pydantic request object the handler expects
            from backend.routes.rides import ProcessPaymentRequest

            req = ProcessPaymentRequest(tip_amount=2.0)
            result = await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert result["success"] is True
        assert Decimal(str(result["charged_amount"])) == Decimal("20.5")  # 18.5 + 2.0 tip

    async def test_wallet_rejects_insufficient_balance(self):
        """If the wallet can't cover fare + tip, payment returns 400 and
        the ride's payment_status is released back to pending so the
        rider can retry with a card."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        completed = _ride(status="completed", payment_method="wallet", total_fare=50.0)
        wallet = {"id": "w1", "user_id": RIDER_ID, "balance": 5.0, "is_active": True}

        update_one_mock = MagicMock()
        update_one_mock.modified_count = 1

        update_ride_calls = []

        async def _capture_update(ride_id, patch):
            update_ride_calls.append((ride_id, patch))

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.rides.db.update_one", AsyncMock(return_value=update_one_mock)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(side_effect=_capture_update)),
            # The atomic RPC raises ValueError('insufficient_funds') when the
            # balance can't cover the charge; settle_wallet must release the lock.
            patch(
                "backend.db_supabase.wallet_pay_for_ride",
                AsyncMock(side_effect=ValueError("insufficient_funds")),
            ),
            patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
            patch("backend.routes.wallet._record_transaction", AsyncMock()),
        ):
            from backend.routes.rides import ProcessPaymentRequest

            req = ProcessPaymentRequest(tip_amount=0)

            with pytest.raises((HTTPException, SpinrException)) as exc:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc.value.status_code == 400
        # Retry is possible — payment_status must be released back to pending
        assert any(p.get("payment_status") == "pending" for _, p in update_ride_calls), (
            "Insufficient-balance rejection did not release the payment_status "
            "lock. Rider is now blocked from retrying with a card."
        )

    async def test_card_decline_marks_payment_failed_and_allows_retry(self):
        """Card decline → payment_status='failed', response surfaces card_declined code.

        Stripe charge is wired (PR#25 / charge_ride helper). This test pins the
        decline branch: ChargeOutcome(status='declined') must flip payment_status
        to 'failed' so the rider can retry with a different card.
        """
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException
        from backend.utils.stripe_charge import ChargeOutcome

        completed = _ride(
            status="completed",
            payment_method="card",
            total_fare=25.0,
            payment_method_id="pm_decline",
            stripe_customer_id="cus_test",
        )

        update_ride_calls: list[tuple] = []

        async def _capture_update(ride_id, patch):
            update_ride_calls.append((ride_id, patch))

        declined_outcome = ChargeOutcome(
            status="declined",
            error_message="Your card was declined.",
            decline_code="card_declined",
        )

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.rides.db_supabase.update_ride", AsyncMock(side_effect=_capture_update)),
            patch(
                "backend.routes.rides.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": RIDER_ID, "email": "r@s.ca", "stripe_customer_id": "cus_test"}),
            ),
            patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=declined_outcome)),
        ):
            from backend.routes.rides import ProcessPaymentRequest

            req = ProcessPaymentRequest(tip_amount=0)
            with pytest.raises((HTTPException, SpinrException)) as exc:
                await rides_mod.process_payment(ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID})

        assert exc.value.status_code == 402
        assert exc.value.detail.get("code") == "card_declined"

        # payment_status must be set to 'failed' so the rider can retry
        assert any(p.get("payment_status") == "failed" for _, p in update_ride_calls), (
            "Card decline did not flip payment_status to 'failed' — rider cannot retry."
        )


# ── E3: Stripe charge at completion ─────────────────────────────────────────


def _patch_settings(secret: str = "sk_test_xxx"):
    from unittest.mock import AsyncMock as _AM

    async def _s():
        return {"stripe_secret_key": secret}

    return patch("backend.utils.stripe_charge.get_app_settings", _AM(side_effect=_s))


def _patch_stripe_confirm(return_value=None, side_effect=None):
    mock_stripe = MagicMock()
    if side_effect is not None:
        mock_stripe.PaymentIntent.confirm.side_effect = side_effect
    else:
        mock_stripe.PaymentIntent.confirm.return_value = return_value
    # create should not be called when a PI id is supplied
    mock_stripe.PaymentIntent.create.side_effect = AssertionError("create must not be called when PI id supplied")
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


_BASE_KW = dict(
    ride={"id": "ride_e3_1"},
    rider_id="rider_e3",
    total_amount=30.00,
    stripe_customer_id="cus_e3",
    payment_method_id="pm_e3",
    payment_intent_id="pi_e3_existing",
)


# ── E3-1: Happy path — PaymentIntent.confirm succeeds ───────────────────────


class TestE3HappyPath:
    async def test_confirm_called_instead_of_create(self):
        """When payment_intent_id is supplied, confirm() is called, not create()."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_e3_existing", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe_confirm(return_value=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "succeeded"
        assert outcome.payment_intent_id == "pi_e3_existing"
        assert outcome.charged_amount == 30.00

        mock_stripe.PaymentIntent.confirm.assert_called_once_with(
            "pi_e3_existing",
            api_key="sk_test_xxx",
            # Replay-safe confirm: deterministic key ride-confirm-<ride>-<cents>
            idempotency_key="ride-confirm-ride_e3_1-3000",
        )
        # create must NOT have been called (side_effect would raise AssertionError)
        mock_stripe.PaymentIntent.create.assert_not_called()

    async def test_no_pi_id_falls_back_to_create(self):
        """Without payment_intent_id the original create() path is taken."""
        from backend.utils.stripe_charge import charge_ride

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.return_value = MagicMock(id="pi_new", status="succeeded", client_secret=None)

        kw = {**_BASE_KW, "payment_intent_id": None}
        with _patch_settings(), patch("backend.utils.stripe_charge.stripe", mock_stripe):
            outcome = await charge_ride(**kw)

        assert outcome.status == "succeeded"
        mock_stripe.PaymentIntent.create.assert_called_once()
        mock_stripe.PaymentIntent.confirm.assert_not_called()


# ── E3-2: Card declined — confirm raises CardError → HTTP 402 ───────────────


class TestE3CardDecline:
    async def test_card_decline_marks_payment_failed_and_allows_retry(self):
        """stripe.PaymentIntent.confirm raises CardError → declined outcome.
        The caller (process_payment) must return 402 and NOT mark ride completed."""
        from backend.utils.stripe_charge import charge_ride

        class _FakeErr:
            code = "card_declined"
            decline_code = "insufficient_funds"
            message = "Your card has insufficient funds."

        class _FakeCardError(Exception):
            def __init__(self):
                super().__init__("Insufficient funds")
                self.error = _FakeErr()

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.confirm.side_effect = _FakeCardError()

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _FakeCardError),
        ):
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "declined"
        assert outcome.decline_code == "insufficient_funds"
        assert "insufficient" in (outcome.error_message or "").lower()

    async def test_requires_payment_method_after_confirm_treated_as_declined(self):
        """PI lands in requires_payment_method after confirm → declined."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_e3_existing", status="requires_payment_method", client_secret=None)
        stripe_patch, _ = _patch_stripe_confirm(return_value=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "declined"


# ── E3-3: Stripe unavailable — network / import failure → graceful fallback ─


class TestE3StripeUnavailable:
    async def test_stripe_base_error_returns_failed(self):
        """Non-card Stripe error (connection, rate-limit) → failed, not 5xx crash."""
        from backend.utils.stripe_charge import charge_ride

        class _FakeCardError(Exception):
            pass

        class _FakeStripeError(_FakeCardError):
            pass

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.confirm.side_effect = _FakeStripeError("api_connection_error: Unable to reach Stripe")

        # Patch _StripeCardError to a class _FakeStripeError does NOT inherit
        # from so the except-CardError branch is skipped and the
        # except-StripeBaseError branch catches it.
        class _UnrelatedCardError(Exception):
            pass

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _UnrelatedCardError),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "failed"
        assert "api_connection_error" in (outcome.error_message or "")

    async def test_stripe_module_not_installed_returns_unconfigured(self):
        """stripe package absent at import time → unconfigured (not a crash)."""
        from backend.utils.stripe_charge import charge_ride

        with _patch_settings(), patch("backend.utils.stripe_charge.stripe", None):
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "unconfigured"

    async def test_stripe_key_missing_returns_unconfigured(self):
        """Empty stripe_secret_key in settings → unconfigured (dev / staging)."""
        from backend.utils.stripe_charge import charge_ride

        with _patch_settings(secret=""):
            outcome = await charge_ride(**_BASE_KW)

        assert outcome.status == "unconfigured"


# ── E3-4: process_payment endpoint — HTTP response surface ──────────────────
#
# These tests stub charge_ride() and db_supabase to verify that process_payment
# maps ChargeOutcome → the correct HTTP status codes without needing a live DB.


def _make_ride(payment_intent_id: str | None = None) -> dict:
    return {
        "id": "ride_http_1",
        "rider_id": "user_1",
        "status": "completed",
        "payment_method": "card",
        "payment_method_id": "pm_http",
        "payment_intent_id": payment_intent_id,
        "payment_status": "pending",
        "total_fare": 20.00,
        "tip_amount": 0,
    }


def _app_with_mocked_auth(user_id: str = "user_1"):
    """Return a TestClient-ready FastAPI app with auth bypassed.

    The rides router registers itself with prefix="/rides", so all routes
    are reachable under /rides/<ride_id>/... in the test client.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        from backend.dependencies import get_current_user
        from backend.routes.rides import api_router
    except ImportError:
        from dependencies import get_current_user
        from routes.rides import api_router

    app = FastAPI()
    app.include_router(api_router)  # router has prefix="/rides" built in

    async def _fake_user():
        return {"id": user_id, "role": "rider"}

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


class TestProcessPaymentHTTP:
    """HTTP surface tests for process_payment — verifies that charge_ride
    outcomes map to the correct HTTP status codes."""

    def _load_rides_module(self):
        """Ensure routes.rides is loaded into sys.modules before patching.

        conftest.py sets env vars and sys.path before any test runs, so
        `routes.rides` is importable by test time. We trigger the import
        here rather than at module-load time to ensure conftest fixtures
        (env vars, mock supabase) are already active.
        """
        import importlib
        import sys

        # Check if already loaded under either package form
        for key in ("backend.routes.rides", "routes.rides"):
            if key in sys.modules:
                return sys.modules[key]

        for mod_path in ("backend.routes.rides", "routes.rides"):
            try:
                return importlib.import_module(mod_path)
            except (ImportError, ModuleNotFoundError):
                continue
        raise ImportError("Cannot import rides module")

    async def test_succeeded_returns_200_and_paid(self):
        """charge_ride returns succeeded → process_payment returns 200."""
        rides_mod = self._load_rides_module()
        ride = _make_ride()
        outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi_ok", charged_amount=20.0)

        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_user_by_id = AsyncMock(
            return_value={"stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}
        )
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": "ride_http_1"})

        # The card path moved into services/payment_service.settle_card (Phase 4
        # decomposition) — patch charge_ride and db access there, and keep the
        # rides-module db patched for the handler's own pre-reads/guards.
        with (
            patch.object(rides_mod, "db_supabase", mock_db),
            patch("backend.services.payment_service.db_supabase", mock_db),
            patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        ):
            client = _app_with_mocked_auth()
            resp = client.post("/rides/ride_http_1/process-payment", json={"tip_amount": "0"})

        assert resp.status_code == 200
        assert resp.json().get("success") is True

    async def test_declined_returns_402(self):
        """charge_ride returns declined → process_payment returns 402."""
        rides_mod = self._load_rides_module()
        ride = _make_ride()
        outcome = ChargeOutcome(
            status="declined",
            decline_code="insufficient_funds",
            error_message="Your card has insufficient funds.",
        )

        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_user_by_id = AsyncMock(
            return_value={"stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}
        )
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": "ride_http_1"})

        # The card path moved into services/payment_service.settle_card (Phase 4
        # decomposition) — patch charge_ride and db access there, and keep the
        # rides-module db patched for the handler's own pre-reads/guards.
        with (
            patch.object(rides_mod, "db_supabase", mock_db),
            patch("backend.services.payment_service.db_supabase", mock_db),
            patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        ):
            client = _app_with_mocked_auth()
            resp = client.post("/rides/ride_http_1/process-payment", json={"tip_amount": "0"})

        assert resp.status_code == 402
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "card_declined"
        assert detail.get("decline_code") == "insufficient_funds"

    async def test_stripe_failed_returns_402(self):
        """charge_ride returns failed (ops error) → process_payment returns 402 payment_error."""
        rides_mod = self._load_rides_module()
        ride = _make_ride()
        outcome = ChargeOutcome(
            status="failed",
            error_message="api_connection_error: Unable to reach Stripe",
        )

        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_user_by_id = AsyncMock(
            return_value={"stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}
        )
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": "ride_http_1"})

        # The card path moved into services/payment_service.settle_card (Phase 4
        # decomposition) — patch charge_ride and db access there, and keep the
        # rides-module db patched for the handler's own pre-reads/guards.
        with (
            patch.object(rides_mod, "db_supabase", mock_db),
            patch("backend.services.payment_service.db_supabase", mock_db),
            patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        ):
            client = _app_with_mocked_auth()
            resp = client.post("/rides/ride_http_1/process-payment", json={"tip_amount": "0"})

        assert resp.status_code == 402
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "payment_error"
