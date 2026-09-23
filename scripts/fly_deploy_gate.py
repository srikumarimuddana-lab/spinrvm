"""Validate production deployment evidence from GitHub Actions."""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass


class GateDenied(RuntimeError):
    """The available CI evidence cannot authorize a production deployment."""


@dataclass(frozen=True)
class GateResult:
    ready: bool
    pending: tuple[str, ...] = ()


REQUIRED_WORKFLOWS = {
    "CI/CD Pipeline": {"backend-test"},
    "Security Gates": {
        "G3 · Semgrep (Spinr rules + public)",
        "G4a · pip-audit (Python deps)",
        "G6 · Trivy container scan",
    },
}


def evaluate_deploy_evidence(*, expected_sha, repository, current_main_sha, runs, jobs_by_run, required_workflows):
    """Evaluate workflow/job evidence for one exact main commit."""
    if current_main_sha != expected_sha:
        raise GateDenied("main advanced while CI was running")

    pending = []
    for workflow, required_jobs in required_workflows.items():
        candidates = [run for run in runs if run.get("name") == workflow]
        if not candidates:
            pending.append(f"missing workflow: {workflow}")
            continue
        run = max(candidates, key=lambda item: int(item.get("id", 0)))
        repo_names = {
            (run.get("repository") or {}).get("full_name"),
            (run.get("head_repository") or {}).get("full_name"),
        }
        if (
            run.get("head_sha") != expected_sha
            or run.get("head_branch") != "main"
            or run.get("event") != "push"
            or repo_names != {repository}
        ):
            raise GateDenied(f"workflow evidence does not match {repository}@{expected_sha} on main push: {workflow}")
        if run.get("status") != "completed":
            pending.append(f"workflow still running: {workflow}")
            continue
        if run.get("conclusion") != "success":
            raise GateDenied(f"workflow did not succeed: {workflow} ({run.get('conclusion')})")

        page = jobs_by_run.get(run.get("id"))
        if page is None:
            pending.append(f"job evidence not available: {workflow}")
            continue
        jobs = [job for job in page.get("jobs", []) if job.get("name") in required_jobs]
        by_name = {job.get("name"): job for job in jobs}
        missing = required_jobs - by_name.keys()
        if missing:
            raise GateDenied(f"required jobs missing or skipped in {workflow}: {sorted(missing)}")
        for name, job in by_name.items():
            if job.get("status") != "completed" or job.get("conclusion") != "success":
                raise GateDenied(f"required job did not succeed: {workflow}/{name} ({job.get('conclusion')})")

    return GateResult(ready=not pending, pending=tuple(pending))


def wait_for_deploy_evidence(*, expected_sha, repository, fetch_runs, fetch_jobs, read_main_sha, required_workflows, max_attempts=50, sleep=None):
    """Poll required Actions evidence and verify main again after it passes."""
    sleep = sleep or time.sleep
    for attempt in range(max_attempts):
        runs = fetch_runs(expected_sha)
        jobs_by_run = {}
        for workflow in required_workflows:
            candidates = [run for run in runs if run.get("name") == workflow]
            if candidates:
                run = max(candidates, key=lambda item: int(item.get("id", 0)))
                if run.get("status") == "completed":
                    jobs_by_run[run.get("id")] = fetch_jobs(run.get("id"))
        result = evaluate_deploy_evidence(
            expected_sha=expected_sha,
            repository=repository,
            current_main_sha=expected_sha,
            runs=runs,
            jobs_by_run=jobs_by_run,
            required_workflows=required_workflows,
        )
        if result.ready:
            return evaluate_deploy_evidence(
                expected_sha=expected_sha,
                repository=repository,
                current_main_sha=read_main_sha(),
                runs=runs,
                jobs_by_run=jobs_by_run,
                required_workflows=required_workflows,
            )
        if attempt + 1 < max_attempts:
            sleep(60)
    raise GateDenied("timed out waiting for required CI evidence")


def _gh_json(endpoint):
    try:
        result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        raise GateDenied("unable to retrieve GitHub Actions deployment evidence") from None


def _fetch_runs(repository, sha):
    endpoint = f"repos/{repository}/actions/runs?head_sha={sha}&branch=main&event=push&per_page=100"
    return _gh_json(endpoint).get("workflow_runs", [])


def _fetch_jobs(repository, run_id):
    return _gh_json(f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100")


def _read_main_sha(repository):
    data = _gh_json(f"repos/{repository}/git/ref/heads/main")
    return (data.get("object") or {}).get("sha")


def main():
    expected_sha = os.environ.get("GITHUB_SHA", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not expected_sha or not repository:
        print("Fly deploy gate denied: expected SHA or repository is unavailable.", file=sys.stderr)
        return 1
    try:
        result = wait_for_deploy_evidence(
            expected_sha=expected_sha,
            repository=repository,
            fetch_runs=lambda sha: _fetch_runs(repository, sha),
            fetch_jobs=lambda run_id: _fetch_jobs(repository, run_id),
            read_main_sha=lambda: _read_main_sha(repository),
            required_workflows=REQUIRED_WORKFLOWS,
        )
    except GateDenied as exc:
        print(f"Fly deploy gate denied: {exc}", file=sys.stderr)
        return 1
    print(f"Fly deploy gate passed for {repository}@{expected_sha}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
