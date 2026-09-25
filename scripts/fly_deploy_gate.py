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
    # Set when main no longer points at the SHA under test. Nothing is deployed;
    # the deploy run for the newer main commit owns the deploy (newest wins).
    superseded_by: str | None = None


REQUIRED_WORKFLOWS = {
    "CI/CD Pipeline": {"backend-test"},
    "Security Gates": {
        "G3 · Semgrep (Spinr rules + public)",
        "G4a · pip-audit (Python deps)",
        "G6 · Trivy container scan",
    },
}
PRODUCTION_HEALTH_URL = "https://spinr-backend-yyz.fly.dev"
DEPLOY_WORKFLOW_FILE = "deploy-fly.yml"


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


def validate_probe_config(health_url, metrics_token):
    """Validate the production readiness and served-SHA probe settings."""
    if health_url != PRODUCTION_HEALTH_URL:
        raise GateDenied("FLY_HEALTH_URL must target the production Fly app")
    if not metrics_token or not metrics_token.strip():
        raise GateDenied("METRICS_AUTH_TOKEN is required for the served-SHA check")


def _superseded_by(expected_sha, read_main_sha):
    """Return the newer main SHA if main has moved past expected_sha, else None."""
    main_sha = read_main_sha()
    if not main_sha:
        raise GateDenied("unable to read current main")
    return None if main_sha == expected_sha else main_sha


def wait_for_deploy_evidence(*, expected_sha, repository, fetch_runs, fetch_jobs, read_main_sha, has_deploy_run, required_workflows, max_attempts=50, sleep=None):
    """Poll required Actions evidence for expected_sha while it is still main.

    main is re-read on every poll, after the evidence snapshot. Once main has
    advanced, this run never authorizes a deploy, whether its own evidence was
    pending, passed or failed (a newer push cancels queued/in-progress CI for
    this SHA). It returns ``superseded_by`` only once ``has_deploy_run`` shows
    a deploy run exists for the newer main SHA; that run is serialized after
    this one by the workflow's concurrency group and owns the deploy. If no
    such run appears (e.g. a ``[skip ci]`` push), it keeps polling and is
    finally denied, so a skipped deploy is never silently green.
    """
    sleep = sleep or time.sleep
    orphaned_main = None
    for attempt in range(max_attempts):
        runs = fetch_runs(expected_sha)
        jobs_by_run = {}
        for workflow in required_workflows:
            candidates = [run for run in runs if run.get("name") == workflow]
            if candidates:
                run = max(candidates, key=lambda item: int(item.get("id", 0)))
                if run.get("status") == "completed":
                    jobs_by_run[run.get("id")] = fetch_jobs(run.get("id"))
        try:
            result = evaluate_deploy_evidence(
                expected_sha=expected_sha,
                repository=repository,
                current_main_sha=expected_sha,
                runs=runs,
                jobs_by_run=jobs_by_run,
                required_workflows=required_workflows,
            )
        except GateDenied:
            result = None
            newer = _superseded_by(expected_sha, read_main_sha)
            if not newer:
                raise
        else:
            newer = _superseded_by(expected_sha, read_main_sha)
        orphaned_main = newer
        if newer:
            if has_deploy_run(newer):
                return GateResult(ready=False, superseded_by=newer)
        elif result.ready:
            return result
        if attempt + 1 < max_attempts:
            sleep(60)
    if orphaned_main:
        raise GateDenied(f"main advanced to {orphaned_main} but no deploy run exists for it; nothing will deploy it")
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


def _has_deploy_run(repository, sha):
    endpoint = f"repos/{repository}/actions/workflows/{DEPLOY_WORKFLOW_FILE}/runs?head_sha={sha}&branch=main&event=push&per_page=1"
    return int(_gh_json(endpoint).get("total_count") or 0) > 0


def _read_main_sha(repository):
    data = _gh_json(f"repos/{repository}/git/ref/heads/main")
    return (data.get("object") or {}).get("sha")


def _write_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        raise GateDenied("GITHUB_OUTPUT is unavailable; cannot hand the gate decision to the deploy job")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main():
    if sys.argv[1:] == ["--check-probes"]:
        try:
            validate_probe_config(os.environ.get("FLY_HEALTH_URL", ""), os.environ.get("METRICS_AUTH_TOKEN", ""))
        except GateDenied as exc:
            print(f"Fly deploy gate denied: {exc}", file=sys.stderr)
            return 1
        print("Fly production probe configuration passed.")
        return 0
    if sys.argv[1:]:
        print("Fly deploy gate denied: unsupported command.", file=sys.stderr)
        return 1
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
            has_deploy_run=lambda sha: _has_deploy_run(repository, sha),
            required_workflows=REQUIRED_WORKFLOWS,
        )
        if not result.ready and not result.superseded_by:
            raise GateDenied("gate returned neither a pass nor a supersede")
        _write_output("deploy", "true" if result.ready else "false")
    except GateDenied as exc:
        print(f"Fly deploy gate denied: {exc}", file=sys.stderr)
        return 1
    if result.superseded_by:
        print(
            f"::notice title=Fly deploy superseded::main advanced from {expected_sha} to "
            f"{result.superseded_by}; nothing deployed. The deploy run for the newer commit owns the deploy."
        )
        return 0
    print(f"Fly deploy gate passed for {repository}@{expected_sha}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
