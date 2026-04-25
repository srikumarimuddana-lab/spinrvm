"""
Fetch GitHub Actions run data (jobs + step logs) via the REST API.

Requires: GITHUB_TOKEN with actions:read permission.
Output: run_data.json suitable for error_classifier.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
MAX_LOG_CHARS = 10_000  # Truncate to avoid enormous JSON files


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
            return text[-MAX_LOG_CHARS:]  # Keep last N chars (where errors usually are)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return ""


def fetch(run_id: str, repo: str, token: str) -> dict[str, Any]:
    owner, name = repo.split("/", 1)

    # Fetch run metadata
    run_data = _get(f"{API_BASE}/repos/{repo}/actions/runs/{run_id}", token)

    # Fetch jobs for this run
    jobs_data = _get(f"{API_BASE}/repos/{repo}/actions/runs/{run_id}/jobs", token)
    jobs = jobs_data.get("jobs", [])

    enriched_jobs = []
    for job in jobs:
        job_id   = job.get("id")
        job_name = job.get("name", "unknown")
        conclusion = job.get("conclusion", "")

        steps: list[dict[str, Any]] = []
        for step in job.get("steps", []):
            step_entry: dict[str, Any] = {
                "name":       step.get("name", ""),
                "conclusion": step.get("conclusion", ""),
                "number":     step.get("number", 0),
                "log":        "",
            }

            # Only fetch logs for failed steps (saves API quota)
            if step.get("conclusion") == "failure" and job_id:
                log_url = (
                    f"{API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs"
                )
                step_entry["log"] = _get_text(log_url, token)

            steps.append(step_entry)

        enriched_jobs.append({
            "id":         job_id,
            "name":       job_name,
            "conclusion": conclusion,
            "html_url":   job.get("html_url", ""),
            "steps":      steps,
        })

    return {
        "run_id":         run_id,
        "workflow_name":  run_data.get("name", ""),
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
    parser.add_argument("--token",  required=True, help="GitHub token")
    parser.add_argument("--output", required=True, help="Path to write run_data.json")
    args = parser.parse_args()

    print(f"Fetching run {args.run_id} from {args.repo}…")
    data = fetch(args.run_id, args.repo, args.token)
    Path(args.output).write_text(json.dumps(data, indent=2))

    failed = [j["name"] for j in data.get("jobs", []) if j.get("conclusion") == "failure"]
    print(f"Fetched {len(data.get('jobs', []))} job(s). Failed: {failed or 'none'}")


if __name__ == "__main__":
    main()
