"""
Unit tests for scripts/ci-audit/auto_close_recovered.py (CR #4612;
#4112 implementation plan, step 2).

Like test_create_github_issue.py, this sits next to the module it tests
(scripts/ci-audit/ is outside backend/pytest.ini's testpaths) and runs
directly:

    pytest scripts/ci-audit/test_auto_close_recovered.py -v

Covers the two pure decision surfaces — the title parser and the
recovered/branch-gone/keep verdict — with no network involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auto_close_recovered as acr  # noqa: E402


# ─── Title parsing ───────────────────────────────────────────────────────────

def test_title_parses_workflow_and_branch():
    m = acr.TITLE_RE.match(
        "[CI Audit] CI/CD Pipeline — P1 — 2 error(s) on `main` (run 32544110372)"
    )
    assert m is not None
    assert m.group("workflow") == "CI/CD Pipeline"
    assert m.group("branch") == "main"


def test_title_parses_branch_with_slashes():
    m = acr.TITLE_RE.match(
        "[CI Audit] CI/CD Pipeline — P1 — 1 error(s) on "
        "`dependabot/npm_and_yarn/rider-app/expo-stack-6fabaa9418` (run 32846099562)"
    )
    assert m is not None
    assert m.group("branch") == "dependabot/npm_and_yarn/rider-app/expo-stack-6fabaa9418"


def test_non_audit_title_does_not_match():
    assert acr.TITLE_RE.match("[CR] some change request about `main` (run 123)") is None
    assert acr.TITLE_RE.match("P0: Real driver SIN/bank PII committed to git") is None


# ─── Verdict logic ───────────────────────────────────────────────────────────

ISSUE = {"number": 1, "created_at": "2026-08-20T00:00:00Z"}


def _run(conclusion: str, created_at: str) -> dict:
    return {"conclusion": conclusion, "created_at": created_at}


def test_branch_gone_closes_as_not_planned_verdict():
    verdict, _ = acr.decide(ISSUE, None, branch_alive=False, min_green=3)
    assert verdict == "branch_gone"


def test_three_greens_after_issue_recovers():
    runs = [
        _run("success", "2026-08-23T00:00:00Z"),
        _run("success", "2026-08-22T00:00:00Z"),
        _run("success", "2026-08-21T00:00:00Z"),
    ]
    verdict, _ = acr.decide(ISSUE, runs, branch_alive=True, min_green=3)
    assert verdict == "recovered"


def test_single_flaky_green_is_not_enough():
    runs = [_run("success", "2026-08-23T00:00:00Z")]
    verdict, _ = acr.decide(ISSUE, runs, branch_alive=True, min_green=3)
    assert verdict == "keep"


def test_one_red_within_window_keeps_open():
    runs = [
        _run("success", "2026-08-23T00:00:00Z"),
        _run("failure", "2026-08-22T00:00:00Z"),
        _run("success", "2026-08-21T00:00:00Z"),
    ]
    verdict, _ = acr.decide(ISSUE, runs, branch_alive=True, min_green=3)
    assert verdict == "keep"


def test_greens_that_predate_the_issue_keep_open():
    runs = [
        _run("success", "2026-08-19T00:00:00Z"),
        _run("success", "2026-08-18T00:00:00Z"),
        _run("success", "2026-08-17T00:00:00Z"),
    ]
    verdict, _ = acr.decide(ISSUE, runs, branch_alive=True, min_green=3)
    assert verdict == "keep"


def test_no_runs_on_live_branch_keeps_open():
    verdict, _ = acr.decide(ISSUE, [], branch_alive=True, min_green=3)
    assert verdict == "keep"
