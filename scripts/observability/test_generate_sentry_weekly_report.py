"""
Unit tests for scripts/observability/generate_sentry_weekly_report.py.

Runs directly (mirrors scripts/security/'s existing test convention — this
lives outside backend/pytest.ini's testpaths):

    pytest scripts/observability/test_generate_sentry_weekly_report.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_sentry_weekly_report import (  # noqa: E402
    _load_json_safe,
    render_report,
)


def test_load_json_safe_missing_path_returns_none(tmp_path):
    assert _load_json_safe(str(tmp_path / "does-not-exist.json")) is None


def test_load_json_safe_none_path_returns_none():
    assert _load_json_safe(None) is None


def test_load_json_safe_malformed_json_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert _load_json_safe(str(bad)) is None


def test_render_report_missing_data_says_no_data_not_clean_week():
    report = render_report(None, "2026-09-14", "2026-09-21")
    assert "No findings data available" in report
    assert "distinct from a clean week" in report.lower()


def test_render_report_empty_issues_says_clean_week():
    data = {"org": "spinr-backend", "project": "crimson-smoke-7445", "connector_ok": True, "issues": []}
    report = render_report(data, "2026-09-14", "2026-09-21")
    assert "No unresolved issues found this window" in report
    assert "clean week" in report.lower()


def test_render_report_connector_not_authorized_flags_it_not_a_clean_week():
    data = {"org": "spinr-backend", "project": "crimson-smoke-7445", "connector_ok": False, "issues": []}
    report = render_report(data, "2026-09-14", "2026-09-21")
    assert "not authorized" in report.lower()
    assert "reflects no live scan, not a clean week" in report.lower()


def test_render_report_renders_issue_fields_without_raw_pii_passthrough():
    data = {
        "org": "spinr-backend",
        "project": "crimson-smoke-7445",
        "connector_ok": True,
        "issues": [
            {
                "short_id": "SPINR-BACKEND-42",
                "title": "KeyError in fare_service.calculate_fare",
                "category": "new",
                "first_seen": "2026-09-18",
                "last_seen": "2026-09-20",
                "event_count": 12,
                "affected_users": 4,
                "domain": "payments",
                "surface": "backend",
                "root_cause": "surge_multiplier missing when service area has no auto-mode row",
                "recommended_fix": "default to 1.0 with an explicit log line",
                "confidence": "high",
                "action_taken": "fixed_pr",
                "pr_url": "https://github.com/srikumarimuddana-lab/spinrvm/pull/9999",
                "guardrail_added": "regression test + fare_service input validation",
                "known_item_ref": None,
            }
        ],
        "blockers": [],
        "carry_forward": ["C2: Sentry alert rule for refresh-token reuse still not created."],
    }
    report = render_report(data, "2026-09-14", "2026-09-21")
    assert "SPINR-BACKEND-42" in report
    assert "KeyError in fare_service.calculate_fare" in report
    assert "domain=`payments`" in report
    assert "surface=`backend`" in report
    assert "Fix PR opened" in report
    assert "https://github.com/srikumarimuddana-lab/spinrvm/pull/9999" in report
    assert "C2: Sentry alert rule" in report
    # No field in the schema carries raw payload/stacktrace text, so the
    # renderer has nothing PII-shaped to accidentally leak -- this test
    # documents that invariant rather than trying to detect PII patterns.
    assert "stacktrace" not in report.lower()
    assert "breadcrumb" not in report.lower()


def test_render_report_groups_issues_by_category():
    data = {
        "issues": [
            {"short_id": "A", "title": "a", "category": "new"},
            {"short_id": "B", "title": "b", "category": "regressed"},
            {"short_id": "C", "title": "c", "category": "long_tail"},
        ],
    }
    report = render_report(data, "2026-09-14", "2026-09-21")
    assert report.index("New this window") < report.index("`A`")
    assert report.index("Regressed") < report.index("`B`")
    assert report.index("Long-tail") < report.index("`C`")


def test_main_writes_output_file(tmp_path):
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"org": "spinr-backend", "project": "crimson-smoke-7445", "issues": []}))
    output = tmp_path / "report.md"

    import subprocess

    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "generate_sentry_weekly_report.py"),
            "--findings", str(findings),
            "--window-start", "2026-09-14",
            "--window-end", "2026-09-21",
            "--output", str(output),
        ],
        check=True,
    )
    assert output.exists()
    assert "clean week" in output.read_text().lower()
