"""
Unit tests for scripts/security/threat_watch.py (Phase 4a of the
2026-09-19 security automation roadmap).

Runs directly (mirrors scripts/security/'s existing test convention):

    pytest scripts/security/test_threat_watch.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from threat_watch import (  # noqa: E402
    _extract_ghsa_id,
    find_new_npm_findings,
    find_pending_decisions,
    render_decision_request,
)

MAPLIBRE_AUDIT = {
    "vulnerabilities": {
        "maplibre-gl": {
            "severity": "critical",
            "via": [{"url": "https://github.com/advisories/GHSA-jrc7-96c5-q579", "title": "Something bad"}],
        }
    }
}


def test_extract_ghsa_id_from_url():
    assert _extract_ghsa_id("https://github.com/advisories/GHSA-jrc7-96c5-q579") == "GHSA-jrc7-96c5-q579"


def test_extract_ghsa_id_no_marker_returns_none():
    assert _extract_ghsa_id("not a url") is None


def test_extract_ghsa_id_non_string_returns_none():
    assert _extract_ghsa_id(None) is None


def test_find_new_npm_findings_reports_untracked_critical():
    findings = find_new_npm_findings(MAPLIBRE_AUDIT, tracked=[], surface="admin-dashboard")
    assert len(findings) == 1
    assert findings[0]["advisory_id"] == "GHSA-jrc7-96c5-q579"
    assert findings[0]["module"] == "maplibre-gl"
    assert findings[0]["severity"] == "critical"
    assert findings[0]["surface"] == "admin-dashboard"


def test_find_new_npm_findings_skips_already_tracked():
    tracked = [{"advisory_id": "GHSA-jrc7-96c5-q579", "surface": "admin-dashboard", "status": "accepted_risk"}]
    findings = find_new_npm_findings(MAPLIBRE_AUDIT, tracked=tracked, surface="admin-dashboard")
    assert findings == []


def test_find_new_npm_findings_same_advisory_different_surface_still_new():
    tracked = [{"advisory_id": "GHSA-jrc7-96c5-q579", "surface": "rider-app", "status": "accepted_risk"}]
    findings = find_new_npm_findings(MAPLIBRE_AUDIT, tracked=tracked, surface="admin-dashboard")
    assert len(findings) == 1


def test_find_new_npm_findings_ignores_low_moderate_severity():
    data = {"vulnerabilities": {"some-pkg": {"severity": "moderate", "via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]}}}
    assert find_new_npm_findings(data, tracked=[], surface="admin-dashboard") == []


def test_find_new_npm_findings_none_data_returns_empty():
    assert find_new_npm_findings(None, tracked=[], surface="admin-dashboard") == []


def test_find_new_npm_findings_deduplicates_same_advisory_across_modules():
    data = {
        "vulnerabilities": {
            "vite-plugin-storybook-nextjs": {
                "severity": "high",
                "via": ["@storybook/nextjs-vite"],
            },
            "@storybook/nextjs-vite": {
                "severity": "high",
                "via": ["image-size"],
            },
            "image-size": {
                "severity": "high",
                "via": [{"url": "https://github.com/advisories/GHSA-w3rx-r6r6-pgpr"}],
            },
        }
    }
    findings = find_new_npm_findings(data, tracked=[], surface="admin-dashboard")
    # All three modules resolve to the same leaf advisory -- must not
    # produce 3 duplicate decision requests for one real finding.
    assert len(findings) == 1
    assert findings[0]["advisory_id"] == "GHSA-w3rx-r6r6-pgpr"


def test_find_pending_decisions_filters_by_status():
    tracked = [
        {"advisory_id": "A", "status": "needs_decision"},
        {"advisory_id": "B", "status": "accepted_risk"},
        {"advisory_id": "C", "status": "fixed"},
    ]
    pending = find_pending_decisions(tracked)
    assert len(pending) == 1
    assert pending[0]["advisory_id"] == "A"


def test_render_decision_request_includes_all_facts_no_fabricated_recommendation():
    finding = {
        "advisory_id": "GHSA-jrc7-96c5-q579",
        "module": "maplibre-gl",
        "reached_via": None,
        "ecosystem": "npm",
        "surface": "admin-dashboard",
        "severity": "critical",
        "url": "https://github.com/advisories/GHSA-jrc7-96c5-q579",
    }
    doc = render_decision_request(finding)
    assert "GHSA-jrc7-96c5-q579" in doc
    assert "maplibre-gl" in doc
    assert "admin-dashboard" in doc
    assert "CRITICAL" in doc
    # Must not fabricate a specific fix recommendation for a real,
    # unresearched finding -- this is the core safety property of the tool.
    assert "No default recommendation is given here" in doc
    assert "still needs research" in doc.lower()


def test_render_decision_request_shows_dependency_chain_when_transitive():
    finding = {
        "advisory_id": "GHSA-w3rx-r6r6-pgpr",
        "module": "image-size",
        "reached_via": "vite-plugin-storybook-nextjs",
        "ecosystem": "npm",
        "surface": "admin-dashboard",
        "severity": "high",
        "url": "https://github.com/advisories/GHSA-w3rx-r6r6-pgpr",
    }
    doc = render_decision_request(finding)
    assert "vite-plugin-storybook-nextjs" in doc
    assert "image-size" in doc
