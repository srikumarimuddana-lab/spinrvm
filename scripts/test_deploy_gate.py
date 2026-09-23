"""Fail-closed checks for production Fly deployment evidence."""

import unittest

from fly_deploy_gate import GateDenied, evaluate_deploy_evidence


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


if __name__ == "__main__":
    unittest.main()
