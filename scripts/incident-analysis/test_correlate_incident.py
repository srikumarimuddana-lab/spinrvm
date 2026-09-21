"""
Unit tests for scripts/incident-analysis/correlate_incident.py (Phase 3 of
the 2026-09-19 security automation roadmap), using mocked Sentry/log/audit
data -- the Sentry MCP connector is not authorized for this session, so
these fixtures stand in for real search_events/search_issues output until
it is (see the module docstring's "Wiring in real Sentry data" section).

Runs directly (mirrors scripts/security/'s test convention — this lives
outside backend/pytest.ini's testpaths):

    pytest scripts/incident-analysis/test_correlate_incident.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from correlate_incident import (  # noqa: E402
    _load_json_safe,
    _parse_ts,
    _redact_free_text,
    _root_cause_hint,
    correlate,
    render_markdown,
)


def _event(request_id, level="error", title="Something broke", domain="payments", timestamp="2026-09-19T10:00:00Z"):
    return {
        "id": f"evt-{request_id}",
        "title": title,
        "level": level,
        "timestamp": timestamp,
        "tags": {"domain": domain, "surface": "backend", "env": "production", "request_id": request_id},
    }


def _log(request_id, level="info", message="ok", module="routes.rides", timestamp="2026-09-19T09:59:59Z"):
    return {"request_id": request_id, "level": level, "message": message, "module": module, "timestamp": timestamp}


def _audit(request_id, action="update", entity_type="rides", entity_id="ride_1", actor_id="admin_1", created_at="2026-09-19T10:00:01Z"):
    return {"request_id": request_id, "action": action, "entity_type": entity_type, "entity_id": entity_id, "actor_id": actor_id, "created_at": created_at}


def test_load_json_safe_missing_path_returns_empty_list(tmp_path):
    assert _load_json_safe(str(tmp_path / "nope.json")) == []


def test_load_json_safe_none_path_returns_empty_list():
    assert _load_json_safe(None) == []


def test_load_json_safe_malformed_returns_empty_list(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _load_json_safe(str(bad)) == []


def test_load_json_safe_non_list_json_returns_empty_list(tmp_path):
    obj = tmp_path / "obj.json"
    obj.write_text(json.dumps({"not": "a list"}))
    assert _load_json_safe(str(obj)) == []


def test_load_json_safe_valid_list_returned(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"a": 1}]))
    assert _load_json_safe(str(good)) == [{"a": 1}]


def test_parse_ts_handles_z_suffix():
    dt = _parse_ts("2026-09-19T10:00:00Z")
    assert dt is not None
    assert dt.year == 2026


def test_parse_ts_invalid_returns_none():
    assert _parse_ts("not a timestamp") is None
    assert _parse_ts(None) is None


def test_correlate_groups_by_request_id():
    events = [_event("req-1"), _event("req-2", level="warning")]
    logs = [_log("req-1", level="error", message="fare calc failed"), _log("req-2")]
    audit = [_audit("req-1")]

    clusters = correlate(events, logs, audit)

    assert len(clusters) == 2
    by_id = {c["request_id"]: c for c in clusters}
    assert len(by_id["req-1"]["log_lines"]) == 1
    assert len(by_id["req-1"]["audit_rows"]) == 1
    assert len(by_id["req-2"]["audit_rows"]) == 0


def test_correlate_ignores_events_without_request_id():
    events = [{"id": "e1", "title": "no tags", "level": "error", "timestamp": "2026-09-19T10:00:00Z", "tags": {}}]
    clusters = correlate(events, [], [])
    assert clusters == []


def test_correlate_sorts_by_severity_then_event_count():
    events = [
        _event("req-warn", level="warning"),
        _event("req-error-1", level="error"),
        _event("req-error-2", level="error"),
        _event("req-error-2b", level="error"),
    ]
    # req-error-2 gets a second event -> should rank first (same severity, more events)
    events.append(_event("req-error-2", level="error", title="second event"))

    clusters = correlate(events, [], [])
    assert clusters[0]["request_id"] == "req-error-2"
    assert clusters[0]["top_level"] == "error"
    # the lone warning cluster must rank last
    assert clusters[-1]["request_id"] == "req-warn"


def test_correlate_ranks_fatal_above_error():
    # Regression test: LEVEL_RANK previously had no "fatal" entry, so a
    # fatal-level event fell back to rank 0 (tied with "debug") instead of
    # sorting above "error" -- exactly backwards for the daily
    # severity-scoped triage mode, which targets error/fatal first.
    events = [
        _event("req-error", level="error"),
        _event("req-fatal", level="fatal"),
    ]
    clusters = correlate(events, [], [])
    assert clusters[0]["request_id"] == "req-fatal"
    assert clusters[0]["top_level"] == "fatal"


def test_correlate_captures_domain_tags():
    events = [_event("req-1", domain="dispatch")]
    clusters = correlate(events, [], [])
    assert clusters[0]["domains"] == ["dispatch"]


def test_root_cause_hint_errors_and_audit():
    cluster = {"log_lines": [{"level": "error"}], "audit_rows": [{"action": "update"}]}
    hint = _root_cause_hint(cluster)
    assert "1 ERROR log line" in hint
    assert "1 audit action" in hint


def test_root_cause_hint_errors_only():
    cluster = {"log_lines": [{"level": "error"}, {"level": "error"}], "audit_rows": []}
    hint = _root_cause_hint(cluster)
    assert "2 ERROR log line" in hint
    assert "no audit trail" in hint


def test_root_cause_hint_audit_only():
    cluster = {"log_lines": [], "audit_rows": [{"action": "update"}]}
    hint = _root_cause_hint(cluster)
    assert "1 audit action" in hint
    assert "no ERROR-level" in hint


def test_root_cause_hint_neither():
    cluster = {"log_lines": [], "audit_rows": []}
    hint = _root_cause_hint(cluster)
    assert "No correlated log lines" in hint


def test_render_markdown_no_clusters():
    report = render_markdown([], "2026-09-19 00:00-06:00 UTC")
    assert "Incident Correlation Report" in report
    assert "No Sentry events with a request_id tag" in report


def test_render_markdown_includes_cluster_detail():
    events = [_event("req-1", level="error", title="Payment settlement failed", domain="payments")]
    logs = [_log("req-1", level="error", message="stripe timeout")]
    audit = [_audit("req-1")]
    clusters = correlate(events, logs, audit)
    report = render_markdown(clusters, "test window")

    assert "req-1" in report
    assert "Payment settlement failed" in report
    assert "stripe timeout" in report
    assert "payments" in report
    assert "Root-cause hint" in report


def test_redact_free_text_strips_email():
    assert "rider@example.com" not in _redact_free_text("failed to notify rider@example.com")
    assert "[redacted-email]" in _redact_free_text("failed to notify rider@example.com")


def test_redact_free_text_strips_phone():
    redacted = _redact_free_text("call driver at 306-555-0199 for pickup")
    assert "306-555-0199" not in redacted
    assert "[redacted-phone]" in redacted


def test_redact_free_text_strips_coordinates():
    redacted = _redact_free_text("pickup at 52.1332,-106.6700 failed geocoding")
    assert "52.1332" not in redacted
    assert "[redacted-coordinates]" in redacted


def test_redact_free_text_strips_low_precision_coordinates():
    redacted = _redact_free_text("lat 52.13,-106.67 approx")
    assert "52.13" not in redacted
    assert "[redacted-coordinates]" in redacted


def test_redact_free_text_strips_sin_shaped_number():
    redacted = _redact_free_text("driver SIN 123 456 789 rejected by KYB check")
    assert "123 456 789" not in redacted
    assert "[redacted-id-number]" in redacted


def test_redact_free_text_non_string_returns_empty():
    assert _redact_free_text(None) == ""
    assert _redact_free_text(123) == ""


def test_redact_free_text_leaves_safe_text_unchanged():
    assert _redact_free_text("fare calc failed") == "fare calc failed"


def test_render_markdown_redacts_pii_shaped_event_title():
    events = [_event("req-1", title="Failed to email receipt to rider@example.com")]
    clusters = correlate(events, [], [])
    report = render_markdown(clusters, "test window")
    assert "rider@example.com" not in report
    assert "[redacted-email]" in report


def test_render_markdown_redacts_pii_shaped_log_message():
    events = [_event("req-1")]
    logs = [_log("req-1", message="geocode failed for 52.1332,-106.6700")]
    clusters = correlate(events, logs, [])
    report = render_markdown(clusters, "test window")
    assert "52.1332" not in report
    assert "[redacted-coordinates]" in report


def test_render_markdown_truncates_long_log_lists():
    events = [_event("req-1")]
    logs = [_log("req-1", message=f"line {i}") for i in range(15)]
    clusters = correlate(events, logs, [])
    report = render_markdown(clusters, "test window")
    assert "and 5 more" in report
