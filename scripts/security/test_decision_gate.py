"""
Unit tests for scripts/security/decision_gate.py (Phase 5 of the
2026-09-19 security automation roadmap).

Runs directly:

    pytest scripts/security/test_decision_gate.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from decision_gate import (  # noqa: E402
    _MARKER,
    _load_json_safe,
    compute_verdict,
    pending_entries,
    render_comment,
)


def test_load_json_safe_missing_path_returns_empty_list(tmp_path):
    assert _load_json_safe(str(tmp_path / "nope.json")) == []


def test_load_json_safe_none_returns_empty_list():
    assert _load_json_safe(None) == []


def test_load_json_safe_non_list_raises_instead_of_silently_reporting_go(tmp_path):
    """A tracker file that exists but isn't a JSON list must fail loudly,
    not silently present as an empty (GO) tracker -- CLAUDE.md's
    don't-silently-swallow-errors principle."""
    bad = tmp_path / "obj.json"
    bad.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError, match="not a JSON list"):
        _load_json_safe(str(bad))


def test_load_json_safe_malformed_json_raises_instead_of_silently_reporting_go(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError, match="not valid JSON"):
        _load_json_safe(str(bad))


def test_compute_verdict_empty_tracker_is_go():
    assert compute_verdict([]) == "GO"


def test_compute_verdict_all_resolved_is_go():
    tracked = [
        {"advisory_id": "A", "status": "fixed"},
        {"advisory_id": "B", "status": "accepted_risk"},
        {"advisory_id": "C", "status": "wont_fix"},
    ]
    assert compute_verdict(tracked) == "GO"


def test_compute_verdict_any_needs_decision_is_no_go():
    tracked = [{"advisory_id": "A", "status": "fixed"}, {"advisory_id": "B", "status": "needs_decision"}]
    assert compute_verdict(tracked) == "NO_GO"


def test_compute_verdict_missing_status_fails_closed_as_needs_decision():
    """An incomplete tracker entry (no status field at all) must be
    treated as needs_decision, not silently passed -- fail closed."""
    tracked = [{"advisory_id": "A"}]
    assert compute_verdict(tracked) == "NO_GO"


def test_pending_entries_filters_correctly():
    tracked = [
        {"advisory_id": "A", "status": "needs_decision"},
        {"advisory_id": "B", "status": "fixed"},
        {"advisory_id": "C"},  # missing status -> counts as pending
    ]
    pending = pending_entries(tracked)
    assert {e["advisory_id"] for e in pending} == {"A", "C"}


def test_render_comment_go_verdict_no_pending():
    comment = render_comment([])
    assert _MARKER in comment
    assert "✅ GO" in comment
    assert "Open decisions:** 0" in comment
    assert "Nothing blocking" in comment


def test_render_comment_no_go_verdict_lists_pending():
    tracked = [
        {
            "advisory_id": "GHSA-jrc7-96c5-q579",
            "module": "maplibre-gl",
            "surface": "admin-dashboard",
            "severity": "critical",
            "status": "needs_decision",
            "first_seen": "2026-09-19",
        }
    ]
    comment = render_comment(tracked)
    assert "🔴 NO-GO" in comment
    assert "GHSA-jrc7-96c5-q579" in comment
    assert "maplibre-gl" in comment
    assert "admin-dashboard" in comment
    assert "decision-request-GHSA-jrc7-96c5-q579.md" in comment


def test_render_comment_includes_marker_for_idempotent_updates():
    """The marker lets a workflow find and update the same comment on
    re-runs instead of posting duplicates -- must always be present."""
    assert render_comment([]).startswith(_MARKER)
    assert render_comment([{"advisory_id": "A", "status": "needs_decision"}]).startswith(_MARKER)


def test_render_comment_computes_days_pending():
    from datetime import date, timedelta

    ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
    tracked = [{"advisory_id": "A", "module": "x", "surface": "y", "severity": "high", "status": "needs_decision", "first_seen": ten_days_ago}]
    comment = render_comment(tracked)
    assert "| 10 |" in comment


def test_render_comment_handles_missing_first_seen_gracefully():
    tracked = [{"advisory_id": "A", "module": "x", "surface": "y", "severity": "high", "status": "needs_decision"}]
    comment = render_comment(tracked)
    assert "| ? |" in comment
