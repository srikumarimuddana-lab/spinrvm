"""
Auto-close recovered `[CI Audit]` issues (CR #4612; #4112 implementation
plan, step 2).

For every open issue carrying both the `ci-audit` and `ci-failure` labels,
parse the (workflow, branch) it was filed for out of the title the
report/issue pipeline generates:

    [CI Audit] <workflow> — P<n> — <k> error(s) on `<branch>` (run <id>)

and close it when the underlying signal has demonstrably recovered:

  1. RECOVERED — the workflow's most recent `--min-green` completed runs on
     that branch all concluded `success`, and the newest of them started
     after the issue was created. Closed as `completed`.
  2. BRANCH GONE — the branch no longer exists (ephemeral claude/* or
     dependabot/* branch was merged/deleted), so the failure can never
     recur or be re-verified. Closed as `not_planned`.

Anything else (still red, not enough green history yet, unparseable title,
unknown workflow name) is left open and reported. Every close posts a
comment stating exactly why — per #4112's "always leave a closing comment"
mitigation. One flaky green does not close an issue: `--min-green`
(default 3) consecutive successes are required.

Run by `.github/workflows/ci-audit-autoclose.yml` on a daily schedule;
supports `--dry-run` for a no-write preview.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"

TITLE_RE = re.compile(
    r"^\[CI Audit\] (?P<workflow>.+?) — P\d — .+? on `(?P<branch>.+?)` \(run \d+\)$"
)


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
        if e.code == 404:
            return None
        print(f"GitHub API error {e.code} for {url}: {e.read().decode()}", file=sys.stderr)
        return {}


def list_open_audit_issues(repo: str, token: str) -> list[dict]:
    issues: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode(
            {"labels": "ci-audit,ci-failure", "state": "open", "per_page": 100, "page": page}
        )
        batch = _api("GET", f"{API_BASE}/repos/{repo}/issues?{q}", token) or []
        # The issues endpoint also returns PRs; filter them out.
        issues.extend(i for i in batch if "pull_request" not in i)
        if len(batch) < 100:
            return issues
        page += 1


def workflow_ids_by_name(repo: str, token: str) -> dict[str, int]:
    """Map workflow display name -> workflow id (paginated)."""
    out: dict[str, int] = {}
    page = 1
    while True:
        q = urllib.parse.urlencode({"per_page": 100, "page": page})
        data = _api("GET", f"{API_BASE}/repos/{repo}/actions/workflows?{q}", token) or {}
        workflows = data.get("workflows", [])
        for wf in workflows:
            out[wf["name"]] = wf["id"]
        if len(workflows) < 100:
            return out
        page += 1


def branch_exists(repo: str, branch: str, token: str) -> bool:
    return _api(
        "GET", f"{API_BASE}/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}", token
    ) is not None


def recent_runs(repo: str, workflow_id: int, branch: str, count: int, token: str) -> list[dict]:
    q = urllib.parse.urlencode(
        {"branch": branch, "status": "completed", "per_page": count}
    )
    data = _api(
        "GET", f"{API_BASE}/repos/{repo}/actions/workflows/{workflow_id}/runs?{q}", token
    ) or {}
    return data.get("workflow_runs", [])


def decide(issue: dict, runs: list[dict] | None, branch_alive: bool, min_green: int) -> tuple[str, str]:
    """Return (verdict, reason). Verdicts: 'recovered' | 'branch_gone' | 'keep'."""
    if not branch_alive:
        return (
            "branch_gone",
            "the branch this failure was recorded on no longer exists, so the "
            "failure cannot recur or be re-verified",
        )
    if runs is None or len(runs) < min_green:
        return ("keep", f"fewer than {min_green} completed runs on the branch since filing")
    latest = runs[:min_green]
    if any(r.get("conclusion") != "success" for r in latest):
        return ("keep", f"a run within the last {min_green} completed runs is not green")
    if latest[0].get("created_at", "") <= issue.get("created_at", ""):
        return ("keep", "no completed run is newer than the issue itself")
    return (
        "recovered",
        f"the last {min_green} completed runs of this workflow on the branch "
        "all succeeded, the newest after this issue was filed",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--min-green", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN not set", file=sys.stderr)
        return 1

    issues = list_open_audit_issues(args.repo, token)
    wf_ids = workflow_ids_by_name(args.repo, token)
    print(f"Open ci-audit issues: {len(issues)}; known workflows: {len(wf_ids)}")

    branch_cache: dict[str, bool] = {}
    runs_cache: dict[tuple[int, str], list[dict]] = {}
    closed = kept = skipped = 0

    for issue in issues:
        number = issue["number"]
        match = TITLE_RE.match(issue.get("title", ""))
        if not match:
            print(f"#{number}: SKIP (title not in [CI Audit] format)")
            skipped += 1
            continue
        workflow, branch = match.group("workflow"), match.group("branch")
        wf_id = wf_ids.get(workflow)
        if wf_id is None:
            print(f"#{number}: SKIP (unknown workflow {workflow!r})")
            skipped += 1
            continue

        if branch not in branch_cache:
            branch_cache[branch] = branch_exists(args.repo, branch, token)
        runs = None
        if branch_cache[branch]:
            key = (wf_id, branch)
            if key not in runs_cache:
                runs_cache[key] = recent_runs(args.repo, wf_id, branch, args.min_green, token)
            runs = runs_cache[key]

        verdict, reason = decide(issue, runs, branch_cache[branch], args.min_green)
        if verdict == "keep":
            print(f"#{number}: KEEP ({reason})")
            kept += 1
            continue

        state_reason = "completed" if verdict == "recovered" else "not_planned"
        print(f"#{number}: CLOSE as {state_reason} ({reason})")
        closed += 1
        if args.dry_run:
            continue
        _api("POST", f"{API_BASE}/repos/{args.repo}/issues/{number}/comments", token, {
            "body": (
                f"Auto-closing: {reason}. (ci-audit-autoclose, CR #4612 / #4112 step 2 — "
                "reopen if this failure recurs.)"
            ),
        })
        _api("PATCH", f"{API_BASE}/repos/{args.repo}/issues/{number}", token, {
            "state": "closed",
            "state_reason": state_reason,
        })

    print(f"Done: closed={closed} kept={kept} skipped={skipped} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
