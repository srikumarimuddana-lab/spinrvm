"""Isolated tests: python -m unittest discover -s scripts -p test_fly_fleet_preflight.py."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from fly_fleet_preflight import validate


def fleet(app=2, burst=6):
    return [
        {
            "id": f"{group}-{i}",
            "region": "yyz",
            "state": "stopped",
            "config": {"metadata": {"fly_process_group": group}},
        }
        for group, count in (("app", app), ("burst", burst))
        for i in range(count)
    ]


class FleetPreflightTests(unittest.TestCase):
    def test_current_layout_requires_migration(self):
        with self.assertRaisesRegex(ValueError, "migration"):
            validate(fleet(8, 0))

    def test_mixed_fleet_and_smaller_repairable_pool(self):
        for machines in (fleet(), fleet(2, 3), fleet(1, 0), []):
            with self.subTest(count=len(machines)):
                validate(machines)

    def test_rejects_excess_unknown_group_region_and_duplicates(self):
        bad_group, bad_region, duplicate = fleet(), fleet(), fleet()
        bad_group[0]["config"]["metadata"]["fly_process_group"] = "other"
        bad_region[0]["region"] = "ord"
        duplicate[1]["id"] = duplicate[0]["id"]
        for machines in (fleet(3, 5), fleet(2, 7), bad_group, bad_region, duplicate):
            with self.subTest(machines=machines):
                with self.assertRaises(ValueError):
                    validate(machines)

    def test_rejects_missing_metadata_instead_of_assuming_app(self):
        machines = fleet()
        del machines[0]["config"]["metadata"]
        with self.assertRaises(ValueError):
            validate(machines)

    def test_cli_rejects_malformed_input_without_echoing_secrets(self):
        script = Path(__file__).with_name("fly_fleet_preflight.py")
        for payload in (
            "secret-not-json",
            json.dumps({"credential": "secret"}),
            "[null]",
        ):
            result = subprocess.run(
                [sys.executable, str(script)],
                input=payload,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("secret", result.stdout + result.stderr)
            self.assertIn("preflight", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
