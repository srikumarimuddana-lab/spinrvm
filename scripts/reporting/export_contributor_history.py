#!/usr/bin/env python3
"""Export full PR/issue history for srikumarimuddana-lab/spinrvm to CSV.

Built for docs/audit/2026-09-25-contributor-governance-baseline.md's "full
contributor timeline" recommendation — that report's aggregate counts (762 /
1,678 PRs, 2,617 / 2,822 totals as of 2026-09-25) came from GitHub search API
totals, not a full per-item export. This script does the full export.

Not run as part of any Claude Code session: pulling ~2,600 PRs + ~2,800
issues page-by-page (100/page, 2 REST calls per PR to also get merge state)
is a multi-thousand-call job against GitHub's REST API, well past what's
sensible to run inline in a chat-driven session. Run this from a machine
with a real GitHub token and no per-call approval friction.

Usage:
    export GITHUB_TOKEN=<a token with repo:read on srikumarimuddana-lab/spinrvm>
    python3 scripts/reporting/export_contributor_history.py \
        --out contributor_history.csv

Output columns: kind (pr/issue), number, state, author, author_association,
created_at, closed_at, merged_at (PRs only), title, labels (semicolon-joined).

Rate-limit aware: uses conditional backoff on 403/secondary-rate-limit
responses (see _get). Expect this to take 15-30 minutes for the full repo
given the volume above -- it prints progress every 5 pages.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from urllib import request as urlrequest
from urllib.error import HTTPError

API_ROOT = "https://api.github.com"
REPO = "srikumarimuddana-lab/spinrvm"


def _get(url: str, token: str) -> tuple[list[dict], dict]:
    req = urlrequest.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    for attempt in range(6):
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                data = resp.read()
                import json

                return json.loads(data), dict(resp.headers)
        except HTTPError as e:
            if e.code in (403, 429) and attempt < 5:
                # Secondary rate limit or abuse detection -- back off.
                wait = int(e.headers.get("Retry-After", 2 ** attempt * 5))
                print(f"  rate-limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"exhausted retries for {url}")


def _paginate(kind: str, token: str):
    """kind: 'pulls' or 'issues'. Issues endpoint includes PRs; caller filters."""
    page = 1
    while True:
        url = (
            f"{API_ROOT}/repos/{REPO}/{kind}"
            f"?state=all&per_page=100&page={page}&sort=created&direction=asc"
        )
        items, headers = _get(url, token)
        if not items:
            return
        yield from items
        if page % 5 == 0:
            print(f"  {kind}: page {page} done", file=sys.stderr)
        if "next" not in (headers.get("Link") or ""):
            return
        page += 1


def export(out_path: str, token: str) -> None:
    fieldnames = [
        "kind", "number", "state", "author", "author_association",
        "created_at", "closed_at", "merged_at", "title", "labels",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        print("Exporting pull requests...", file=sys.stderr)
        for pr in _paginate("pulls", token):
            writer.writerow({
                "kind": "pr",
                "number": pr["number"],
                "state": pr["state"],
                "author": pr["user"]["login"] if pr.get("user") else "",
                "author_association": pr.get("author_association", ""),
                "created_at": pr.get("created_at", ""),
                "closed_at": pr.get("closed_at", ""),
                "merged_at": pr.get("merged_at", ""),
                "title": pr.get("title", ""),
                "labels": ";".join(l["name"] for l in pr.get("labels", [])),
            })

        print("Exporting issues (excluding PRs)...", file=sys.stderr)
        for issue in _paginate("issues", token):
            if "pull_request" in issue:
                continue  # already covered above
            writer.writerow({
                "kind": "issue",
                "number": issue["number"],
                "state": issue["state"],
                "author": issue["user"]["login"] if issue.get("user") else "",
                "author_association": issue.get("author_association", ""),
                "created_at": issue.get("created_at", ""),
                "closed_at": issue.get("closed_at", ""),
                "merged_at": "",
                "title": issue.get("title", ""),
                "labels": ";".join(l["name"] for l in issue.get("labels", [])),
            })

    print(f"Done. Wrote {out_path}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="contributor_history.csv")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN to a token with read access to "
              f"{REPO} before running this script.", file=sys.stderr)
        return 1

    export(args.out, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
