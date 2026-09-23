"""Validate production deployment evidence from GitHub Actions."""

from dataclasses import dataclass


class GateDenied(RuntimeError):
    """The available CI evidence cannot authorize a production deployment."""


@dataclass(frozen=True)
class GateResult:
    ready: bool
    pending: tuple[str, ...] = ()


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
