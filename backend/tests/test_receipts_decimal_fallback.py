"""GET /rides/{id}/receipt money math is Decimal on every path (2026-09-25).

Follow-up to PR #5792's money audit: ``routes/rides/receipts.py`` summed the
legacy no-``grand_total`` fallback, and the cancelled-ride
``cancellation_fee`` field, with raw float ``+``, and round-tripped the
fare-lock tip through ``float()``. The response shape (floats at the wire)
is unchanged; only the arithmetic moved to Decimal via ``_d``/``_round``/``_f``.

DB access goes through the autouse-patched ``mock_supabase_client``
(``repositories.ride_repo.supabase``), not a hand-rolled ``db_supabase`` stub.

See docs/change-log/2026-09-25-receipts-float-fallback.md.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

RIDER_ID = "rider_dec"
RIDE_ID = "ride_dec_1"


def _ride(**kw) -> dict:
    base = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,
        "vehicle_type_id": None,
        "corporate_account_id": None,
        "status": "completed",
        "ride_completed_at": "2026-09-25T12:00:00+00:00",
        "base_fare": 3.00,
        "distance_fare": 6.90,
        "time_fare": 0.10,
        "booking_fee": 0.20,
        "airport_fee": 0,
        "surge_multiplier": 1.0,
        "distance_km": 5.0,
        # 3.00 + 6.90 + 0.10 + 0.20 — the float-drift-prone shape.
        "total_fare": 10.20,
        "area_fees_total": 0,
        "area_fees_breakdown": [],
        # Saskatchewan: GST 5% and PST 6% as separate persisted lines.
        "tax_breakdown": {
            "GST": {"rate": 5.0, "amount": 0.51},
            "PST": {"rate": 6.0, "amount": 0.61},
        },
        "tax_amount": 1.12,
        "tip_amount": 0,
        "grand_total": None,
        "fare_breakdown_snapshot": None,
    }
    base.update(kw)
    return base


async def _receipt(mock_supabase_client: MagicMock, ride: dict, settings: dict | None = None) -> dict:
    from backend.routes.rides import get_ride_receipt

    res = MagicMock()
    res.data = [ride]
    mock_supabase_client.table.return_value.execute = MagicMock(return_value=res)
    with patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings or {})):
        result = await get_ride_receipt(ride_id=RIDE_ID, current_user={"id": RIDER_ID})
    return result["receipt"]


def _lines_sum(lines: list[dict]) -> Decimal:
    return sum((Decimal(str(ln["amount"])) for ln in lines if ln.get("amount") is not None), Decimal("0"))


class TestLegacyFallbackTotal:
    async def test_float_drift_inputs_produce_exact_total(self, mock_supabase_client):
        ride = _ride(
            total_fare=0.1,
            area_fees_total=0.2,
            tax_amount=0,
            tax_breakdown={},
            base_fare=0.1,
            distance_fare=0,
            time_fare=0,
            booking_fee=0,
        )
        # Guard: the old float sum really does drift on these inputs.
        assert 0.1 + 0.2 + 0 + 0 != 0.3
        r = await _receipt(mock_supabase_client, ride)
        assert r["grand_total"] == 0.3
        assert isinstance(r["grand_total"], float)

    async def test_mixed_components_sum_exactly_in_decimal(self, mock_supabase_client):
        ride = _ride(total_fare=10.1, base_fare=2.9, area_fees_total=0.2, tip_amount=1.1)
        # 10.10 + 0.20 + 1.12 + 1.10 -> float gives 12.519999999999998
        assert 10.1 + 0.2 + 1.12 + 1.1 != 12.52
        r = await _receipt(mock_supabase_client, ride)
        assert r["grand_total"] == 12.52

    async def test_string_numeric_columns_are_summed_not_concatenated(self, mock_supabase_client):
        ride = _ride(total_fare="10.20", area_fees_total="0", tax_amount="1.12", tip_amount="0")
        r = await _receipt(mock_supabase_client, ride)
        assert r["grand_total"] == 11.32

    async def test_persisted_grand_total_is_passed_through_untouched(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride(grand_total=11.32))
        assert r["grand_total"] == 11.32


class TestGstPstLinesReconcile:
    async def test_gst_and_pst_are_separate_lines_that_sum_to_tax_amount(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride())
        tax_lines = [ln for ln in r["fare_breakdown"] if ln["type"] == "tax"]
        assert [ln["label"] for ln in tax_lines] == ["GST (5.0%)", "PST (6.0%)"]
        assert _lines_sum(tax_lines) == Decimal(str(r["tax_amount"])) == Decimal("1.12")
        # The two stay distinct amounts, never merged into one "Tax" row.
        assert {Decimal(str(ln["amount"])) for ln in tax_lines} == {Decimal("0.51"), Decimal("0.61")}

    async def test_fallback_lines_reconcile_to_grand_total(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride(tip_amount=1.1))
        assert _lines_sum(r["fare_breakdown"]) == Decimal(str(r["grand_total"])) == Decimal("12.42")

    async def test_persisted_grand_total_lines_reconcile(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride(grand_total=11.32))
        assert _lines_sum(r["fare_breakdown"]) == Decimal(str(r["grand_total"]))

    async def test_fare_locked_ten_tiny_fees_and_appended_tip_reconcile(self, mock_supabase_client):
        lines = [{"label": f"Fee {i}", "amount": 0.10, "type": "fee"} for i in range(10)]
        lines += [
            {"label": "GST (5.0%)", "amount": 0.05, "type": "tax"},
            {"label": "PST (6.0%)", "amount": 0.06, "type": "tax"},
        ]
        ride = _ride(fare_breakdown_snapshot={"lines": lines}, tip_amount=0.3)
        assert sum([0.10] * 10) != 1.0  # guard: float drift on ten 0.10s
        r = await _receipt(mock_supabase_client, ride, settings={"fare_lock_enabled": True})
        assert r["fare_locked"] is True
        tip = [ln for ln in r["fare_breakdown"] if ln["type"] == "tip"]
        assert tip == [{"label": "Tip", "amount": 0.3, "type": "tip"}]
        assert isinstance(tip[0]["amount"], float)
        assert r["grand_total"] == 1.41
        assert _lines_sum(r["fare_breakdown"]) == Decimal("1.41")
        assert [ln["label"] for ln in r["fare_breakdown"] if ln["type"] == "tax"] == ["GST (5.0%)", "PST (6.0%)"]


class TestCancellationFeeField:
    async def test_cancellation_fee_sums_exactly(self, mock_supabase_client):
        ride = _ride(
            status="cancelled",
            cancelled_at="2026-09-25T12:00:00+00:00",
            cancellation_fee_admin=0.1,
            cancellation_fee_driver=0.2,
        )
        r = await _receipt(mock_supabase_client, ride)
        assert r["cancellation_fee"] == 0.3
        assert isinstance(r["cancellation_fee"], float)

    async def test_completed_ride_cancellation_fee_stays_int_zero(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride())
        assert r["cancellation_fee"] == 0 and type(r["cancellation_fee"]) is int


class TestResponseShapeUnchanged:
    async def test_passthrough_fields_keep_their_db_types(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride())
        for k in ("base_fare", "distance_fare", "time_fare", "booking_fee", "tax_amount", "total_charged"):
            assert isinstance(r[k], float), k
        assert r["tax_breakdown"] == _ride()["tax_breakdown"]
        assert set(r) >= {"grand_total", "fare_breakdown", "fare_locked", "tax_amount", "tip_amount"}


class TestKnownReconciliationGaps:
    """Pre-existing, non-float gaps found while checking reconciliation.

    Not fixed here (behaviour change, not money-math drift) — recorded as
    strict xfails so whoever fixes them sees these flip. See the change log.
    """

    @pytest.mark.xfail(strict=True, reason="legacy fallback total omits discount_amount; fare lines subtract it")
    async def test_fallback_with_promo_reconciles(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride(discount_amount=2.0, promo_code="SAVE2"))
        assert _lines_sum(r["fare_breakdown"]) == Decimal(str(r["grand_total"]))

    @pytest.mark.xfail(
        strict=True, reason="persisted grand_total excludes tip; non-locked fare lines include a Tip line"
    )
    async def test_persisted_grand_total_with_tip_reconciles(self, mock_supabase_client):
        r = await _receipt(mock_supabase_client, _ride(grand_total=11.32, tip_amount=2.0))
        assert _lines_sum(r["fare_breakdown"]) == Decimal(str(r["grand_total"]))
