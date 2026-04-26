"""
Fetch GitHub Actions run data (jobs + step logs) via the REST API.

Requires: GH_TOKEN env var (or --token flag) with actions:read permission.
Output: run_data.json suitable for error_classifier.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
MAX_LOG_CHARS = 10_000  # Keep last N chars of each job log (errors usually tail)


def _get(url: str, token: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", "60"))
                print(f"Rate limited. Waiting {retry_after}s…", file=sys.stderr)
                time.sleep(retry_after)
                continue
            if e.code == 404:
                return {}
            raise
    return {}


def _get_text(url: str, token: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.raw",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return text[-MAX_LOG_CHARS:]  # Errors are usually at the end
    except (urllib.error.HTTPError, urllib.error.URLError):
        return ""


def _parse_next_link(link_header: str) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None


def _get_all_jobs(run_id: str, repo: str, token: str) -> list[dict]:
    """Fetch all jobs for a run, following pagination (GitHub max 30/page by default)."""
    url = f"{API_BASE}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    all_jobs: list[dict] = []

    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                all_jobs.extend(data.get("jobs", []))
                url = _parse_next_link(resp.headers.get("Link", ""))
        except (urllib.error.HTTPError, urllib.error.URLError):
            break

    return all_jobs


def _detect_flaky_jobs(
    repo: str, workflow_id: int, failing_job_names: list[str], token: str
) -> set[str]:
    """
    Return job names that passed in at least one of the 5 most recent completed
    runs of the same workflow. These are candidates for flaky-test investigation
    rather than hard regressions.
    """
    if not failing_job_names or not workflow_id:
        return set()

    url = (
        f"{API_BASE}/repos/{repo}/actions/workflows/{workflow_id}/runs"
        f"?per_page=10&status=completed&exclude_pull_requests=true"
    )
    data = _get(url, token)
    recent_runs = data.get("workflow_runs", [])[:5]

    names_to_check = set(failing_job_names)
    passed_in_recent: set[str] = set()

    for run in recent_runs:
        run_id = run.get("id")
        if not run_id:
            continue
        jobs_data = _get(
            f"{API_BASE}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
            token,
        )
        for job in jobs_data.get("jobs", []):
            name = job.get("name", "")
            if name in names_to_check and job.get("conclusion") == "success":
                passed_in_recent.add(name)

    return passed_in_recent


def fetch(run_id: str, repo: str, token: str) -> dict[str, Any]:
    # Fetch run metadata
    run_data = _get(f"{API_BASE}/repos/{repo}/actions/runs/{run_id}", token)
    workflow_id: int = run_data.get("workflow_id", 0)

    # Fetch all jobs with pagination (C3+H7 fix)
    jobs = _get_all_jobs(run_id, repo, token)

    # Identify failing jobs for flaky detection
    failing_job_names = [
        j.get("name", "") for j in jobs if j.get("conclusion") == "failure"
    ]
    flaky_candidates = _detect_flaky_jobs(repo, workflow_id, failing_job_names, token)

    enriched_jobs = []
    for job in jobs:
        job_id     = job.get("id")
        job_name   = job.get("name", "unknown")
        conclusion = job.get("conclusion", "")

        # Fetch job log ONCE per failed job (not once per failed step — C3 fix)
        job_log = ""
        if conclusion in ("failure",) and job_id:
            log_url = f"{API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs"
            job_log = _get_text(log_url, token)

        steps: list[dict[str, Any]] = [
            {
                "name":       step.get("name", ""),
                "conclusion": step.get("conclusion", ""),
                "number":     step.get("number", 0),
            }
            for step in job.get("steps", [])
        ]

        enriched_jobs.append({
            "id":                job_id,
            "name":              job_name,
            "conclusion":        conclusion,
            "html_url":          job.get("html_url", ""),
            "log":               job_log,         # Job-level log (single fetch)
            "steps":             steps,
            "is_flaky_candidate": job_name in flaky_candidates,
        })

    return {
        "run_id":         run_id,
        "workflow_name":  run_data.get("name", ""),
        "workflow_id":    workflow_id,
        "status":         run_data.get("status", ""),
        "conclusion":     run_data.get("conclusion", ""),
        "head_branch":    run_data.get("head_branch", ""),
        "head_sha":       run_data.get("head_sha", ""),
        "html_url":       run_data.get("html_url", ""),
        "created_at":     run_data.get("created_at", ""),
        "updated_at":     run_data.get("updated_at", ""),
        "jobs":           enriched_jobs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch GitHub Actions run data")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo",   required=True, help="owner/repo")
    parser.add_argument("--token",  default="", help="GitHub token (falls back to GH_TOKEN env var)")
    parser.add_argument("--output", required=True, help="Path to write run_data.json")
    args = parser.parse_args()

    token = args.token or os.environ.get("GH_TOKEN", "")
    if not token:
        print("ERROR: GitHub token required via --token or GH_TOKEN env var", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching run {args.run_id} from {args.repo}…")
    data = fetch(args.run_id, args.repo, token)
    Path(args.output).write_text(json.dumps(data, indent=2))

    failed = [j["name"] for j in data.get("jobs", []) if j.get("conclusion") == "failure"]
    flaky  = [j["name"] for j in data.get("jobs", []) if j.get("is_flaky_candidate")]
    print(f"Fetched {len(data.get('jobs', []))} job(s). Failed: {failed or 'none'}")
    if flaky:
        print(f"Flaky candidates (passed in recent history): {flaky}")


if __name__ == "__main__":
    main()
