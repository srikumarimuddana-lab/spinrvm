import json
import re
import xml.etree.ElementTree as ET

import pytest

from scripts.validate_dast import validate_report, validate_target, write_scope_context


STAGING = "https://spinr-backend-staging.fly.dev"


def test_target_requires_https_and_exact_allowlisted_origin():
    assert validate_target(STAGING, STAGING) == STAGING
    for target, allowed in [("", STAGING), (STAGING, ""), ("http://stage.test", "http://stage.test"),
                            (STAGING + "/api", STAGING), (STAGING + "?x=1", STAGING),
                            ("https://user:pass@stage.test", "https://stage.test"),
                            ("https://stage:notaport", "https://stage:notaport"),
                            ("https://stage..fly.dev", "https://stage..fly.dev"),
                            ("https://api-spinr.spinr.ca", "https://api-spinr.spinr.ca"),
                            (STAGING, "https://other-staging.fly.dev")]:
        with pytest.raises(ValueError):
            validate_target(target, allowed)


def test_report_must_be_nonempty_valid_json_and_include_exact_target(tmp_path):
    report = tmp_path / "report_json.json"
    report.write_text(json.dumps({"site": [{"@host": "spinr-backend-staging.fly.dev",
                                             "@port": "443", "@ssl": "true", "alerts": []}]}))
    validate_report(report, STAGING)  # A real zero-alert scan is still a report.
    report.write_text('{"site": []}')
    with pytest.raises(ValueError):
        validate_report(report, STAGING)
    report.write_text('{broken')
    with pytest.raises(ValueError):
        validate_report(report, STAGING)


def test_report_rejects_another_host_or_port(tmp_path):
    report = tmp_path / "report_json.json"
    for host, port in [("api-spinr.spinr.ca", "443"), ("spinr-backend-staging.fly.dev", "8443")]:
        report.write_text(json.dumps({"site": [{"@host": host, "@port": port,
                                                 "@ssl": "true", "alerts": []}]}))
        with pytest.raises(ValueError):
            validate_report(report, STAGING)


def test_report_rejects_mixed_in_scope_and_out_of_scope_sites(tmp_path):
    report = tmp_path / "report_json.json"
    report.write_text(json.dumps({"site": [
        {"@host": "spinr-backend-staging.fly.dev", "@port": "443", "@ssl": "true"},
        {"@host": "api-spinr.spinr.ca", "@port": "443", "@ssl": "true"},
    ]}))
    with pytest.raises(ValueError):
        validate_report(report, STAGING)


@pytest.mark.parametrize("target,other", [
    (STAGING, "https://spinr-backend-staging.fly.dev.evil.test/path"),
    ("https://stage.example.test:8443", "https://stage.example.test:8443.evil.test/path"),
])
def test_scope_context_uses_anchored_exact_origin_regex(tmp_path, target, other):
    context = tmp_path / "staging.context"
    write_scope_context(target, context)
    root = ET.parse(context).getroot()
    assert root.findtext("./context/name") == "Staging"
    assert root.findtext("./context/inscope") == "true"
    # ZAP's exported .context format stores regex text directly in repeated
    # <incregexes> elements (not nested <incregex> children).
    patterns = root.findall("./context/incregexes")
    assert len(patterns) == 1
    pattern = patterns[0].text
    assert pattern and re.fullmatch(pattern, target + "/health")
    assert not re.fullmatch(pattern, other)


def test_default_https_port_context_accepts_normalized_and_explicit_443(tmp_path):
    context = tmp_path / "staging.context"
    write_scope_context(STAGING, context)
    pattern = ET.parse(context).getroot().findtext("./context/incregexes")
    assert pattern and re.fullmatch(pattern, STAGING + "/health")
    assert re.fullmatch(pattern, STAGING + ":443/health")


def test_workflow_fails_closed_scopes_before_zap_and_checks_report_afterward():
    workflow = open(".github/workflows/dast-zap-baseline.yml", encoding="utf-8").read()
    preflight = workflow.index("--write-scope-context staging.context")
    scanner = workflow.index("uses: zaproxy/action-baseline@")
    report_check = workflow.index("python3 scripts/validate_dast.py report_json.json")
    assert preflight < scanner < report_check
    assert "cmd_options: '-a -n staging.context'" in workflow
    assert "STAGING_ALLOWED_ORIGIN" in workflow
    assert "No-op (STAGING_URL not configured)" not in workflow
