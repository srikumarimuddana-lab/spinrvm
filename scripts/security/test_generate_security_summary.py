"""
Unit tests for scripts/security/generate_security_summary.py (Phase 2 of the
2026-09-19 security automation roadmap).

Runs directly (mirrors scripts/security/'s existing test convention — this
lives outside backend/pytest.ini's testpaths):

    pytest scripts/security/test_generate_security_summary.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_security_summary import (  # noqa: E402
    _load_json_safe,
    generate,
    summarize_bandit,
    summarize_npm_audit,
    summarize_semgrep,
)


def test_load_json_safe_missing_path_returns_none(tmp_path):
    assert _load_json_safe(str(tmp_path / "does-not-exist.json")) is None


def test_load_json_safe_none_path_returns_none():
    assert _load_json_safe(None) is None


def test_load_json_safe_malformed_json_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert _load_json_safe(str(bad)) is None


def test_load_json_safe_valid_json_returns_dict(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"results": []}))
    assert _load_json_safe(str(good)) == {"results": []}


def test_summarize_bandit_counts_by_severity():
    data = {
        "results": [
            {"issue_severity": "HIGH", "filename": "backend/routes/rides.py", "line_number": 10, "test_id": "B101", "issue_text": "x"},
            {"issue_severity": "high", "filename": "backend/routes/fares.py", "line_number": 20, "test_id": "B102", "issue_text": "y"},
            {"issue_severity": "MEDIUM", "filename": "backend/utils/x.py", "line_number": 5, "test_id": "B103", "issue_text": "z"},
        ]
    }
    summary = summarize_bandit(data)
    assert summary["total"] == 3
    assert summary["by_severity"] == {"HIGH": 2, "MEDIUM": 1}


def test_summarize_bandit_never_includes_free_text_issue_text():
    """Bandit's hardcoded-secret checks (B105/B106/B107) can echo the
    literal matched string value in issue_text -- that must never survive
    into a summary that gets committed permanently to docs/audit/."""
    data = {"results": [{"issue_severity": "HIGH", "filename": "a.py", "line_number": 1, "test_id": "B105", "issue_text": "hardcoded credential detected near this line: hunter2fakevalue"}]}
    summary = summarize_bandit(data)
    assert "text" not in summary["findings"][0]
    assert "hunter2fakevalue" not in json.dumps(summary)


def test_summarize_bandit_none_input_returns_none():
    assert summarize_bandit(None) is None


def test_summarize_bandit_empty_results():
    summary = summarize_bandit({"results": []})
    assert summary["total"] == 0
    assert summary["by_severity"] == {}


def test_summarize_semgrep_counts_by_severity():
    data = {
        "results": [
            {"check_id": "spinr-no-float-in-money", "path": "backend/services/fare_service.py", "start": {"line": 42}, "extra": {"severity": "ERROR", "message": "float in money code"}},
            {"check_id": "spinr-pii-in-logs", "path": "backend/routes/drivers.py", "start": {"line": 7}, "extra": {"severity": "WARNING", "message": "pii"}},
        ]
    }
    summary = summarize_semgrep(data)
    assert summary["total"] == 2
    assert summary["by_severity"] == {"ERROR": 1, "WARNING": 1}


def test_summarize_semgrep_never_includes_free_text_message():
    """semgrep's p/secrets pack can place a matched secret snippet in
    extra.message -- same rationale as the bandit test above."""
    data = {"results": [{"check_id": "generic-secrets-key", "path": "a.py", "start": {"line": 1}, "extra": {"severity": "ERROR", "message": "found API key: sk-supersecret123"}}]}
    summary = summarize_semgrep(data)
    assert "text" not in summary["findings"][0]
    assert "sk-supersecret123" not in json.dumps(summary)


def test_summarize_semgrep_none_input_returns_none():
    assert summarize_semgrep(None) is None


def test_summarize_npm_audit_counts_by_severity():
    data = {
        "vulnerabilities": {
            "image-size": {"severity": "high"},
            "some-other-pkg": {"severity": "critical"},
        }
    }
    summary = summarize_npm_audit(data)
    assert summary["total"] == 2
    assert summary["by_severity"] == {"HIGH": 1, "CRITICAL": 1}


def test_summarize_npm_audit_none_input_returns_none():
    assert summarize_npm_audit(None) is None


def test_generate_handles_all_missing_data_without_crashing():
    report = generate(None, None, None, "org/repo", "123", "main", "abc123def456")
    assert "Automated Security Scan Report" in report
    assert "No data available for this run" in report
    # Missing data must never be reported as a clean scan.
    assert report.count("No data available for this run") == 3


def test_generate_reports_findings_when_present():
    bandit_data = {"results": [{"issue_severity": "HIGH", "filename": "a.py", "line_number": 1, "test_id": "B101", "issue_text": "x"}]}
    report = generate(bandit_data, None, None, "org/repo", "123", "main", "abc123def456")
    assert "Total findings: 1" in report
    assert "| HIGH | 1 |" in report


def test_generate_never_leaks_finding_free_text_into_report():
    bandit_data = {"results": [{"issue_severity": "HIGH", "filename": "a.py", "line_number": 1, "test_id": "B105", "issue_text": "hardcoded credential detected near this line: hunter2fakevalue"}]}
    semgrep_data = {"results": [{"check_id": "generic-secrets-key", "path": "b.py", "start": {"line": 2}, "extra": {"severity": "ERROR", "message": "found API key: sk-supersecret123"}}]}
    report = generate(bandit_data, semgrep_data, None, "org/repo", "123", "main", "abc123def456")
    assert "hunter2fakevalue" not in report
    assert "sk-supersecret123" not in report
    # Rule id + location must still be present -- redaction shouldn't
    # gut the report's actual triage value.
    assert "B105" in report
    assert "generic-secrets-key" in report


def test_generate_includes_run_metadata():
    report = generate(None, None, None, "org/repo", "42", "main", "deadbeefcafe123456")
    assert "org/repo" in report
    assert "https://github.com/org/repo/actions/runs/42" in report
    assert "`main`" in report
    assert "`deadbeefcafe`" in report  # commit truncated to first 12 chars
