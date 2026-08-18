"""
Create a GitHub Issue from a CI audit report.

Only creates issues for findings at or above the severity_filter threshold.

De-duplicates two ways:
  1. Same-run: if an open issue for the same run_id already exists (e.g. this
     script re-ran for the same workflow run), updates that issue in place.
  2. Cross-run (CR #4112, step 1 of the implementation plan): a fingerprint is
     computed from (workflow name, sorted failing job names, classified error
     category+signature from error_classifier.py) -- deliberately NOT the run
     ID, so the same recurring failure across many different runs collapses
     onto one issue instead of spawning a new one every run. The fingerprint
     is stored as a hidden HTML-comment marker in the issue body (the same
     "hidden marker" pattern other GitHub bots, e.g. Vercel's, use to encode
     bookkeeping data invisibly in a comment/issue body). Before creating a
     new issue, open `ci-audit`-labeled issues are searched for a matching
     marker; if found, a comment linking the new run is added to that issue
     instead of opening a duplicate.

Explicitly NOT implemented here (separate decisions per CR #4112):
  - Auto-closing issues when the underlying failure goes green again.
  - Bulk cleanup of the pre-existing issue backlog.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# Hidden marker embedded in the issue body to carry the cross-run dedup
# fingerprint. Matches the convention other GitHub bots use to stash
# bookkeeping data invisibly in a comment/issue body -- kept as a simple,
# greppable "<!-- key: value -->" HTML comment rather than an encoded blob.
FINGERPRINT_MARKER_PREFIX = "ci-audit-fingerprint"
FINGERPRINT_MARKER_RE = re.compile(
    re.escape(FINGERPRINT_MARKER_PREFIX) + r":\s*([0-9a-f]+)", re.IGNORECASE
)


def compute_fingerprint(workflow: str, errors_data: dict) -> str:
    """Hash a CI failure down to its recurring "shape".

    Deliberately keyed on (workflow name, sorted failing job names, classified
    error category+description signature) -- NOT run ID, NOT raw log excerpts,
    NOT the specific matched substring (`raw_message`) -- so the fingerprint
    stays stable across separate runs of the *same* underlying failure while
    still varying with the actual bucket `error_classifier.py` assigned it
    (category + its fixed description template), so two failures classified
    into different categories/descriptions are never conflated.
    """
    errors = errors_data.get("errors", [])
    jobs = sorted({e.get("job", "") for e in errors})
    signatures = sorted(
        {f"{e.get('job', '')}::{e.get('category', '')}::{e.get('description', '')}" for e in errors}
    )
    payload = json.dumps(
        {"workflow": workflow, "jobs": jobs, "signatures": signatures},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def fingerprint_marker(fingerprint: str) -> str:
    return f"<!-- {FINGERPRINT_MARKER_PREFIX}: {fingerprint} -->"


def extract_fingerprint(body: str) -> str | None:
    match = FINGERPRINT_MARKER_RE.search(body or "")
    return match.group(1) if match else None


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


def _search_by_fingerprint(repo: str, fingerprint: str, token: str) -> int | None:
    """Find an open ci-audit issue already carrying this fingerprint marker.

    Uses the GitHub search API (same access pattern as `_search_existing`)
    against the literal hidden-marker text, scoped to `label:ci-audit` so a
    coincidental substring match elsewhere can't false-positive.
    """
    marker_text = f"{FINGERPRINT_MARKER_PREFIX}: {fingerprint}"
    query = f'repo:{repo} is:issue is:open label:ci-audit "{marker_text}"'
    url = f"{API_BASE}/search/issues?q={urllib.parse.quote(query)}&per_page=5"
    result = _api("GET", url, token)
    for item in result.get("items", []):
        # Search can match on partial/fuzzy text; confirm the marker in the
        # actual body resolves to exactly this fingerprint before trusting it.
        if extract_fingerprint(item.get("body", "")) == fingerprint:
            return item["number"]
    return None


def _add_comment(repo: str, issue_number: int, body: str, token: str) -> None:
    _api("POST", f"{API_BASE}/repos/{repo}/issues/{issue_number}/comments", token, {"body": body})


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


def _append_fingerprint_marker(body: str, fingerprint: str) -> str:
    return f"{body}\n\n{fingerprint_marker(fingerprint)}"


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
    fingerprint = compute_fingerprint(workflow, errors_data)
    body = _append_fingerprint_marker(
        _build_issue_body(report_text, errors_data, cr_data), fingerprint
    )
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    # 1. Same-run dedup (unchanged): this script re-running for the same
    #    run_id (e.g. a retry of this workflow itself) updates in place.
    existing_number = None
    try:
        existing_number = _search_existing(repo, run_id, token)
    except Exception:
        pass  # Non-critical

    if existing_number:
        print(f"Updating existing issue #{existing_number} (same run {run_id})…")
        _api("PATCH", f"{API_BASE}/repos/{repo}/issues/{existing_number}", token, {
            "title": title,
            "body": body,
            "labels": labels,
        })
        print(f"Updated: https://github.com/{repo}/issues/{existing_number}")
        return

    # 2. Cross-run dedup (CR #4112): same failure signature, different run.
    #    Comment on the existing issue instead of opening a duplicate.
    fingerprint_match = None
    try:
        fingerprint_match = _search_by_fingerprint(repo, fingerprint, token)
    except Exception:
        pass  # Non-critical — fall through to creating a new issue

    if fingerprint_match:
        print(f"Fingerprint {fingerprint} matches open issue #{fingerprint_match} — commenting instead of creating a new issue…")
        comment_body = (
            f"Same failure signature (`{fingerprint}`) seen again on a new run.\n\n"
            f"- **Run**: {run_url}\n"
            f"- **Workflow**: {workflow}\n"
            f"- **Branch**: `{branch}`\n"
            f"- **{top_severity}, {total_errors} error(s)**\n"
        )
        _add_comment(repo, fingerprint_match, comment_body, token)
        print(f"Commented: https://github.com/{repo}/issues/{fingerprint_match}#issuecomment")
        return

    # 3. No existing issue for this run or this fingerprint — create new.
    print(f"Creating new issue for run {run_id} (fingerprint {fingerprint})…")
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
