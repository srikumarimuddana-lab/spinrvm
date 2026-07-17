"""Driver/platform GST split (migration 233) — calculation, snapshot
attribution, and the Stripe Tax recorder for the platform share.

The split invariant is the money-critical property: for every tax label,
driver + platform must equal the receipt's tax line EXACTLY (the platform
side is computed as the remainder), so the rider's receipt, the driver's
remittance, and Spinr's Stripe Tax filing always reconcile to the cent.
"""

import random
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import features
from utils import stripe_tax
from utils.earnings_snapshot import driver_tax_portion


def _dec(v) -> Decimal:
    return Decimal(str(v))


_AREA_GST = {"id": "area-1", "name": "Regina", "gst_enabled": True, "gst_rate": 5.0}


async def _fees(subtotal, driver_subtotal=None, area=_AREA_GST):
    """calculate_all_fees with no area_fees rows and a pre-matched area."""
    with patch.object(features.db_supabase, "get_rows", AsyncMock(return_value=[])):
        return await features.calculate_all_fees(
            50.44,
            -104.61,
            50.45,
            -104.62,
            5.0,
            subtotal,
            driver_subtotal=driver_subtotal,
            _all_areas=[area],
            _matched_area=area,
        )


class TestTaxSplitCalculation:
    async def test_split_sums_exactly_to_tax_amount(self):
        res = await _fees(13.50, driver_subtotal=11.00)
        split = res["tax_split"]
        assert _dec(res["tax_amount"]) == _dec("0.68")  # 13.50 * 5% = 0.675 → HALF_UP
        assert _dec(split["driver"]["GST"]) == _dec("0.55")  # 11.00 * 5%
        assert _dec(split["platform"]["GST"]) == _dec("0.13")  # exact remainder
        assert _dec(split["driver_total"]) + _dec(split["platform_total"]) == _dec(res["tax_amount"])
        assert _dec(split["platform_taxable_base"]) == _dec("2.50")

    async def test_split_invariant_holds_for_random_amounts(self):
        rng = random.Random(233)
        for _ in range(200):
            subtotal = rng.randrange(500, 20_000) / 100
            driver_sub = rng.randrange(0, int(subtotal * 100)) / 100
            res = await _fees(subtotal, driver_subtotal=driver_sub)
            split = res["tax_split"]
            assert _dec(split["driver_total"]) + _dec(split["platform_total"]) == _dec(res["tax_amount"]), (
                f"drift at subtotal={subtotal} driver={driver_sub}"
            )
            for label, amt in res["tax_breakdown"].items():
                assert _dec(split["driver"][label]) + _dec(split["platform"][label]) == _dec(amt["amount"])

    async def test_no_driver_subtotal_means_no_split(self):
        res = await _fees(13.50)
        assert "tax_split" not in res

    async def test_driver_subtotal_clamped_to_taxable_base(self):
        res = await _fees(10.00, driver_subtotal=99.00)
        split = res["tax_split"]
        assert _dec(split["platform_total"]) == _dec("0")
        assert _dec(split["driver_total"]) == _dec(res["tax_amount"])

    async def test_negative_driver_subtotal_attributes_all_to_platform(self):
        res = await _fees(10.00, driver_subtotal=-5.00)
        split = res["tax_split"]
        assert _dec(split["driver_total"]) == _dec("0")
        assert _dec(split["platform_total"]) == _dec(res["tax_amount"])

    async def test_hst_branch_splits_too(self):
        area = {"id": "a2", "hst_enabled": True, "hst_rate": 13.0}
        res = await _fees(20.00, driver_subtotal=15.00, area=area)
        split = res["tax_split"]
        assert "HST" in split["driver"]
        assert _dec(split["driver_total"]) + _dec(split["platform_total"]) == _dec(res["tax_amount"])


class TestDriverTaxPortion:
    def test_split_ride_returns_driver_total_only(self):
        ride = {"tax_amount": 0.68, "tax_split": {"driver_total": 0.55, "platform_total": 0.13}}
        assert driver_tax_portion(ride) == 0.55

    def test_legacy_ride_falls_back_to_full_tax_amount(self):
        assert driver_tax_portion({"tax_amount": 0.68}) == 0.68

    def test_zero_driver_total_is_not_treated_as_missing(self):
        ride = {"tax_amount": 0.13, "tax_split": {"driver_total": 0, "platform_total": 0.13}}
        assert driver_tax_portion(ride) == 0

    def test_no_tax_at_all(self):
        assert driver_tax_portion({}) == 0


def _settings(enabled=True):
    return {"stripe_tax_enabled": enabled, "stripe_secret_key": "sk_test_x"}


class TestStripeTaxRecorder:
    async def test_disabled_setting_records_nothing(self):
        with (
            patch.object(stripe_tax, "get_app_settings", AsyncMock(return_value=_settings(False))),
            patch.object(stripe_tax, "db_supabase") as db,
            patch.object(stripe_tax, "stripe") as stripe_mod,
        ):
            out = await stripe_tax.record_platform_ride_tax({"id": "r1", "tax_split": {}})
            assert out is None
            stripe_mod.tax.Calculation.create.assert_not_called()
            db.update_one.assert_not_called()

    async def test_zero_platform_tax_stamps_sentinel_without_stripe_call(self):
        db = MagicMock()
        db.update_one = AsyncMock()
        ride = {"id": "r1", "tax_split": {"platform_taxable_base": 0, "platform_total": 0}}
        with (
            patch.object(stripe_tax, "get_app_settings", AsyncMock(return_value=_settings())),
            patch.object(stripe_tax, "db_supabase", db),
            patch.object(stripe_tax, "stripe") as stripe_mod,
        ):
            out = await stripe_tax.record_platform_ride_tax(ride)
            assert out == stripe_tax.NO_PLATFORM_TAX
            stripe_mod.tax.Calculation.create.assert_not_called()
            _filters, update = db.update_one.call_args.args[1], db.update_one.call_args.args[2]
            assert update == {"stripe_tax_transaction_id": stripe_tax.NO_PLATFORM_TAX}

    async def test_records_transaction_with_unique_reference_and_stamps_ride(self):
        db = MagicMock()
        db.update_one = AsyncMock()
        calc = MagicMock(id="taxcalc_1", tax_amount_exclusive=13)
        txn = MagicMock(id="tax_txn_1")
        ride = {
            "id": "r1",
            "tax_split": {"platform_taxable_base": 2.50, "platform_total": 0.13},
        }
        with (
            patch.object(stripe_tax, "get_app_settings", AsyncMock(return_value=_settings())),
            patch.object(stripe_tax, "db_supabase", db),
            patch.object(stripe_tax.stripe.tax.Calculation, "create", MagicMock(return_value=calc)) as c_mock,
            patch.object(
                stripe_tax.stripe.tax.Transaction, "create_from_calculation", MagicMock(return_value=txn)
            ) as t_mock,
        ):
            out = await stripe_tax.record_platform_ride_tax(ride)
            assert out == "tax_txn_1"
            line = c_mock.call_args.kwargs["line_items"][0]
            assert line["amount"] == 250  # dollars → integer cents
            assert t_mock.call_args.kwargs["reference"] == "spinr-ride-r1-platform-tax"
            # Claim filter: only stamp while still NULL (replica race safety)
            assert db.update_one.call_args.args[1] == {"id": "r1", "stripe_tax_transaction_id": None}
            assert db.update_one.call_args.args[2] == {"stripe_tax_transaction_id": "tax_txn_1"}

    async def test_partial_refund_does_not_reverse(self):
        ride = {
            "id": "r1",
            "grand_total": 20.00,
            "stripe_tax_transaction_id": "tax_txn_1",
        }
        with (
            patch.object(stripe_tax, "get_app_settings", AsyncMock(return_value=_settings())),
            patch.object(stripe_tax.stripe.tax.Transaction, "create_reversal", MagicMock()) as r_mock,
        ):
            out = await stripe_tax.reverse_platform_ride_tax(ride, refund_cents=500)
            assert out is None
            r_mock.assert_not_called()

    async def test_full_refund_reverses_in_full_mode(self):
        ride = {
            "id": "r1",
            "grand_total": 20.00,
            "stripe_tax_transaction_id": "tax_txn_1",
        }
        rev = MagicMock(id="tax_rev_1")
        with (
            patch.object(stripe_tax, "get_app_settings", AsyncMock(return_value=_settings())),
            patch.object(stripe_tax.stripe.tax.Transaction, "create_reversal", MagicMock(return_value=rev)) as r_mock,
        ):
            out = await stripe_tax.reverse_platform_ride_tax(ride, refund_cents=2000)
            assert out == "tax_rev_1"
            assert r_mock.call_args.kwargs["mode"] == "full"
            assert r_mock.call_args.kwargs["original_transaction"] == "tax_txn_1"

    async def test_sentinel_transaction_ids_never_reverse(self):
        for sentinel in (stripe_tax.NO_PLATFORM_TAX, stripe_tax.RECORDED_UNLINKED, None):
            ride = {"id": "r1", "grand_total": 20.00, "stripe_tax_transaction_id": sentinel}
            with patch.object(stripe_tax.stripe.tax.Transaction, "create_reversal", MagicMock()) as r_mock:
                assert await stripe_tax.reverse_platform_ride_tax(ride, refund_cents=2000) is None
                r_mock.assert_not_called()
