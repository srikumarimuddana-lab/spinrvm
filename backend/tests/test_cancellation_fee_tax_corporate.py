"""Cancellation-fee tax + corporate billing helpers (2026-09-23).

Covers ``services/cancellation_service.py``'s two new helpers:

* ``compute_cancellation_fee_tax`` — GST/PST/HST on a cancellation/no-show
  fee, reusing ``features.calculate_all_fees``' tax logic. Flag-gated
  (``cancellation_fee_tax_enabled``, default off) because taxability of the
  fee is a policy decision pending legal confirmation.
* ``bill_corporate_cancellation_fee`` — debits the company master wallet for
  a company_allowance ride's fee (``corporate_cancellation_fee_billing_enabled``,
  default off), and records a queryable ``corporate_cancellation_fee_unbilled``
  audit row whenever it does not bill.

See docs/change-log/2026-09-23-cancellation-fee-tax-receipt-corporate.md.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import cancellation_service as svc

SK_AREA = {"id": "area_sk", "gst_enabled": True, "gst_rate": 5.0, "pst_enabled": False}
TAX_ON = {"cancellation_fee_tax_enabled": True}


@pytest.mark.unit
class TestComputeCancellationFeeTax:
    async def test_flag_off_is_untaxed(self):
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), {}, SK_AREA)
        assert tax == Decimal("0")
        assert breakdown == {}

    async def test_gst_only_area(self):
        # 4.50 * 5% = 0.225 -> HALF_UP 0.23 (same quantization as the fare path).
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, SK_AREA)
        assert tax == Decimal("0.23")
        assert isinstance(tax, Decimal)
        assert breakdown == {"GST": {"rate": 5.0, "amount": 0.23}}

    async def test_gst_and_pst_are_separate_lines(self):
        area = {**SK_AREA, "pst_enabled": True, "pst_rate": 6.0}
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, area)
        assert breakdown["GST"]["amount"] == 0.23
        assert breakdown["PST"] == {"rate": 6.0, "amount": 0.27}
        assert tax == Decimal("0.50")

    async def test_hst_area(self):
        area = {"id": "a", "hst_enabled": True, "hst_rate": 13.0}
        tax, breakdown = await svc.compute_cancellation_fee_tax(Decimal("5.00"), TAX_ON, area)
        assert tax == Decimal("0.65")
        assert list(breakdown) == ["HST"]

    async def test_area_fees_are_never_added(self):
        # Must not fetch/apply area_fees (airport/night surcharges belong to a
        # trip, not a cancellation). _area_fees=[] means zero DB reads.
        with patch("backend.features.db_supabase.get_rows", AsyncMock(side_effect=AssertionError("no DB read"))):
            tax, _ = await svc.compute_cancellation_fee_tax(Decimal("10.00"), TAX_ON, SK_AREA)
        assert tax == Decimal("0.50")

    async def test_no_area_or_zero_fee_is_untaxed(self):
        assert (await svc.compute_cancellation_fee_tax(Decimal("4.50"), TAX_ON, None))[0] == Decimal("0")
        assert (await svc.compute_cancellation_fee_tax(Decimal("0"), TAX_ON, SK_AREA))[0] == Decimal("0")


CORP_RIDE = {"id": "ride_corp_1", "payment_method": "company_allowance", "corporate_account_id": "co_1"}
BILL_ON = {"corporate_cancellation_fee_billing_enabled": True}


def _bill(settings, **kw):
    return svc.bill_corporate_cancellation_fee(
        ride=kw.get("ride", CORP_RIDE),
        ride_id="ride_corp_1",
        amount=Decimal("4.73"),
        fee_driver=Decimal("4.00"),
        settings=settings,
        actor_user_id="actor_1",
        source="cancellation_fee",
    )


@pytest.mark.unit
class TestBillCorporateCancellationFee:
    async def test_bills_master_wallet_with_ride_scoped_idempotency(self):
        adjust = AsyncMock(return_value={"transaction_id": "t1", "deduped": False})
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(svc.corporate_wallet_service, "apply_adjustment", adjust),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            outcome = await _bill(BILL_ON)
        assert outcome == "billed"
        kwargs = adjust.await_args.kwargs
        assert kwargs["wallet_id"] == "w1"
        assert kwargs["amount"] == Decimal("-4.73")
        assert kwargs["ride_id"] == "ride_corp_1"  # migration 297 dedup key
        assert kwargs["floor"] == Decimal("0")
        audit.assert_not_awaited()  # billed -> no write-off row

    async def test_replay_is_deduped(self):
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(svc.corporate_wallet_service, "apply_adjustment", AsyncMock(return_value={"deduped": True})),
            patch.object(svc.db_supabase, "insert_one", AsyncMock()),
        ):
            assert await _bill(BILL_ON) == "deduped"

    @pytest.mark.parametrize(
        "settings,reason",
        [
            ({}, "billing_flag_off"),
            ({**BILL_ON, "corporate_billing_enabled": False}, "corporate_billing_disabled"),
        ],
    )
    async def test_gated_off_records_writeoff_and_moves_no_money(self, settings, reason):
        adjust = AsyncMock()
        audit = AsyncMock()
        with (
            patch.object(svc.corporate_wallet_service, "apply_adjustment", adjust),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(settings) == "unbilled"
        adjust.assert_not_awaited()
        row = audit.await_args.args[1]
        assert audit.await_args.args[0] == "audit_logs"
        assert row["action"] == "corporate_cancellation_fee_unbilled"
        assert row["entity_id"] == "ride_corp_1"
        assert row["details"]["reason"] == reason
        assert row["details"]["amount"] == "4.73"
        assert row["details"]["fee_driver"] == "4.00"
        assert row["details"]["company_id"] == "co_1"

    async def test_debit_failure_below_floor_records_writeoff(self):
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
            patch.object(
                svc.corporate_wallet_service,
                "apply_adjustment",
                AsyncMock(side_effect=RuntimeError("wallet_below_floor: new=-1 floor=0")),
            ),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(BILL_ON) == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "debit_failed"

    async def test_no_wallet_records_writeoff(self):
        audit = AsyncMock()
        with (
            patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
            patch.object(svc.db_supabase, "insert_one", audit),
        ):
            assert await _bill(BILL_ON) == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "no_wallet"

    async def test_missing_company_records_writeoff(self):
        audit = AsyncMock()
        with patch.object(svc.db_supabase, "insert_one", audit):
            outcome = await _bill(BILL_ON, ride={"id": "ride_corp_1", "payment_method": "company_allowance"})
        assert outcome == "unbilled"
        assert audit.await_args.args[1]["details"]["reason"] == "no_corporate_account"

    async def test_audit_write_failure_never_raises(self):
        with patch.object(svc.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await _bill({}) == "unbilled"


# ── cancel_ride_rider wiring ─────────────────────────────────────────

RIDER_ID = "rider_tax_cancel"
DRIVER_ID = "driver_tax_cancel"
RIDE_ID = "ride_tax_cancel_001"
FEE_SETTINGS = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 4.00}


def _arrived_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": "driver_arrived",
        "payment_method": "card",
        "payment_method_id": "pm_test_1",
        "service_area_id": "area_sk",
        # The stale pre-trip quote — must never leak into what is charged.
        "total_fare": 18.40,
        "grand_total": 19.32,
        "tax_amount": 0.92,
    }
    row.update(extra)
    return row


async def _run_rider_cancel(ride: dict, settings: dict, extra_patches: dict):
    from backend.routes import rides as rides_mod
    from backend.utils.stripe_charge import ChargeOutcome

    async def _find_one(table, flt, *a, **k):
        return SK_AREA if table == "service_areas" else ride

    mocks = {
        "charge": AsyncMock(
            return_value=ChargeOutcome(status="succeeded", payment_intent_id="pi_fee", charged_amount=Decimal("0"))
        ),
        "ledger": AsyncMock(return_value="evt"),
        "update_ride": AsyncMock(),
        "pay_driver": AsyncMock(return_value=True),
        "bill_corp": AsyncMock(return_value="billed"),
    }
    mocks.update(extra_patches)
    p = "backend.routes.rides._deps."
    with (
        patch(p + "db.find_one", AsyncMock(side_effect=_find_one)),
        patch(p + "get_app_settings", AsyncMock(return_value=settings)),
        patch(p + "db_supabase.get_user_by_id", AsyncMock(return_value={"id": RIDER_ID, "stripe_customer_id": "cus"})),
        patch(p + "charge_ancillary_fee", mocks["charge"]),
        patch(p + "record_ledger_event", mocks["ledger"]),
        patch(p + "pay_driver_cancellation_fee", mocks["pay_driver"]),
        patch(p + "bill_corporate_cancellation_fee", mocks["bill_corp"]),
        patch(p + "db_supabase.get_driver_by_id", AsyncMock(return_value={"id": DRIVER_ID, "user_id": "du"})),
        patch(p + "db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch(p + "db.insert_one", AsyncMock()),
        patch(p + "db_supabase.update_ride", mocks["update_ride"]),
        patch(p + "db_supabase.get_ride", AsyncMock(return_value={**ride, "status": "cancelled"})),
        patch(p + "db_supabase.set_driver_available", AsyncMock()),
        patch(p + "release_driver_and_close_period", AsyncMock()),
        patch(p + "manager.send_personal_message", AsyncMock()),
        patch(p + "manager.broadcast_ride_status", AsyncMock()),
        patch(p + "manager.broadcast_to_admins", AsyncMock()),
        patch(p + "send_push_notification", AsyncMock()),
        patch(p + "spawn", lambda coro: coro.close()),
    ):
        fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
        result = await fn(request=None, ride_id=RIDE_ID, reason="", current_user={"id": RIDER_ID})
    return result, mocks


@pytest.mark.unit
class TestRiderCancelWiring:
    async def test_tax_on_charges_fee_plus_tax_and_persists_breakdown(self):
        result, m = await _run_rider_cancel(_arrived_ride(), {**FEE_SETTINGS, **TAX_ON}, {})
        # 4.50 fee + 0.23 GST is what is actually charged — not the stale 19.32 quote.
        assert m["charge"].await_args.kwargs["amount"] == Decimal("4.73")
        meta = m["ledger"].await_args.kwargs["metadata"]
        assert m["ledger"].await_args.kwargs["delta_cents"] == 473
        assert meta["fee_tax"] == "0.23" and meta["fee_driver"] == "4.00" and meta["fee_admin"] == "0.50"
        # Driver payout stays the pre-tax driver share.
        assert m["pay_driver"].await_args.kwargs["fee"] == Decimal("4.00")
        tax_writes = [c.args[1] for c in m["update_ride"].call_args_list if "cancellation_fee_tax_amount" in c.args[1]]
        assert tax_writes == [
            {
                "cancellation_fee_tax_amount": 0.23,
                "cancellation_fee_tax_breakdown": {"GST": {"rate": 5.0, "amount": 0.23}},
            }
        ]
        # Pre-tax split columns keep their meaning; quote columns untouched.
        base = m["update_ride"].call_args_list[0].args[1]
        assert base["cancellation_fee_admin"] == 0.5 and base["cancellation_fee_driver"] == 4.0
        assert "grand_total" not in base and "tax_amount" not in base
        assert result["cancellation_fee"] == Decimal("4.73")
        assert result["cancellation_fee_tax"] == Decimal("0.23")

    async def test_tax_flag_off_is_byte_identical_to_before(self):
        result, m = await _run_rider_cancel(_arrived_ride(), FEE_SETTINGS, {})
        assert m["charge"].await_args.kwargs["amount"] == Decimal("4.50")
        assert m["ledger"].await_args.kwargs["metadata"]["fee_tax"] == "0.00"
        assert not any("cancellation_fee_tax_amount" in c.args[1] for c in m["update_ride"].call_args_list)
        assert result["cancellation_fee"] == Decimal("4.50")

    async def test_tax_column_write_failure_does_not_fail_the_cancel(self):
        async def _update(ride_id, payload):
            if "cancellation_fee_tax_amount" in payload:
                raise RuntimeError("column missing")

        result, _ = await _run_rider_cancel(
            _arrived_ride(), {**FEE_SETTINGS, **TAX_ON}, {"update_ride": AsyncMock(side_effect=_update)}
        )
        assert result["success"] is True

    async def test_company_allowance_bills_company_and_still_pays_driver(self):
        ride = _arrived_ride(payment_method="company_allowance", corporate_account_id="co_1", payment_method_id=None)
        result, m = await _run_rider_cancel(ride, {**FEE_SETTINGS, **TAX_ON}, {})
        m["charge"].assert_not_awaited()  # never a personal card
        kw = m["bill_corp"].await_args.kwargs
        assert kw["ride_id"] == RIDE_ID
        assert kw["amount"] == Decimal("4.73")
        assert kw["fee_driver"] == Decimal("4.00")
        assert kw["source"] == "cancellation_fee"
        m["pay_driver"].assert_awaited_once()
        assert result["cancellation_fee"] == Decimal("4.73")

    async def test_free_cancel_bills_nobody(self):
        ride = _arrived_ride(status="driver_accepted", payment_method="company_allowance", driver_accepted_at=None)
        _, m = await _run_rider_cancel(ride, {**FEE_SETTINGS, **TAX_ON}, {})
        m["bill_corp"].assert_not_awaited()
        m["pay_driver"].assert_not_awaited()


# ── mark_rider_noshow wiring (sibling path, same gaps) ───────────────


def _noshow_ride(**kw) -> dict:
    from datetime import datetime, timedelta, timezone

    base = {
        "id": "ride-ns",
        "status": "driver_arrived",
        "driver_id": "drv-1",
        "rider_id": "rider-1",
        "driver_arrived_at": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(),
        "service_area_id": "area_sk",
        "payment_method": "card",
        "payment_intent_id": None,
        "auth_status": None,
        "authorized_amount": 0,
    }
    base.update(kw)
    return base


async def _run_noshow(ride: dict, settings: dict):
    from backend.routes import drivers as drv
    from backend.utils.stripe_charge import ChargeOutcome

    area = {**SK_AREA, "noshow_wait_seconds": 300}
    m = {
        "fresh": AsyncMock(
            return_value=ChargeOutcome(status="succeeded", payment_intent_id="pi_ns", charged_amount=Decimal("4.73"))
        ),
        "ledger": AsyncMock(),
        "update_ride": AsyncMock(return_value={"id": "ride-ns"}),
        "pay_driver": AsyncMock(),
        "bill_corp": AsyncMock(return_value="billed"),
    }
    d = "backend.routes.drivers._deps."
    with (
        patch(d + "db_supabase.get_rows", AsyncMock(return_value=[{"id": "drv-1", "user_id": "user-1"}])),
        patch(d + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch(d + "db_supabase.update_one", AsyncMock(return_value={"id": "ride-ns"})),
        patch(d + "db_supabase.update_ride", m["update_ride"]),
        patch("backend.services.cancellation_service.pay_driver_cancellation_fee", m["pay_driver"]),
        patch("backend.services.cancellation_service.bill_corporate_cancellation_fee", m["bill_corp"]),
        patch(d + "db_supabase.set_driver_available", AsyncMock(return_value={"id": "drv-1", "is_available": True})),
        patch(d + "record_period_transition", AsyncMock()),
        patch(d + "manager.broadcast_ride_status", AsyncMock()),
        patch(d + "manager.broadcast_to_admins", AsyncMock()),
        patch(d + "send_push_notification", AsyncMock()),
        patch(d + "spawn", side_effect=lambda c: c.close()),
        patch("backend.services.ledger_service.record_event", m["ledger"]),
        patch(d + "db_supabase.get_user_by_id", AsyncMock(return_value={"stripe_customer_id": "cus_1"})),
        patch(d + "db_supabase.find_one", AsyncMock(return_value=area)),
        patch("backend.utils.stripe_charge.charge_ancillary_fee", m["fresh"]),
    ):
        await drv.mark_rider_noshow(ride_id="ride-ns", current_user={"id": "user-1"})
    return m


@pytest.mark.unit
class TestNoShowWiring:
    async def test_tax_on_charges_fee_plus_tax(self):
        m = await _run_noshow(_noshow_ride(), {**FEE_SETTINGS, **TAX_ON})
        assert m["fresh"].await_args.kwargs["amount"] == Decimal("4.73")
        assert m["ledger"].await_args.kwargs["metadata"]["fee_tax"] == "0.23"
        assert m["pay_driver"].await_args.kwargs["fee"] == Decimal("4.00")
        tax_writes = [c.args[1] for c in m["update_ride"].call_args_list if "cancellation_fee_tax_amount" in c.args[1]]
        assert tax_writes and tax_writes[0]["cancellation_fee_tax_amount"] == 0.23

    async def test_tax_off_unchanged(self):
        m = await _run_noshow(_noshow_ride(), FEE_SETTINGS)
        assert m["fresh"].await_args.kwargs["amount"] == Decimal("4.50")
        assert not any("cancellation_fee_tax_amount" in c.args[1] for c in m["update_ride"].call_args_list)

    async def test_company_allowance_noshow_bills_company(self):
        ride = _noshow_ride(payment_method="company_allowance", corporate_account_id="co_1")
        m = await _run_noshow(ride, {**FEE_SETTINGS, **TAX_ON})
        m["fresh"].assert_not_awaited()
        kw = m["bill_corp"].await_args.kwargs
        assert kw["amount"] == Decimal("4.73") and kw["source"] == "noshow_fee"
        m["pay_driver"].assert_awaited_once()


# ── get_ride pre-cancel disclosure ───────────────────────────────────


async def _disclosed_fee(settings: dict) -> Decimal:
    from starlette.requests import Request as SR

    from backend.routes.rides import get_ride

    ride = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,
        "status": "driver_accepted",
        "service_area_id": "area_sk",
        "payment_method": "card",
    }
    req = SR({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "client": ("t", 1)})
    with (
        patch("backend.routes.rides._deps.db_supabase") as db,
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=settings)),
    ):
        db.get_ride = AsyncMock(return_value=ride)
        db.get_rows = AsyncMock(return_value=[])
        db.find_one = AsyncMock(return_value=SK_AREA)
        db.get_driver_by_id = AsyncMock(return_value=None)
        result = await get_ride(request=req, ride_id=RIDE_ID, current_user={"id": RIDER_ID})
    return result["cancellation_fee"]


@pytest.mark.unit
class TestPreCancelDisclosure:
    async def test_disclosed_fee_includes_tax_when_charged(self):
        # What the rider is told before cancelling == what cancel_ride_rider collects.
        assert await _disclosed_fee({**FEE_SETTINGS, **TAX_ON}) == Decimal("4.73")

    async def test_disclosed_fee_unchanged_when_flag_off(self):
        assert await _disclosed_fee(FEE_SETTINGS) == Decimal("4.50")
