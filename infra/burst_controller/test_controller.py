import copy
import unittest
from controller import Controller
from policy import Sample


class FakeFly:
    def __init__(self):
        self.raw = [
            {
                "id": str(i),
                "region": "yyz",
                "state": "started" if i < 2 else "stopped",
                "config": {
                    "metadata": {"fly_process_group": "app" if i < 2 else "burst"}
                },
            }
            for i in range(8)
        ]
        self.state, self.calls, self.now = {}, [], 1000
        self.expires, self.fail_start = 1060, False

    def machines(self):
        return copy.deepcopy(self.raw)

    def journal(self, anchor):
        return copy.deepcopy(self.state)

    def lease(self, machine):
        self.calls.append(("lease", machine))
        return {"nonce": "nonce-" + machine, "expires_at": self.expires}

    def release(self, machine, nonce):
        self.calls.append(("release", machine))

    def save(self, anchor, state, nonce):
        self.calls.append(("save", anchor))
        self.state = copy.deepcopy(state)

    def start(self, machine, nonce):
        self.calls.append(("start", machine))
        if self.fail_start:
            raise TimeoutError()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.fly = FakeFly()
        self.controller = Controller(self.fly, "0", clock=lambda: self.fly.now)

    def tick(self, ready=None, dry=False):
        raw = self.fly.machines()
        samples = {
            m["id"]: Sample(0.9, 0.1, self.fly.now)
            for m in raw
            if m["state"] == "started"
        }
        return self.controller.tick(
            raw, samples, {"0", "1"} if ready is None else ready, dry
        )

    def test_journal_precedes_start_and_both_leases_are_released(self):
        self.assertEqual(self.tick().target, "2")
        self.assertLess(
            self.fly.calls.index(("save", "0")), self.fly.calls.index(("start", "2"))
        )
        self.assertEqual(self.fly.state["pending"], "2")
        self.assertIn(("release", "0"), self.fly.calls)
        self.assertIn(("release", "2"), self.fly.calls)

    def test_uncertain_result_survives_controller_restart(self):
        self.fly.fail_start = True
        with self.assertRaises(TimeoutError):
            self.tick()
        self.fly.fail_start = False
        self.fly.now += 15
        self.controller = Controller(self.fly, "0", clock=lambda: self.fly.now)
        self.assertEqual(self.tick().reason, "awaiting_readiness")
        self.assertEqual(self.fly.calls.count(("start", "2")), 1)

    def test_dry_run_has_no_lease_or_writes(self):
        self.assertEqual(self.tick(dry=True).target, "2")
        self.assertEqual(self.fly.calls, [])

    def test_expiring_lease_cannot_mutate(self):
        self.fly.expires = 1005
        with self.assertRaises(ValueError):
            self.tick()
        self.assertFalse(any(c[0] in ("save", "start") for c in self.fly.calls))

    def test_fleet_changed_during_probe_aborts(self):
        original = self.fly.machines()
        self.fly.raw[2]["state"] = "started"
        with self.assertRaises(ValueError):
            self.controller.tick(original, {}, {"0", "1"}, False)
        self.assertFalse(any(c[0] == "start" for c in self.fly.calls))

    def test_malformed_journal_and_missing_anchor_fail_closed(self):
        self.fly.state = {
            "attempts": [{"id": "2", "at": float("nan"), "outcome": "pending"}]
        }
        with self.assertRaises(ValueError):
            self.tick()
        self.controller.anchor = "unknown"
        with self.assertRaises(ValueError):
            self.tick()

    def test_target_must_be_ready_and_failed_boot_rotates(self):
        self.tick()
        self.fly.now = 1190
        self.fly.expires = 1250
        self.assertEqual(self.tick().reason, "start_failed")
        self.assertIsNone(self.fly.state["pending"])
        self.fly.now = 1205
        self.assertEqual(self.tick().target, "3")

    def test_successful_boot_clears_pending_but_keeps_cooldown(self):
        self.tick()
        self.fly.raw[2]["state"] = "started"
        self.fly.now = 1015
        self.assertEqual(self.tick(ready={"0", "1", "2"}).reason, "cooldown")
        self.assertIsNone(self.fly.state["pending"])
        self.assertEqual(self.fly.state["attempts"][-1]["outcome"], "ready")

    def test_reassigned_group_is_not_started(self):
        original = self.fly.machines()
        self.fly.raw[2]["config"]["metadata"]["fly_process_group"] = "unknown"
        samples = {str(i): Sample(0.9, 0.1, 1000) for i in range(2)}
        with self.assertRaises(ValueError):
            self.controller.tick(original, samples, {"0", "1"}, False)
        self.assertFalse(any(c[0] == "start" for c in self.fly.calls))

    def test_metrics_expiring_during_final_read_are_not_used(self):
        original_read = self.fly.machines
        calls = [0]

        def delayed():
            calls[0] += 1
            if calls[0] == 2:
                self.fly.now = 1040
            return original_read()

        raw = original_read()
        self.fly.machines = delayed
        samples = {str(i): Sample(0.9, 0.1, 911) for i in range(2)}
        with self.assertRaises(ValueError):
            self.controller.tick(raw, samples, {"0", "1"}, False)
        self.assertFalse(any(c[0] == "start" for c in self.fly.calls))

    def test_lease_expiry_after_journal_keeps_pending_without_start(self):
        original_save = self.fly.save

        def delayed(*args):
            original_save(*args)
            self.fly.now = 1045

        self.fly.save = delayed
        with self.assertRaises(ValueError):
            self.tick()
        self.assertEqual(self.fly.state["pending"], "2")
        self.assertFalse(any(c[0] == "start" for c in self.fly.calls))
