import unittest
from unittest.mock import patch
from runtime import Runtime, dry_run_setting
from test_controller import FakeFly
from policy import Sample


class RuntimeTests(unittest.TestCase):
    def test_invalid_mode_fails_closed(self):
        self.assertTrue(dry_run_setting("true"))
        self.assertFalse(dry_run_setting("false"))
        for value in ("FALSE", "0", "", "typo"):
            with self.assertRaises(ValueError):
                dry_run_setting(value)

    @patch("runtime.readiness", return_value={"0", "1"})
    @patch(
        "runtime.collect",
        return_value={str(i): Sample(0.9, 0.1, 1000) for i in range(2)},
    )
    def test_observation_mode_reports_without_mutation(self, collect, ready):
        fly = FakeFly()
        runtime = Runtime(fly, "0", "app", "org", "token", True, clock=lambda: 1000)
        runtime.cycle()
        self.assertEqual(runtime.metrics["spinr_burst_running_machines"], 2)
        self.assertEqual(
            runtime.metrics["spinr_burst_last_observation_timestamp_seconds"], 1000
        )
        self.assertEqual(fly.calls, [])

    @patch("runtime.collect", side_effect=ValueError("secret-must-not-appear"))
    def test_failures_reset_pressure_and_raise_monitoring_flag(self, collect):
        runtime = Runtime(
            FakeFly(), "0", "app", "org", "token", True, clock=lambda: 1000
        )
        runtime.controller.policy.high_since = 900
        with self.assertLogs("runtime", level="ERROR") as logs:
            runtime.cycle()
        self.assertNotIn("secret", str(logs.output))
        self.assertEqual(runtime.metrics["spinr_burst_action_required"], 1)
        self.assertEqual(
            runtime.metrics["spinr_burst_last_observation_timestamp_seconds"], 0
        )
        self.assertIsNone(runtime.controller.policy.high_since)

    @patch("runtime.readiness", return_value=set())
    @patch(
        "runtime.collect",
        return_value={str(i): Sample(0.9, 0.1, 1000) for i in range(2)},
    )
    def test_repeated_samples_do_not_clear_dependency_alarm(self, collect, ready):
        runtime = Runtime(
            FakeFly(), "0", "app", "org", "token", True, clock=lambda: 1000
        )
        runtime.cycle()
        runtime.cycle()
        self.assertEqual(runtime.metrics["spinr_burst_action_required"], 1)

    @patch("runtime.readiness", return_value={"0", "1"})
    @patch(
        "runtime.collect",
        return_value={str(i): Sample(0.9, 0.1, 1000) for i in range(8)},
    )
    def test_repeated_samples_do_not_clear_capacity_alarm(self, collect, ready):
        fly = FakeFly()
        for m in fly.raw:
            m["state"] = "started"
        runtime = Runtime(fly, "0", "app", "org", "token", True, clock=lambda: 1000)
        runtime.cycle()
        runtime.cycle()
        self.assertEqual(runtime.metrics["spinr_burst_action_required"], 1)

    @patch("runtime.readiness", return_value={"0", "1"})
    @patch(
        "runtime.collect",
        return_value={str(i): Sample(0.9, 0.1, 1000) for i in range(2)},
    )
    def test_observe_restart_restores_failed_start_metric(self, collect, ready):
        fly = FakeFly()
        fly.state = {
            "pending": None,
            "attempts": [{"id": "2", "at": 950, "outcome": "failed"}],
        }
        runtime = Runtime(fly, "0", "app", "org", "token", True, clock=lambda: 1000)
        runtime.cycle()
        self.assertEqual(
            runtime.metrics["spinr_burst_last_failed_start_timestamp_seconds"], 950
        )
