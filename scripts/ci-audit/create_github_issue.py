"""
Create a GitHub Issue from a CI audit report.

Only creates issues for findings at or above the severity_filter threshold.
De-duplicates: if an open issue for the same run already exists, updates it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

SEVERITY_LABELS = {
    "P0": "severity: P0 - critical",
    "P1": "severity: P1 - high",
    "P2": "severity: P2 - medium",
    "P3": "severity: P3 - low",
}

CATEGORY_LABELS = {
    "test":       "category: test",
    "coverage":   "category: coverage",
    "lint":       "category: lint",
    "security":   "category: security",
    "build":      "category: build",
    "deploy":     "category: deploy",
    "dependency": "category: dependency",
    "infra":      "category: infra",
}


def _api(method: str, url: str, token: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"GitHub API error {e.code}: {e.read().decode()}", file=sys.stderr)
        return {}


def _search_existing(repo: str, run_id: str, token: str) -> int | None:
    query = f"repo:{repo} is:issue is:open \"CI Error Audit\" \"{run_id}\" in:title"
    url = f"{API_BASE}/search/issues?q={urllib.parse.quote(query)}&per_page=1"
    result = _api("GET", url, token)
    items = result.get("items", [])
    return items[0]["number"] if items else None


def _build_issue_body(report_text: str, errors_data: dict, change_requests: dict) -> str:
    cr_list = change_requests.get("change_requests", [])
    cr_section = ""
    if cr_list:
        cr_lines = "\n".join(
            f"- **{cr['cr_id']}**: {cr['title']} ({cr['priority']})"
            for cr in cr_list
        )
        cr_section = f"\n## Proposed Change Requests\n{cr_lines}\n\n> File each CR via: `.github/ISSUE_TEMPLATE/ci_change_request.yml`\n"

    # Truncate report to fit GitHub issue limit (65k chars)
    truncated = report_text[:30_000]
    if len(report_text) > 30_000:
        truncated += "\n\n_[Report truncated — see full report in `reports/audits/`]_"

    return f"{truncated}{cr_section}"


def create_or_update_issue(
    report_path: str,
    errors_path: str,
    cr_path: str,
    repo: str,
    run_id: str,
    workflow: str,
    branch: str,
    token: str,
    severity_filter: str,
) -> None:
    report_text  = Path(report_path).read_text()
    errors_data  = json.loads(Path(errors_path).read_text())
    cr_data      = json.loads(Path(cr_path).read_text())

    top_severity = errors_data.get("top_severity", "P3")
    total_errors = errors_data.get("total_errors", 0)

    # Skip if below filter threshold
    if SEVERITY_ORDER.get(top_severity, 3) > SEVERITY_ORDER.get(severity_filter, 2):
        print(f"Top severity {top_severity} is below filter {severity_filter}. Skipping issue creation.")
        return

    # Collect labels
    categories = list({e["category"] for e in errors_data.get("errors", [])})
    labels = (
        ["ci-failure", "ci-audit", "needs-triage"]
        + [SEVERITY_LABELS[top_severity]]
        + [CATEGORY_LABELS[c] for c in categories if c in CATEGORY_LABELS]
    )

    title = f"[CI Audit] {workflow} — {top_severity} — {total_errors} error(s) on `{branch}` (run {run_id})"
    body  = _build_issue_body(report_text, errors_data, cr_data)

    existing_number = None
    try:
        existing_number = _search_existing(repo, run_id, token)
    except Exception:
        pass  # Non-critical

    if existing_number:
        print(f"Updating existing issue #{existing_number}…")
        _api("PATCH", f"{API_BASE}/repos/{repo}/issues/{existing_number}", token, {
            "title": title,
            "body": body,
            "labels": labels,
        })
        print(f"Updated: https://github.com/{repo}/issues/{existing_number}")
    else:
        print(f"Creating new issue for run {run_id}…")
        result = _api("POST", f"{API_BASE}/repos/{repo}/issues", token, {
            "title": title,
            "body": body,
            "labels": labels,
        })
        issue_number = result.get("number")
        if issue_number:
            print(f"Created: https://github.com/{repo}/issues/{issue_number}")
        else:
            print("Issue creation may have failed — check GitHub API response.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create GitHub issue from CI audit")
    parser.add_argument("--report",           required=True)
    parser.add_argument("--errors",           required=True)
    parser.add_argument("--change-requests",  required=True)
    parser.add_argument("--repo",             required=True)
    parser.add_argument("--run-id",           required=True)
    parser.add_argument("--workflow",         required=True)
    parser.add_argument("--branch",           required=True)
    parser.add_argument("--token",            default="", help="GitHub token (falls back to GH_TOKEN env var)")
    parser.add_argument("--severity-filter",  default="P1")
    args = parser.parse_args()

    token = args.token or os.environ.get("GH_TOKEN", "")
    if not token:
        print("ERROR: GitHub token required via --token or GH_TOKEN env var", file=sys.stderr)
        sys.exit(1)

    create_or_update_issue(
        args.report, args.errors, args.change_requests,
        args.repo, args.run_id, args.workflow,
        args.branch, token, args.severity_filter,
    )


if __name__ == "__main__":
    main()
