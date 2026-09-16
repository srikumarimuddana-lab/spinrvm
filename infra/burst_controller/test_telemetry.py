import unittest
from unittest.mock import patch
from telemetry import values, readiness, collect


def vector(items):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"instance": key}, "value": [1000, str(value)]}
                for key, value in items
            ],
        },
    }


class TelemetryTests(unittest.TestCase):
    def test_distinct_instances(self):
        self.assertEqual(values(vector([("a", 0.7), ("b", 0.9)])), {"a": 0.7, "b": 0.9})

    def test_duplicate_invalid_and_warning_results_rejected(self):
        bad = [
            vector([("a", 1), ("a", 2)]),
            vector([("a", "NaN")]),
            vector([("", 1)]),
            {"status": "error"},
            dict(vector([("a", 1)]), warnings=["partial result"]),
        ]
        for result in bad:
            with self.assertRaises(ValueError):
                values(result)

    @patch("telemetry.request")
    def test_partial_metrics_cannot_become_zero(self, get):
        get.side_effect = [vector([("a", 0.8)]), vector([]), vector([("a", 1000)])]
        with self.assertRaises(ValueError):
            collect("app", "org", "token")

    @patch("telemetry.request")
    def test_source_timestamp_not_query_timestamp(self, get):
        get.side_effect = [
            vector([("a", 0.8)]),
            vector([("a", 0.1)]),
            vector([("a", 980)]),
        ]
        self.assertEqual(collect("app", "org", "token")["a"].timestamp, 980)

    @patch("telemetry.request")
    def test_private_target_readiness_and_ssrf_guard(self, get):
        get.return_value = {"status": "ready", "db": {"status": "ok"}}
        raw = [{"id": "a", "state": "started", "private_ip": "fdaa::123"}]
        self.assertEqual(readiness(raw), {"a"})
        self.assertEqual(get.call_args.args[0], "http://[fdaa::123]:8000/ready")
        get.reset_mock()
        with self.assertRaises(ValueError):
            readiness([dict(raw[0], private_ip="127.0.0.1")])
        get.assert_not_called()

    @patch("telemetry.request")
    def test_unready_is_not_healthy(self, get):
        get.return_value = {"status": "not_ready", "db": {"status": "error"}}
        self.assertEqual(
            readiness([{"id": "a", "state": "started", "private_ip": "fdaa::1"}]), set()
        )
