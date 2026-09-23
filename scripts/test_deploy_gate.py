"""Fail-closed checks for production Fly deployment evidence."""

import unittest
from pathlib import Path

from fly_deploy_gate import GateDenied, evaluate_deploy_evidence, validate_probe_config, wait_for_deploy_evidence


REPO = "acme/spinrvm"
SHA = "a" * 40
WORKFLOWS = {
    "CI/CD Pipeline": {"backend-test"},
    "Security Gates": {
        "G3 · Semgrep (Spinr rules + public)",
        "G4a · pip-audit (Python deps)",
        "G6 · Trivy container scan",
    },
}


def run(name, *, sha=SHA, repo=REPO, branch="main", event="push", status="completed", conclusion="success", run_id=1):
    return {
        "id": run_id,
        "name": name,
        "head_sha": sha,
        "head_branch": branch,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "repository": {"full_name": repo},
        "head_repository": {"full_name": repo},
    }


def successful_jobs(names):
    return {
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in names
        ]
    }


def evidence(runs=None, jobs=None, *, current_main_sha=SHA):
    return evaluate_deploy_evidence(
        expected_sha=SHA,
        repository=REPO,
        current_main_sha=current_main_sha,
        runs=runs if runs is not None else [run(name, run_id=i) for i, name in enumerate(WORKFLOWS, 1)],
        jobs_by_run=jobs
        if jobs is not None
        else {i: successful_jobs(names) for i, names in enumerate(WORKFLOWS.values(), 1)},
        required_workflows=WORKFLOWS,
    )


class DeployEvidenceTests(unittest.TestCase):
    def test_exact_main_push_with_executed_required_jobs_is_accepted(self):
        self.assertTrue(evidence().ready)

    def test_missing_workflow_is_pending_not_authorized(self):
        result = evidence(runs=[run("CI/CD Pipeline", run_id=1)])
        self.assertFalse(result.ready)

    def test_running_workflow_is_pending(self):
        result = evidence(runs=[run(name, run_id=i, status="in_progress", conclusion=None) for i, name in enumerate(WORKFLOWS, 1)])
        self.assertFalse(result.ready)

    def test_failed_workflow_is_denied(self):
        runs = [run("CI/CD Pipeline", run_id=1), run("Security Gates", run_id=2, conclusion="failure")]
        with self.assertRaises(GateDenied):
            evidence(runs=runs)

    def test_non_main_or_non_push_run_is_denied(self):
        for kwargs in ({"branch": "feature/x"}, {"event": "pull_request"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(GateDenied):
                    evidence(runs=[run("CI/CD Pipeline", run_id=1), run("Security Gates", run_id=2, **kwargs)])

    def test_foreign_repository_or_different_sha_is_denied(self):
        for kwargs in ({"repo": "fork/spinrvm"}, {"sha": "b" * 40}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(GateDenied):
                    evidence(runs=[run("CI/CD Pipeline", run_id=1), run("Security Gates", run_id=2, **kwargs)])

    def test_skipped_or_missing_required_job_is_denied_after_workflow_completes(self):
        jobs = {
            1: successful_jobs(WORKFLOWS["CI/CD Pipeline"]),
            2: {"jobs": [{"name": name, "status": "completed", "conclusion": "skipped"} for name in WORKFLOWS["Security Gates"]]},
        }
        with self.assertRaises(GateDenied):
            evidence(jobs=jobs)
        jobs[2] = {"jobs": []}
        with self.assertRaises(GateDenied):
            evidence(jobs=jobs)

    def test_stale_main_tip_is_denied_after_wait(self):
        with self.assertRaises(GateDenied):
            evidence(current_main_sha="c" * 40)

    def test_wait_retries_missing_evidence_and_reads_main_after_gates_pass(self):
        runs_by_poll = [
            [run("CI/CD Pipeline", run_id=1)],
            [run(name, run_id=i) for i, name in enumerate(WORKFLOWS, 1)],
        ]
        poll_count = 0
        sleeps = []
        main_reads = []

        def fetch_runs(_sha):
            nonlocal poll_count
            result = runs_by_poll[poll_count]
            poll_count += 1
            return result

        def fetch_jobs(run_id):
            names = list(WORKFLOWS.values())[run_id - 1]
            return successful_jobs(names)

        ready = wait_for_deploy_evidence(
            expected_sha=SHA,
            repository=REPO,
            fetch_runs=fetch_runs,
            fetch_jobs=fetch_jobs,
            read_main_sha=lambda: main_reads.append(True) or SHA,
            required_workflows=WORKFLOWS,
            max_attempts=2,
            sleep=sleeps.append,
        )
        self.assertTrue(ready.ready)
        self.assertEqual(sleeps, [60])
        self.assertEqual(main_reads, [True])

    def test_wait_timeout_denies_missing_evidence_without_reading_main(self):
        main_reads = []
        with self.assertRaises(GateDenied):
            wait_for_deploy_evidence(
                expected_sha=SHA,
                repository=REPO,
                fetch_runs=lambda _sha: [],
                fetch_jobs=lambda _run_id: {},
                read_main_sha=lambda: main_reads.append(True) or SHA,
                required_workflows=WORKFLOWS,
                max_attempts=2,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(main_reads, [])

    def test_probe_config_requires_exact_production_url_and_metrics_token(self):
        for url, token in (
            ("", "metrics-token"),
            ("https://other.example", "metrics-token"),
            ("https://spinr-backend-yyz.fly.dev", " "),
        ):
            with self.subTest(url=url, token=bool(token.strip())):
                with self.assertRaises(GateDenied):
                    validate_probe_config(url, token)
        validate_probe_config("https://spinr-backend-yyz.fly.dev", "metrics-token")


class DeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-fly.yml"
        cls.source = workflow.read_text()

    def test_production_deploy_is_push_main_only_and_has_actions_read(self):
        self.assertIn("  push:\n    branches:\n      - main", self.source)
        self.assertNotIn("  workflow_dispatch:", self.source)
        self.assertNotIn("    paths:", self.source)
        self.assertIn("  actions: read", self.source)

    def test_exact_sha_gate_runs_before_secrets_or_deploy(self):
        gate = self.source.index("python3 scripts/fly_deploy_gate.py")
        self.assertLess(gate, self.source.index("Stage Sentry DSN in Fly secrets"))
        self.assertLess(gate, self.source.index("run: flyctl deploy"))
        self.assertIn("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", self.source)

    def test_production_readiness_and_served_sha_probes_are_required(self):
        self.assertIn("Verify production probe configuration", self.source)
        self.assertIn("python3 scripts/fly_deploy_gate.py --check-probes", self.source)
        self.assertIn("FLY_HEALTH_URL", self.source)
        self.assertIn("METRICS_AUTH_TOKEN", self.source)
        for step in ("Readiness check", "Verify the deployed build SHA is serving"):
            start = self.source.index(f"- name: {step}")
            next_step = self.source.find("- name:", start + 1)
            block = self.source[start : next_step if next_step >= 0 else len(self.source)]
            self.assertNotIn("if:", block)


if __name__ == "__main__":
    unittest.main()
