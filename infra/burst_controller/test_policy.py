import unittest
from dataclasses import replace
from policy import Machine, Policy, Sample


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.machines = [
            Machine(
                str(i), "app" if i < 2 else "burst", "started" if i < 2 else "stopped"
            )
            for i in range(8)
        ]
        self.policy = Policy()

    def decide(
        self,
        now=1000,
        memory=0.5,
        cpu=0.2,
        state=None,
        ready=None,
        machines=None,
        samples=None,
    ):
        machines = self.machines if machines is None else machines
        samples = (
            samples
            if samples is not None
            else {
                m.id: Sample(memory, cpu, now) for m in machines if m.state == "started"
            }
        )
        return self.policy.decide(
            machines, samples, {"0", "1"} if ready is None else ready, state or {}, now
        )

    def test_normal_and_brief_spike_do_not_scale(self):
        self.assertIsNone(self.decide().target)
        self.assertIsNone(self.decide(now=1015, memory=0.75).target)
        self.assertIsNone(self.decide(now=1030).target)
        self.assertIsNone(self.decide(now=1045, memory=0.75).target)

    def test_sustained_memory_and_cpu(self):
        for signal in ({"memory": 0.75}, {"cpu": 0.85}):
            self.policy = Policy()
            self.assertIsNone(self.decide(**signal).target)
            self.assertIsNone(self.decide(now=1015, **signal).target)
            self.assertEqual(self.decide(now=1030, **signal).target, "2")

    def test_one_critical_machine_is_not_averaged_away(self):
        samples = {"0": Sample(0.9, 0.1, 1000), "1": Sample(0.1, 0.1, 1000)}
        self.assertEqual(self.decide(samples=samples).target, "2")

    def test_cap_and_ninth_machine(self):
        running = [replace(m, state="started") for m in self.machines]
        self.assertEqual(
            self.decide(machines=running, memory=0.9).reason, "capacity_exhausted"
        )
        self.assertEqual(
            self.decide(machines=running + [Machine("9", "burst", "stopped")]).reason,
            "invalid_inventory",
        )

    def test_invalid_inventory_and_transition(self):
        for field, value in (("region", "ord"), ("group", "unknown"), ("id", "1")):
            bad = [replace(self.machines[0], **{field: value})] + self.machines[1:]
            self.assertEqual(self.decide(machines=bad).reason, "invalid_inventory")
        self.assertEqual(
            self.decide(
                machines=[replace(m, state="starting") for m in self.machines]
            ).reason,
            "machine_transition",
        )

    def test_missing_stale_future_nan_and_partial_samples(self):
        for sample in (
            Sample(0.9, 0.1, 900),
            Sample(0.9, 0.1, 1001),
            Sample(float("nan"), 0.1, 1000),
            Sample(1.1, 0.1, 1000),
        ):
            self.assertEqual(
                self.decide(samples={"0": sample, "1": sample}).reason,
                "invalid_metrics",
            )
        self.assertEqual(self.decide(samples={}).reason, "missing_metrics")
        self.assertEqual(
            self.decide(samples={"0": Sample(0.9, 0.1, 1000)}).reason, "missing_metrics"
        )

    def test_repeated_sample_cannot_satisfy_sustained_threshold(self):
        self.decide(memory=0.75)
        samples = {str(i): Sample(0.75, 0.1, 1000) for i in range(2)}
        self.assertEqual(
            self.decide(now=1030, samples=samples).reason, "awaiting_fresh_sample"
        )

    def test_dependency_outage_blocks_even_critical_pressure(self):
        self.assertEqual(
            self.decide(memory=0.95, ready=set()).reason, "dependency_unready"
        )

    def test_pending_cooldown_budget_and_restart_journal(self):
        for state, reason in (
            ({"pending": "2"}, "awaiting_readiness"),
            ({"attempts": [{"id": "2", "at": 950}]}, "cooldown"),
            (
                {"attempts": [{"id": str(i), "at": 500 + i} for i in range(8)]},
                "attempt_budget",
            ),
        ):
            self.policy = Policy()
            self.assertEqual(self.decide(memory=0.9, state=state).reason, reason)
        self.policy = Policy()
        self.assertEqual(
            self.decide(
                memory=0.9, state={"attempts": [{"id": "2", "at": 500}]}
            ).target,
            "3",
        )

    def test_delayed_samples_do_not_prove_sustained_pressure(self):
        self.decide(memory=0.75)
        samples = {str(i): Sample(0.75, 0.1, 1001) for i in range(2)}
        self.assertIsNone(self.decide(now=1030, samples=samples).target)

    def test_interrupted_observation_resets_pressure(self):
        self.decide(memory=0.75)
        moving = [replace(m, state="starting") for m in self.machines]
        self.decide(now=1015, machines=moving)
        self.assertIsNone(self.decide(now=1045, memory=0.75).target)

    def test_restore_missing_warm_capacity_first(self):
        self.machines[1] = replace(self.machines[1], state="stopped")
        self.assertEqual(self.decide().target, "1")


if __name__ == "__main__":
    unittest.main()
