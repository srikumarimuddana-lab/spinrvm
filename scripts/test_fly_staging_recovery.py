"""Offline safety tests for staging Fly image recovery snapshots."""

import unittest

from fly_staging_recovery import RecoveryDenied, capture_snapshot, validate_snapshot


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = '''app = "spinr-backend-staging"\n[env]\n  ENV = "staging"\n'''


def machine(*, digest=DIGEST, group="app", region="yyz", state="started", registry="registry.fly.io", repository="spinr-backend-staging"):
    return {
        "id": "machine-123",
        "region": region,
        "state": state,
        "config": {"metadata": {"fly_process_group": group}},
        "image_ref": {
            "registry": registry,
            "repository": repository,
            "tag": "deployment-tag",
            "digest": digest,
        },
    }


def deploy_info(*, sha=SHA, provider="fly", env="staging"):
    return {"provider": provider, "env": env, "build": {"sha": sha}}


class SnapshotTests(unittest.TestCase):
    def test_captures_verified_immutable_staging_baseline(self):
        snapshot = capture_snapshot([machine()], deploy_info(), CONFIG)
        self.assertEqual(snapshot["app"], "spinr-backend-staging")
        self.assertEqual(snapshot["served_sha"], SHA)
        self.assertEqual(snapshot["config_commit_sha"], SHA)
        self.assertEqual(snapshot["image_ref"], f"registry.fly.io/spinr-backend-staging@{DIGEST}")
        self.assertEqual(snapshot["config_toml_sha256"], validate_snapshot(snapshot, CONFIG))
        self.assertNotIn("config_toml", snapshot)

    def test_empty_staging_app_is_bootstrap_without_rollback_baseline(self):
        self.assertIsNone(capture_snapshot([], deploy_info(), CONFIG))

    def test_rejects_unknown_app_shape_or_untrusted_machine_inventory(self):
        bad = [
            [machine(region="ord")],
            [machine(group="burst")],
            [machine(state="starting")],
            [machine(digest="")],
            [machine(registry="evil.example")],
            [machine(repository="spinr-backend-yyz")],
            [machine(), machine()],
        ]
        for inventory in bad:
            with self.subTest(inventory=inventory), self.assertRaises(RecoveryDenied):
                capture_snapshot(inventory, deploy_info(), CONFIG)

    def test_rejects_wrong_served_build_and_config(self):
        invalid = [
            (deploy_info(provider="railway"), CONFIG),
            (deploy_info(env="production"), CONFIG),
            (deploy_info(sha="unknown"), CONFIG),
            (deploy_info(), CONFIG.replace("spinr-backend-staging", "spinr-backend-yyz")),
            (deploy_info(), CONFIG.replace('[env]\n  ENV = "staging"', '[other]\n  ENV = "staging"\n[env]\n  ENV = "production"')),
            (deploy_info(), CONFIG + '[env]\n  ENV = "production"\n'),
            (deploy_info(), 'app = "spinr-backend-staging"\n[env\n'),
        ]
        for info, config in invalid:
            with self.subTest(info=info), self.assertRaises(RecoveryDenied):
                capture_snapshot([machine()], info, config)

    def test_snapshot_detects_config_or_reference_tampering(self):
        snapshot = capture_snapshot([machine()], deploy_info(), CONFIG)
        with self.assertRaises(RecoveryDenied):
            validate_snapshot(snapshot, CONFIG + "# changed\n")
        snapshot["image_ref"] = "registry.fly.io/spinr-backend-staging:latest"
        with self.assertRaises(RecoveryDenied):
            validate_snapshot(snapshot, CONFIG)


if __name__ == "__main__":
    unittest.main()
