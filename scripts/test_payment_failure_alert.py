"""Regression checks for the Grafana payment failure rate rule."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def payment_rule() -> dict:
    provision = yaml.safe_load((ROOT / "metrics-agent/grafana/alert-rules.yaml").read_text())
    return next(
        rule
        for group in provision["groups"]
        for rule in group["rules"]
        if rule["uid"] == "spinr-payment-failure-rate-breach"
    )


class PaymentFailureAlertTests(unittest.TestCase):
    def test_rule_requires_twenty_fly_settlements_before_testing_rate(self):
        rule = payment_rule()
        expression = rule["data"][0]["model"]["expr"]
        compact = " ".join(expression.split())

        self.assertEqual(
            compact,
            '(sum(increase(spinr_payment_settlement_total{outcome="failed",provider="fly"}[10m])) '
            '/ sum(increase(spinr_payment_settlement_total{provider="fly"}[10m]))) and on() '
            '(sum(increase(spinr_payment_settlement_total{provider="fly"}[10m])) >= 20)',
        )
        self.assertEqual(rule.get("noDataState"), "NoData")
        self.assertEqual(rule.get("execErrState"), "Alerting")

    def test_sample_guard_truth_table(self):
        def condition(failed: int, settled: int) -> bool:
            return settled >= 20 and settled > 0 and failed / settled > 0.01

        self.assertFalse(condition(1, 1))  # below sample floor
        self.assertFalse(condition(1, 19))
        self.assertFalse(condition(0, 20))
        self.assertTrue(condition(1, 20))
        self.assertFalse(condition(2, 200))  # exactly 1% is not a breach
        self.assertTrue(condition(3, 200))


if __name__ == "__main__":
    unittest.main()
