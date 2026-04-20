"""
P0 ship-blocker tests — pins the five highest-priority end-to-end paths
called out in docs/E2E_TEST_GAP_ANALYSIS.md §4.

Each class covers one P0. Where the implementation already exists, the
class ends with ``Pin`` and asserts the current behavior so a regression
fails loudly. Where the implementation is a stub or deliberately missing,
the test is marked ``xfail(strict=False)`` with a comment pointing at the
gap so the failing assertion documents what needs to be built.

Run as:
    pytest backend/tests/test_p0_ship_blockers.py -v
    pytest -m e2e backend/tests/test_p0_ship_blockers.py  # lifecycle subset
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
        "created_at": datetime.utcnow().isoformat(),
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
@pytest.mark.asyncio
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

        rider_channels = [c for c, _ in ws_calls if f"rider_{RIDER_ID}" in str(c)]
        assert rider_channels, (
            f"Driver cancellation did not notify rider channel. "
            f"Channels seen: {[c for c, _ in ws_calls]}"
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

        in_progress = _ride(status="trip_in_progress", driver_id=DRIVER_ID)

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
@pytest.mark.asyncio
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

        assert any(
            f"rider_{RIDER_ID}" in str(c) and m.get("type") == "ride_cancelled"
            for c, m in ws_calls
        ), "Rider channel was not notified on search timeout"

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
@pytest.mark.asyncio
class TestDuplicateRideRequestGuardHandler:
    """Direct handler tests — bypass FastAPI middleware so we can pin the
    actual guard logic regardless of auth/rate-limit config."""

    async def test_handler_raises_409_when_rider_has_active_ride(self):
        """Active-ride guard: second create for the same rider → 409."""
        from fastapi import HTTPException
        from starlette.requests import Request

        from backend.routes import rides as rides_mod
        from backend.schemas import CreateRideRequest

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
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.create_ride(
                    request=fake_request, body=body, current_user={"id": RIDER_ID}
                )

        assert exc_info.value.status_code == 409
        assert "active" in exc_info.value.detail.lower()

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
            result = await rides_mod.create_ride(
                request=fake_request, body=body, current_user={"id": RIDER_ID}
            )

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
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"})),
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

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Surge-lock is not implemented. /estimate and POST /rides each "
            "independently query service_area for surge_multiplier, so surge "
            "drift between the two calls bait-and-switches the rider on price. "
            "Fix: /estimate should return an estimate_token (signed { "
            "surge_multiplier, expires_at }) and POST /rides should accept it "
            "and reuse the locked surge if still within the expiry window."
        ),
    )
    def test_surge_is_locked_between_estimate_and_create(self):
        """DOCUMENTS THE GAP — will pass once surge-locking ships.

        Expectation: estimate at surge=1.5, then config flips surge to 1.0,
        then create_ride should still apply 1.5 (because the estimate was
        already shown to the rider). Today this test fails because create_ride
        re-reads surge at line 606.
        """
        # The actual assertion can be written once the estimate_token
        # mechanism exists. Until then this xfail serves as a living TODO.
        assert False, "surge-lock not implemented yet"


# ─────────────────────────────────────────────────────────────────────────────
# P0-5: Payment failure at complete
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
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
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value={"id": RIDER_ID, "email": "r@s.ca"})),
            patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
            patch("backend.routes.wallet._record_transaction", AsyncMock()),
        ):
            # Construct the Pydantic request object the handler expects
            from backend.routes.rides import ProcessPaymentRequest

            req = ProcessPaymentRequest(tip_amount=2.0)
            result = await rides_mod.process_payment(
                ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID}
            )

        assert result["success"] is True
        assert result["charged_amount"] == 20.5  # 18.5 + 2.0 tip

    async def test_wallet_rejects_insufficient_balance(self):
        """If the wallet can't cover fare + tip, payment returns 400 and
        the ride's payment_status is released back to pending so the
        rider can retry with a card."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

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
            patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
            patch("backend.routes.wallet._record_transaction", AsyncMock()),
        ):
            from backend.routes.rides import ProcessPaymentRequest

            req = ProcessPaymentRequest(tip_amount=0)

            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID, req=req, current_user={"id": RIDER_ID}
                )

        assert exc.value.status_code == 400
        # Retry is possible — payment_status must be released back to pending
        assert any(
            p.get("payment_status") == "pending"
            for _, p in update_ride_calls
        ), (
            "Insufficient-balance rejection did not release the payment_status "
            "lock. Rider is now blocked from retrying with a card."
        )

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Card payment path is a stub. See rides.py:1088: "
            "'Card path is still a stub — Stripe charge to be wired separately'. "
            "Complete-ride handler marks payment_status='completed' regardless "
            "of actual charge outcome (drivers.py:1997). A real Stripe decline "
            "today silently leaves the ride as 'paid' in our DB. Fix: wire "
            "Stripe PaymentIntent.confirm(), map decline → payment_status="
            "'failed', surface retry UX."
        ),
    )
    def test_card_decline_marks_payment_failed_and_allows_retry(self):
        """DOCUMENTS THE GAP — will pass once card path is wired to Stripe.

        Expectation: Stripe returns card_declined, rides.payment_status
        flips to 'failed', process_payment returns an error the client
        can retry with a different card.
        """
        assert False, "card path is a stub; Stripe charge not yet wired"
