"""
Unit tests for scripts/ci-audit/create_github_issue.py's cross-run
fingerprint dedup (CR #4112, implementation step 1).

No existing test location for scripts/ci-audit/ existed at the time this was
added (backend/pytest.ini scopes `testpaths = tests` to backend/tests only,
and this module lives outside backend/ entirely), so this file sits next to
the module it tests and is run directly:

    pytest scripts/ci-audit/test_create_github_issue.py -v

All GitHub API I/O is faked in-process (no network) by monkeypatching the
single `_api` call boundary with a tiny stateful fake that mimics just enough
of the real GitHub REST + Search API surface (issue create/update/comment,
`search/issues`) for `create_or_update_issue` to exercise its real dedup
logic end to end.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import create_github_issue as cgi  # noqa: E402


# ─── Fake GitHub backend ─────────────────────────────────────────────────────

class FakeGitHub:
    """In-memory stand-in for the slice of the GitHub API this script calls."""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list[str]] = {}
        self._next_id = 1000

    def api(self, method: str, url: str, token: str, body: dict | None = None):
        parsed = urllib.parse.urlparse(url)
        path = parsed.path

        if method == "GET" and path == "/search/issues":
            query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            return {"items": self._search(query)}

        if method == "POST" and path.endswith("/issues"):
            self._next_id += 1
            number = self._next_id
            self.issues[number] = {
                "number": number,
                "title": body["title"],
                "body": body["body"],
                "labels": body["labels"],
                "state": "open",
            }
            self.comments[number] = []
            return {"number": number}

        if method == "PATCH" and "/issues/" in path and not path.endswith("/comments"):
            number = int(path.rsplit("/", 1)[-1])
            self.issues[number].update(
                {k: v for k, v in body.items() if k in ("title", "body", "labels")}
            )
            return self.issues[number]

        if method == "POST" and path.endswith("/comments"):
            number = int(path.split("/issues/")[1].split("/comments")[0])
            self.comments[number].append(body["body"])
            return {"id": len(self.comments[number])}

        raise AssertionError(f"Unhandled fake API call: {method} {url}")

    def _search(self, query: str) -> list[dict]:
        # Only open issues are ever searched for by this script.
        if "is:open" not in query:
            return []
        results = []
        for issue in self.issues.values():
            if issue["state"] != "open":
                continue
            # Every quoted phrase in the query must appear in title or body.
            phrases = [p for p in query.split('"')[1::2]]
            haystack = f"{issue['title']}\n{issue['body']}"
            if all(phrase in haystack for phrase in phrases):
                if "label:ci-audit" in query and "ci-audit" not in issue["labels"]:
                    continue
                results.append(issue)
        return results


@pytest.fixture
def fake_github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(cgi, "_api", fake.api)
    return fake


# ─── Test fixtures on disk ────────────────────────────────────────────────────

def _write_audit_bundle(tmp_path: Path, *, job: str, category: str, description: str) -> dict[str, str]:
    report_path = tmp_path / "audit_report.md"
    report_path.write_text("# CI Error Audit\nSomething failed.\n")

    errors_path = tmp_path / "classified_errors.json"
    errors_path.write_text(json.dumps({
        "total_errors": 1,
        "top_severity": "P1",
        "severity_counts": {"P0": 0, "P1": 1, "P2": 0, "P3": 0},
        "summary": "1 error(s): 1×P1",
        "errors": [{
            "job": job,
            "step": "job log",
            "category": category,
            "severity": "P1",
            "description": description,
            "log_excerpt": "FAILED tests/test_whatever.py::test_thing",
            "surface": "backend",
            "patterns_matched": [r"FAILED\s+tests/"],
            "raw_message": "FAILED tests/test_whatever.py",
        }],
    }))

    cr_path = tmp_path / "change_requests.json"
    cr_path.write_text(json.dumps({"change_requests": []}))

    return {
        "report": str(report_path),
        "errors": str(errors_path),
        "cr": str(cr_path),
    }


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_same_fingerprint_different_run_ids_comments_instead_of_creating(tmp_path, fake_github):
    """Two different run IDs, identical failure signature -> one issue, one comment."""
    paths = _write_audit_bundle(
        tmp_path, job="backend-test", category="test", description="pytest test failure"
    )

    cgi.create_or_update_issue(
        paths["report"], paths["errors"], paths["cr"],
        repo="acme/spinrvm", run_id="1111", workflow="CI/CD Pipeline",
        branch="main", token="fake-token", severity_filter="P1",
    )
    cgi.create_or_update_issue(
        paths["report"], paths["errors"], paths["cr"],
        repo="acme/spinrvm", run_id="2222", workflow="CI/CD Pipeline",
        branch="main", token="fake-token", severity_filter="P1",
    )

    assert len(fake_github.issues) == 1, "second run must not create a second issue"
    (issue_number, issue), = fake_github.issues.items()
    assert cgi.extract_fingerprint(issue["body"]) is not None
    assert len(fake_github.comments[issue_number]) == 1
    assert "2222" in fake_github.comments[issue_number][0]


def test_different_fingerprint_creates_a_new_issue(tmp_path, fake_github):
    """Different failure signature -> a distinct new issue, not folded into the first."""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    paths_a = _write_audit_bundle(
        dir_a, job="backend-test", category="test", description="pytest test failure"
    )
    paths_b = _write_audit_bundle(
        dir_b, job="rider-app-test", category="lint", description="ESLint errors"
    )

    cgi.create_or_update_issue(
        paths_a["report"], paths_a["errors"], paths_a["cr"],
        repo="acme/spinrvm", run_id="3333", workflow="CI/CD Pipeline",
        branch="main", token="fake-token", severity_filter="P1",
    )
    cgi.create_or_update_issue(
        paths_b["report"], paths_b["errors"], paths_b["cr"],
        repo="acme/spinrvm", run_id="4444", workflow="CI/CD Pipeline",
        branch="main", token="fake-token", severity_filter="P1",
    )

    assert len(fake_github.issues) == 2, "distinct failure signatures must get distinct issues"
    fingerprints = {cgi.extract_fingerprint(i["body"]) for i in fake_github.issues.values()}
    assert len(fingerprints) == 2
    assert all(fake_github.comments[n] == [] for n in fake_github.issues)


def test_fingerprint_excludes_run_id_but_includes_workflow_job_category():
    errors_data = {
        "errors": [
            {"job": "backend-test", "category": "test", "description": "pytest test failure"},
            {"job": "backend-test", "category": "test", "description": "pytest test failure"},  # dup
        ]
    }
    fp1 = cgi.compute_fingerprint("CI/CD Pipeline", errors_data)
    fp2 = cgi.compute_fingerprint("CI/CD Pipeline", errors_data)
    assert fp1 == fp2, "fingerprint must be deterministic for identical inputs"

    other_workflow = cgi.compute_fingerprint("Deploy Backend", errors_data)
    assert other_workflow != fp1, "different workflow name must change the fingerprint"

    other_job = cgi.compute_fingerprint("CI/CD Pipeline", {
        "errors": [{"job": "rider-app-test", "category": "test", "description": "pytest test failure"}]
    })
    assert other_job != fp1, "different job name must change the fingerprint"


def test_fingerprint_marker_roundtrip():
    marker = cgi.fingerprint_marker("abc123")
    assert marker == "<!-- ci-audit-fingerprint: abc123 -->"
    assert cgi.extract_fingerprint(f"some issue body\n\n{marker}") == "abc123"
    assert cgi.extract_fingerprint("no marker here") is None
