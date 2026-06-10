"""KPI instrumentation for payment settlement (CLAUDE.md metric scheme).

spinr_payment_settlement_total{method,outcome} and
spinr_payment_settlement_duration_ms{method} — emitted once per
process-payment settlement, regardless of payment path.
"""

from __future__ import annotations

from backend.routes import rides as rides_mod
from backend.services.payment_service import PaymentResult
from backend.utils import metrics


def _outcome_count(method: str, outcome: str) -> int:
    bucket = metrics.snapshot()["counters"].get("spinr_payment_settlement_total", {})
    return bucket.get((("method", method), ("outcome", outcome)), 0)


def _duration_count(method: str) -> int:
    series = metrics.snapshot()["histograms"].get("spinr_payment_settlement_duration_ms", {})
    cell = series.get((("method", method),))
    return cell["count"] if cell else 0


class TestRecordSettlementMetrics:
    def test_wallet_success(self):
        before = _outcome_count("wallet", "success")
        d_before = _duration_count("wallet")
        rides_mod._record_settlement_metrics("wallet", PaymentResult(success=True), 42.0)
        assert _outcome_count("wallet", "success") == before + 1
        assert _duration_count("wallet") == d_before + 1

    def test_card_failure(self):
        before = _outcome_count("card", "failed")
        rides_mod._record_settlement_metrics(
            "card", PaymentResult(success=False, error="declined", status_code=402), 130.0
        )
        assert _outcome_count("card", "failed") == before + 1

    def test_corporate_already_paid_not_counted_as_success(self):
        """Idempotent replays moved no money — they must not inflate the
        settlement success rate (payment success KPI >= 99%)."""
        replay_before = _outcome_count("corporate", "already_paid")
        success_before = _outcome_count("corporate", "success")
        rides_mod._record_settlement_metrics("company_allowance", PaymentResult(success=True, already_paid=True), 5.0)
        assert _outcome_count("corporate", "already_paid") == replay_before + 1
        assert _outcome_count("corporate", "success") == success_before

    def test_unknown_method_maps_to_card(self):
        before = _outcome_count("card", "success")
        rides_mod._record_settlement_metrics("apple_pay", PaymentResult(success=True), 10.0)
        assert _outcome_count("card", "success") == before + 1
