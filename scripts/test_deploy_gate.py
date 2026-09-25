"""Fail-closed checks for production Fly deployment evidence."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fly_deploy_gate
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


def all_success_runs():
    return [run(name, run_id=i) for i, name in enumerate(WORKFLOWS, 1)]


def jobs_for(run_id):
    return successful_jobs(list(WORKFLOWS.values())[run_id - 1])


def wait(fetch_runs, read_main_sha, *, has_deploy_run=lambda _sha: True, max_attempts=3, sleeps=None):
    return wait_for_deploy_evidence(
        expected_sha=SHA,
        repository=REPO,
        fetch_runs=fetch_runs,
        fetch_jobs=jobs_for,
        read_main_sha=read_main_sha,
        has_deploy_run=has_deploy_run,
        required_workflows=WORKFLOWS,
        max_attempts=max_attempts,
        sleep=(sleeps.append if sleeps is not None else (lambda _seconds: None)),
    )


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

    def test_wait_retries_missing_evidence_and_reads_main_every_poll(self):
        runs_by_poll = [
            [run("CI/CD Pipeline", run_id=1)],
            all_success_runs(),
        ]
        poll_count = 0
        sleeps = []
        main_reads = []

        def fetch_runs(_sha):
            nonlocal poll_count
            result = runs_by_poll[poll_count]
            poll_count += 1
            return result

        ready = wait(fetch_runs, lambda: main_reads.append(True) or SHA, max_attempts=2, sleeps=sleeps)
        self.assertTrue(ready.ready)
        self.assertIsNone(ready.superseded_by)
        self.assertEqual(sleeps, [60])
        # Once per poll, each read taken after that poll's evidence snapshot,
        # so the final read still happens after the gates passed.
        self.assertEqual(main_reads, [True, True])

    def test_wait_timeout_denies_missing_evidence_while_still_main(self):
        with self.assertRaises(GateDenied):
            wait(lambda _sha: [], lambda: SHA, max_attempts=2)

    def test_superseded_while_pending_skips_without_waiting_for_ci(self):
        sleeps = []
        pending = [run(name, run_id=i, status="in_progress", conclusion=None) for i, name in enumerate(WORKFLOWS, 1)]
        result = wait(lambda _sha: pending, lambda: "c" * 40, sleeps=sleeps)
        self.assertFalse(result.ready)
        self.assertEqual(result.superseded_by, "c" * 40)
        self.assertEqual(sleeps, [])

    def test_superseded_after_gates_pass_never_authorizes_deploy(self):
        result = wait(lambda _sha: all_success_runs(), lambda: "c" * 40)
        self.assertFalse(result.ready)
        self.assertEqual(result.superseded_by, "c" * 40)

    def test_cancelled_ci_on_superseded_sha_skips_instead_of_denying(self):
        runs = [run("CI/CD Pipeline", run_id=1, conclusion="cancelled"), run("Security Gates", run_id=2)]
        result = wait(lambda _sha: runs, lambda: "c" * 40)
        self.assertFalse(result.ready)
        self.assertEqual(result.superseded_by, "c" * 40)

    def test_failed_or_cancelled_ci_on_current_main_is_still_denied(self):
        for conclusion in ("failure", "cancelled"):
            with self.subTest(conclusion=conclusion):
                runs = [run("CI/CD Pipeline", run_id=1, conclusion=conclusion), run("Security Gates", run_id=2)]
                with self.assertRaises(GateDenied):
                    wait(lambda _sha: runs, lambda: SHA)

    def test_superseded_without_successor_deploy_run_is_denied_not_skipped(self):
        # e.g. the newer main commit was pushed with [skip ci], so no deploy
        # run will ever carry it: this must go red, never silently green.
        sleeps = []
        with self.assertRaisesRegex(GateDenied, "no deploy run exists"):
            wait(lambda _sha: all_success_runs(), lambda: "c" * 40, has_deploy_run=lambda _sha: False, sleeps=sleeps)
        self.assertEqual(sleeps, [60, 60])

    def test_superseded_skips_once_successor_deploy_run_appears(self):
        checks = []

        def has_deploy_run(sha):
            checks.append(sha)
            return len(checks) > 1  # run for the newer push is created a poll later

        result = wait(lambda _sha: all_success_runs(), lambda: "c" * 40, has_deploy_run=has_deploy_run)
        self.assertFalse(result.ready)
        self.assertEqual(result.superseded_by, "c" * 40)
        self.assertEqual(checks, ["c" * 40, "c" * 40])

    def test_transient_successor_lookup_error_is_retried_not_denied(self):
        calls = []

        def has_deploy_run(sha):
            calls.append(sha)
            if len(calls) == 1:
                raise GateDenied("unable to retrieve GitHub Actions deployment evidence")
            return True

        sleeps = []
        result = wait(lambda _sha: all_success_runs(), lambda: "c" * 40, has_deploy_run=has_deploy_run, sleeps=sleeps)
        self.assertFalse(result.ready)
        self.assertEqual(result.superseded_by, "c" * 40)
        self.assertEqual(sleeps, [60])

    def test_unreadable_main_is_denied_not_treated_as_superseded(self):
        for main_sha in (None, ""):
            with self.subTest(main_sha=main_sha):
                with self.assertRaises(GateDenied):
                    wait(lambda _sha: all_success_runs(), lambda: main_sha)

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


class GateEntrypointTests(unittest.TestCase):
    def run_main(self, *, main_sha, runs=None, with_output=True):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            env = {"GITHUB_SHA": SHA, "GITHUB_REPOSITORY": REPO}
            if with_output:
                env["GITHUB_OUTPUT"] = str(output)
            fetch = runs if runs is not None else all_success_runs()
            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fly_deploy_gate.sys, "argv", ["fly_deploy_gate.py"]), \
                mock.patch.object(fly_deploy_gate, "_fetch_runs", lambda _repo, _sha: fetch), \
                mock.patch.object(fly_deploy_gate, "_fetch_jobs", lambda _repo, run_id: jobs_for(run_id)), \
                mock.patch.object(fly_deploy_gate, "_read_main_sha", lambda _repo: main_sha), \
                mock.patch.object(fly_deploy_gate, "_has_deploy_run", lambda _repo, _sha: True), \
                mock.patch.object(fly_deploy_gate.time, "sleep", lambda _seconds: None), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                code = fly_deploy_gate.main()
            return code, (output.read_text() if output.exists() else "")

    def test_pass_on_current_main_outputs_deploy_true(self):
        self.assertEqual(self.run_main(main_sha=SHA), (0, "deploy=true\n"))

    def test_superseded_exits_clean_with_deploy_false(self):
        self.assertEqual(self.run_main(main_sha="c" * 40), (0, "deploy=false\n"))

    def test_denied_exits_nonzero_without_deploy_output(self):
        runs = [run("CI/CD Pipeline", run_id=1, conclusion="failure"), run("Security Gates", run_id=2)]
        self.assertEqual(self.run_main(main_sha=SHA, runs=runs), (1, ""))

    def test_missing_github_output_is_denied_not_silently_skipped(self):
        code, _ = self.run_main(main_sha=SHA, with_output=False)
        self.assertEqual(code, 1)


class CheckStillMainTests(unittest.TestCase):
    def test_first_attempt_is_allowed_without_reading_main(self):
        def boom():
            raise AssertionError("main must not be read on attempt 1")

        fly_deploy_gate.check_still_main(expected_sha=SHA, run_attempt="1", read_main_sha=boom)

    def test_rerun_on_current_main_is_allowed(self):
        fly_deploy_gate.check_still_main(expected_sha=SHA, run_attempt="2", read_main_sha=lambda: SHA)

    def test_rerun_after_main_advanced_is_denied(self):
        with self.assertRaisesRegex(GateDenied, "re-run the whole workflow"):
            fly_deploy_gate.check_still_main(expected_sha=SHA, run_attempt="2", read_main_sha=lambda: "c" * 40)

    def test_rerun_with_unreadable_main_is_denied(self):
        with self.assertRaises(GateDenied):
            fly_deploy_gate.check_still_main(expected_sha=SHA, run_attempt="3", read_main_sha=lambda: None)

    def test_entrypoint_exit_codes(self):
        for attempt, main_sha, expected in (("1", "c" * 40, 0), ("2", SHA, 0), ("2", "c" * 40, 1)):
            env = {"GITHUB_SHA": SHA, "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ATTEMPT": attempt}
            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fly_deploy_gate.sys, "argv", ["fly_deploy_gate.py", "--check-still-main"]), \
                mock.patch.object(fly_deploy_gate, "_read_main_sha", lambda _repo, sha=main_sha: sha), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                self.assertEqual(fly_deploy_gate.main(), expected, (attempt, main_sha))


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

    def test_successor_lookup_targets_this_workflow_file(self):
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        self.assertEqual((workflows / fly_deploy_gate.DEPLOY_WORKFLOW_FILE).read_text(), self.source)

    def test_deploy_job_runs_only_on_explicit_gate_pass(self):
        self.assertIn("      deploy: ${{ steps.gate.outputs.deploy }}", self.source)
        self.assertIn("        id: gate\n        run: python3 scripts/fly_deploy_gate.py\n", self.source)
        gate_start = self.source.index("\n  gate:\n")
        deploy_start = self.source.index("\n  deploy:\n")
        self.assertLess(gate_start, deploy_start)
        self.assertIn("    needs: gate\n    if: needs.gate.outputs.deploy == 'true'\n", self.source[deploy_start:])
        # The polling gate job holds only GITHUB_TOKEN; Fly/probe secrets stay in the deploy job.
        gate_job = self.source[gate_start:deploy_start]
        for secret in ("FLY_API_TOKEN", "SENTRY_DSN", "METRICS_AUTH_TOKEN", "FLY_HEALTH_URL"):
            self.assertNotIn(secret, gate_job)

    def test_deploy_job_rechecks_main_before_anything_else_on_rerun(self):
        deploy_job = self.source[self.source.index("\n  deploy:\n"):]
        guard = deploy_job.index("python3 scripts/fly_deploy_gate.py --check-still-main")
        self.assertLess(guard, deploy_job.index("--check-probes"))
        self.assertLess(guard, deploy_job.index("flyctl deploy"))

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
