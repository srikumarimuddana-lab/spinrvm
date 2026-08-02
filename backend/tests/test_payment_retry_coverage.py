"""Coverage for utils/payment_retry.py (A1c, Sub-tier C — money-adjacent:
a bug here means a failed Stripe payment or stuck driver payout silently
never retries, which is a real financial/support-escalation consequence,
not just an untested line).

`tests/test_payment_retry.py` already covers the core double-charge guard
(claim race, invoice-skip, requires_capture happy/edge paths, unexpected
intent-state release). This file fills the remaining gaps: the
purchase-conversion side hook, the invoice-claim staleness helper, the
admin-alert/payout-notify error-swallow branches, the 24h/processing-window
skip branches of the main scan, the guest-corporate settlement sweep, and
the `payment_retry_loop` background loop itself (lock contention,
per-sub-step exception isolation, heartbeat).

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import payment_retry

RIDE_ID = "ride_cov_001"
PI_ID = "pi_cov_abc"
STRIPE_SECRET = "sk_test_secret"


def _make_ride(**overrides) -> dict:
    base = {
        "id": RIDE_ID,
        "rider_id": "rider_1",
        "driver_id": "driver_1",
        "payment_intent_id": PI_ID,
        "payment_status": "failed",
        "payment_retry_count": 0,
        "total_fare": 25.50,
        "grand_total": 25.50,
        "tip_amount": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _fake_intent(status: str, amount: int = 2550) -> MagicMock:
    intent = MagicMock()
    intent.status = status
    intent.amount = amount
    intent.id = PI_ID
    return intent


# ---------------------------------------------------------------------------
# _fire_purchase_conversion
# ---------------------------------------------------------------------------


class TestFirePurchaseConversion:
    @pytest.mark.anyio
    async def test_uses_get_ride_when_available(self):
        ride_row = {"id": RIDE_ID, "rider_id": "rider_1", "grand_total": 30, "tip_amount": 2}
        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(return_value=ride_row)
        mock_send = AsyncMock()
        with (
            patch("utils.payment_retry.db", mock_db),
            patch(
                "services.meta_conversions_service.send_ride_purchase_for_ride",
                mock_send,
            ),
        ):
            await payment_retry._fire_purchase_conversion(RIDE_ID)
        mock_send.assert_awaited_once()
        args = mock_send.await_args[0]
        assert args[0] == ride_row
        assert args[1] == "rider_1"
        assert args[2] == 32  # grand_total + tip_amount

    @pytest.mark.anyio
    async def test_falls_back_to_get_rows_when_no_get_ride_hasattr(self):
        ride_row = {"id": RIDE_ID, "rider_id": "rider_1", "total_fare": 10, "tip_amount": 0}
        mock_db = MagicMock(spec=["get_rows"])
        mock_db.get_rows = AsyncMock(return_value=[ride_row])
        mock_send = AsyncMock()
        with (
            patch("utils.payment_retry.db", mock_db),
            patch(
                "services.meta_conversions_service.send_ride_purchase_for_ride",
                mock_send,
            ),
        ):
            await payment_retry._fire_purchase_conversion(RIDE_ID)
        mock_send.assert_awaited_once()

    @pytest.mark.anyio
    async def test_logs_error_when_ride_not_found(self):
        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(return_value=None)
        mock_db.get_rows = AsyncMock(return_value=[])
        with patch("utils.payment_retry.db", mock_db):
            # Must not raise — this runs inside a background loop.
            await payment_retry._fire_purchase_conversion(RIDE_ID)

    @pytest.mark.anyio
    async def test_never_raises_on_internal_exception(self):
        mock_db = MagicMock()
        mock_db.get_ride = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("utils.payment_retry.db", mock_db):
            await payment_retry._fire_purchase_conversion(RIDE_ID)


# ---------------------------------------------------------------------------
# _invoice_claim_is_stale
# ---------------------------------------------------------------------------


class TestInvoiceClaimIsStale:
    def test_legacy_sentinel_without_timestamp_is_not_stale(self):
        assert payment_retry._invoice_claim_is_stale("pending:abc-uuid") is False

    def test_fresh_sentinel_is_not_stale(self):
        now_epoch = datetime.now(timezone.utc).timestamp()
        assert payment_retry._invoice_claim_is_stale(f"pending:{now_epoch}:uuid") is False

    def test_old_sentinel_is_stale(self):
        old_epoch = (datetime.now(timezone.utc) - timedelta(seconds=999)).timestamp()
        assert payment_retry._invoice_claim_is_stale(f"pending:{old_epoch}:uuid") is True

    def test_malformed_timestamp_returns_false(self):
        assert payment_retry._invoice_claim_is_stale("pending:not-a-float:uuid") is False


# ---------------------------------------------------------------------------
# _alert_admins_payment_exhausted
# ---------------------------------------------------------------------------


class TestAlertAdminsPaymentExhausted:
    @pytest.mark.anyio
    async def test_broadcast_failure_does_not_block_push_or_logging(self):
        ride = _make_ride()
        mock_manager = MagicMock()
        mock_manager.broadcast_to_admins = AsyncMock(side_effect=RuntimeError("ws down"))
        mock_db = MagicMock()
        mock_db.get_rows = AsyncMock(return_value=[{"id": "admin_1"}])
        with (
            patch("utils.payment_retry.manager", mock_manager),
            patch("utils.payment_retry.db", mock_db),
            patch("utils.payment_retry.send_push_notification", AsyncMock()),
        ):
            await payment_retry._alert_admins_payment_exhausted(ride)

    @pytest.mark.anyio
    async def test_admin_lookup_failure_is_swallowed(self):
        ride = _make_ride()
        mock_manager = MagicMock()
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_rows = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch("utils.payment_retry.manager", mock_manager),
            patch("utils.payment_retry.db", mock_db),
        ):
            await payment_retry._alert_admins_payment_exhausted(ride)

    @pytest.mark.anyio
    async def test_individual_admin_push_failure_does_not_stop_the_loop(self):
        ride = _make_ride()
        mock_manager = MagicMock()
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_rows = AsyncMock(return_value=[{"id": "admin_1"}, {"id": "admin_2"}])
        mock_push = AsyncMock(side_effect=[RuntimeError("push failed"), None])
        with (
            patch("utils.payment_retry.manager", mock_manager),
            patch("utils.payment_retry.db", mock_db),
            patch("utils.payment_retry.send_push_notification", mock_push),
        ):
            await payment_retry._alert_admins_payment_exhausted(ride)
        assert mock_push.await_count == 2


# ---------------------------------------------------------------------------
# update_payout_status / notify_driver_payout_failed
# ---------------------------------------------------------------------------


class TestPayoutHelpers:
    @pytest.mark.anyio
    async def test_update_payout_status_writes_expected_shape(self):
        mock_update = AsyncMock(return_value={"id": "payout_1"})
        with patch("utils.payment_retry.db.update_one", mock_update):
            await payment_retry.update_payout_status("payout_1", "failed")
        args = mock_update.await_args[0]
        assert args[0] == "payouts"
        assert args[1] == {"id": "payout_1"}
        assert args[2]["$set"]["status"] == "failed"

    @pytest.mark.anyio
    async def test_notify_driver_payout_failed_happy_path(self):
        mock_push = AsyncMock()
        with patch("utils.payment_retry.send_push_notification", mock_push):
            await payment_retry.notify_driver_payout_failed("driver_1", "payout_1")
        mock_push.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_driver_payout_failed_swallows_push_exception(self):
        mock_push = AsyncMock(side_effect=RuntimeError("push down"))
        with patch("utils.payment_retry.send_push_notification", mock_push):
            await payment_retry.notify_driver_payout_failed("driver_1", "payout_1")


# ---------------------------------------------------------------------------
# retry_stuck_payouts
# ---------------------------------------------------------------------------


class TestRetryStuckPayouts:
    @pytest.mark.anyio
    async def test_fetch_failure_returns_early(self):
        mock_get = AsyncMock(side_effect=RuntimeError("db down"))
        mock_update = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", mock_get),
            patch("utils.payment_retry.db.update_one", mock_update),
        ):
            await payment_retry.retry_stuck_payouts()
        mock_update.assert_not_awaited()

    @pytest.mark.anyio
    async def test_below_max_retries_is_left_alone(self):
        payout = {"id": "p1", "driver_id": "d1", "retry_count": 1, "status": "pending"}
        mock_update = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[payout])),
            patch("utils.payment_retry.db.update_one", mock_update),
        ):
            await payment_retry.retry_stuck_payouts()
        mock_update.assert_not_awaited()

    @pytest.mark.anyio
    async def test_claim_race_lost_skips_notify(self):
        payout = {"id": "p1", "driver_id": "d1", "retry_count": 3, "status": "pending"}
        mock_update = AsyncMock(return_value=None)
        mock_notify = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[payout])),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry.notify_driver_payout_failed", mock_notify),
        ):
            await payment_retry.retry_stuck_payouts()
        mock_notify.assert_not_awaited()

    @pytest.mark.anyio
    async def test_claim_won_marks_failed_and_notifies(self):
        payout = {"id": "p1", "driver_id": "d1", "retry_count": 3, "status": "pending"}
        mock_update = AsyncMock(return_value={"id": "p1"})
        mock_notify = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[payout])),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry.notify_driver_payout_failed", mock_notify),
        ):
            await payment_retry.retry_stuck_payouts()
        mock_notify.assert_awaited_once_with("d1", "p1")
        set_body = mock_update.await_args[0][2]["$set"]
        assert set_body["status"] == "failed"


# ---------------------------------------------------------------------------
# retry_failed_payments — scan-level branches
# ---------------------------------------------------------------------------


class TestRetryFailedPaymentsScan:
    @pytest.mark.anyio
    async def test_fetch_failure_returns_early(self):
        mock_get = AsyncMock(side_effect=RuntimeError("db down"))
        with patch("utils.payment_retry.db.get_rows", mock_get):
            await payment_retry.retry_failed_payments()

    @pytest.mark.anyio
    async def test_exhausted_and_not_yet_alerted_claims_and_alerts(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES, admin_alerted_payment_exhausted=False)
        mock_update = AsyncMock(return_value={"id": RIDE_ID})
        mock_alert = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
        ):
            await payment_retry.retry_failed_payments()
        mock_alert.assert_awaited_once()
        set_body = mock_update.await_args[0][2]["$set"]
        assert set_body["admin_alerted_payment_exhausted"] is True

    @pytest.mark.anyio
    async def test_exhausted_claim_race_lost_skips_alert(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES, admin_alerted_payment_exhausted=False)
        mock_update = AsyncMock(return_value=None)
        mock_alert = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
        ):
            await payment_retry.retry_failed_payments()
        mock_alert.assert_not_awaited()

    @pytest.mark.anyio
    async def test_exhausted_and_already_alerted_is_skipped(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES, admin_alerted_payment_exhausted=True)
        mock_update = AsyncMock()
        mock_alert = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", mock_update),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
        ):
            await payment_retry.retry_failed_payments()
        mock_update.assert_not_awaited()
        mock_alert.assert_not_awaited()

    @pytest.mark.anyio
    async def test_ride_older_than_24h_is_skipped(self):
        old_created = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        ride = _make_ride(created_at=old_created)
        mock_confirm = MagicMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock()),
            patch("stripe.PaymentIntent.confirm", mock_confirm),
        ):
            await payment_retry.retry_failed_payments()
        mock_confirm.assert_not_called()

    @pytest.mark.anyio
    async def test_processing_status_within_30min_window_is_skipped(self):
        recent_update = datetime.now(timezone.utc).isoformat()
        ride = _make_ride(payment_status="processing", updated_at=recent_update)
        mock_retrieve = MagicMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", mock_retrieve),
        ):
            await payment_retry.retry_failed_payments()
        mock_retrieve.assert_not_called()

    @pytest.mark.anyio
    async def test_processing_status_past_30min_window_proceeds(self):
        old_update = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
        ride = _make_ride(payment_status="processing", updated_at=old_update)
        mock_retrieve = MagicMock(return_value=_fake_intent("succeeded"))
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", mock_retrieve),
            patch("utils.payment_retry._fire_purchase_conversion", AsyncMock()),
        ):
            await payment_retry.retry_failed_payments()
        mock_retrieve.assert_called_once()

    @pytest.mark.anyio
    async def test_missing_payment_intent_id_is_skipped(self):
        ride = _make_ride(payment_intent_id=None)
        mock_update = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", mock_update),
        ):
            await payment_retry.retry_failed_payments()
        mock_update.assert_not_awaited()

    @pytest.mark.anyio
    async def test_missing_stripe_secret_is_skipped(self):
        ride = _make_ride()
        mock_update = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": ""})),
            patch("utils.payment_retry.db.update_one", mock_update),
        ):
            await payment_retry.retry_failed_payments()
        mock_update.assert_not_awaited()

    @pytest.mark.anyio
    async def test_requires_capture_owed_falls_back_to_total_fare(self):
        ride = _make_ride(grand_total=None, total_fare=20.0, tip_amount=0)
        mock_capture = MagicMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=_fake_intent("requires_capture", amount=5000))),
            patch("stripe.PaymentIntent.capture", mock_capture),
            patch("services.payment_service.record_payment_event", AsyncMock()),
            patch("utils.payment_retry._fire_purchase_conversion", AsyncMock()),
        ):
            await payment_retry.retry_failed_payments()
        mock_capture.assert_called_once()
        assert mock_capture.call_args.kwargs["amount_to_capture"] == 2000

    @pytest.mark.anyio
    async def test_unexpected_state_exhausts_and_alerts_when_new_count_hits_max(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES - 1)
        mock_alert = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock(return_value=_fake_intent("some_weird_state"))),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
        ):
            await payment_retry.retry_failed_payments()
        mock_alert.assert_awaited_once()

    @pytest.mark.anyio
    async def test_stripe_exception_marks_failed_and_notifies_rider_on_exhaustion(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES - 1)
        mock_alert = AsyncMock()
        mock_push = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock(side_effect=RuntimeError("stripe down"))),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
            patch("utils.payment_retry.send_push_notification", mock_push),
        ):
            await payment_retry.retry_failed_payments()
        mock_alert.assert_awaited_once()
        mock_push.assert_awaited_once()

    @pytest.mark.anyio
    async def test_stripe_exception_rider_push_failure_is_swallowed_on_exhaustion(self):
        ride = _make_ride(payment_retry_count=payment_retry.MAX_RETRIES - 1)
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock(side_effect=RuntimeError("stripe down"))),
            patch("utils.payment_retry._alert_admins_payment_exhausted", AsyncMock()),
            patch("utils.payment_retry.send_push_notification", AsyncMock(side_effect=RuntimeError("push down"))),
        ):
            # Must not raise — the push failure is caught and logged, not propagated.
            await payment_retry.retry_failed_payments()

    @pytest.mark.anyio
    async def test_stripe_exception_below_max_does_not_alert_or_push(self):
        ride = _make_ride(payment_retry_count=0)
        mock_alert = AsyncMock()
        mock_push = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("stripe.PaymentIntent.retrieve", MagicMock(side_effect=RuntimeError("stripe down"))),
            patch("utils.payment_retry._alert_admins_payment_exhausted", mock_alert),
            patch("utils.payment_retry.send_push_notification", mock_push),
        ):
            await payment_retry.retry_failed_payments()
        mock_alert.assert_not_awaited()
        mock_push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_stale_pending_invoice_sentinel_logs_and_skips(self):
        old_epoch = (datetime.now(timezone.utc) - timedelta(seconds=999)).timestamp()
        ride = _make_ride(stripe_invoice_id=f"pending:{old_epoch}:uuid")
        mock_update = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
            patch("utils.payment_retry.get_app_settings", AsyncMock(return_value={"stripe_secret_key": STRIPE_SECRET})),
            patch("utils.payment_retry.db.update_one", mock_update),
        ):
            await payment_retry.retry_failed_payments()
        mock_update.assert_not_awaited()


# ---------------------------------------------------------------------------
# sweep_guest_corporate_settlements
# ---------------------------------------------------------------------------


class TestSweepGuestCorporateSettlements:
    @pytest.mark.anyio
    async def test_query_failure_returns_early(self):
        mock_settle = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
            patch(
                "services.payment_service.auto_settle_guest_corporate",
                mock_settle,
            ),
        ):
            await payment_retry.sweep_guest_corporate_settlements()
        mock_settle.assert_not_awaited()

    @pytest.mark.anyio
    async def test_settles_each_stuck_ride(self):
        rows = [{"id": "r1"}, {"id": "r2"}]
        mock_settle = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=rows)),
            patch(
                "services.payment_service.auto_settle_guest_corporate",
                mock_settle,
            ),
        ):
            await payment_retry.sweep_guest_corporate_settlements()
        assert mock_settle.await_count == 2

    @pytest.mark.anyio
    async def test_one_ride_failing_does_not_stop_the_sweep(self):
        rows = [{"id": "r1"}, {"id": "r2"}]
        mock_settle = AsyncMock(side_effect=[RuntimeError("settle failed"), None])
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=rows)),
            patch(
                "services.payment_service.auto_settle_guest_corporate",
                mock_settle,
            ),
        ):
            await payment_retry.sweep_guest_corporate_settlements()
        assert mock_settle.await_count == 2

    @pytest.mark.anyio
    async def test_no_stuck_rides_is_a_noop(self):
        mock_settle = AsyncMock()
        with (
            patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[])),
            patch(
                "services.payment_service.auto_settle_guest_corporate",
                mock_settle,
            ),
        ):
            await payment_retry.sweep_guest_corporate_settlements()
        mock_settle.assert_not_awaited()


# ---------------------------------------------------------------------------
# payment_retry_loop
# ---------------------------------------------------------------------------


class TestPaymentRetryLoop:
    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_work_sleeps_and_continues(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        mock_retry = AsyncMock()
        with (
            patch("utils.payment_retry.redis_set_nx", AsyncMock(return_value=False)),
            patch("utils.payment_retry.retry_failed_payments", mock_retry),
            patch("utils.payment_retry.asyncio.sleep", _fake_sleep),
            patch("utils.payment_retry._record_heartbeat", MagicMock()) as mock_hb,
        ):
            with pytest.raises(asyncio.CancelledError):
                await payment_retry.payment_retry_loop()
        # `continue` after the first skipped tick sends control back to the
        # top of the loop for a second lock check — proving the loop body
        # actually re-enters rather than falling through.
        mock_retry.assert_not_awaited()
        assert mock_hb.call_count == 2
        assert sleep_calls == [payment_retry.RETRY_INTERVAL_SECONDS, payment_retry.RETRY_INTERVAL_SECONDS]

    @pytest.mark.anyio
    async def test_lock_acquired_runs_all_three_substeps_and_isolates_failures(self):
        call_order = []

        async def _fake_retry_payments():
            call_order.append("payments")
            raise RuntimeError("payments failed")

        async def _fake_retry_payouts():
            call_order.append("payouts")
            raise RuntimeError("payouts failed")

        async def _fake_sweep():
            call_order.append("sweep")
            raise RuntimeError("sweep failed")

        async def _fake_sleep(seconds):
            raise asyncio.CancelledError()

        with (
            patch("utils.payment_retry.redis_set_nx", AsyncMock(return_value=True)),
            patch("utils.payment_retry.retry_failed_payments", _fake_retry_payments),
            patch("utils.payment_retry.retry_stuck_payouts", _fake_retry_payouts),
            patch("utils.payment_retry.sweep_guest_corporate_settlements", _fake_sweep),
            patch("utils.payment_retry.asyncio.sleep", _fake_sleep),
            patch("utils.payment_retry._record_heartbeat", MagicMock()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await payment_retry.payment_retry_loop()
        # All three sub-steps run despite each raising — exceptions are
        # isolated per-substep, never crashing the loop.
        assert call_order == ["payments", "payouts", "sweep"]

    @pytest.mark.anyio
    async def test_happy_path_records_heartbeat_and_sleeps_with_jitter(self):
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()

        with (
            patch("utils.payment_retry.redis_set_nx", AsyncMock(return_value=True)),
            patch("utils.payment_retry.retry_failed_payments", AsyncMock()),
            patch("utils.payment_retry.retry_stuck_payouts", AsyncMock()),
            patch("utils.payment_retry.sweep_guest_corporate_settlements", AsyncMock()),
            patch("utils.payment_retry.asyncio.sleep", _fake_sleep),
            patch("utils.payment_retry._record_heartbeat", MagicMock()) as mock_hb,
        ):
            with pytest.raises(asyncio.CancelledError):
                await payment_retry.payment_retry_loop()
        assert mock_hb.call_count == 1
        assert len(sleep_calls) == 1
        delta = payment_retry.RETRY_INTERVAL_SECONDS * 0.1
        lo = payment_retry.RETRY_INTERVAL_SECONDS - delta
        hi = payment_retry.RETRY_INTERVAL_SECONDS + delta
        assert lo <= sleep_calls[0] <= hi


# ---------------------------------------------------------------------------
# _pod_id
# ---------------------------------------------------------------------------


def test_pod_id_combines_hostname_and_pid():
    pod_id = payment_retry._pod_id()
    assert ":" in pod_id
    host, _, pid = pod_id.partition(":")
    assert host
    assert pid.isdigit()
