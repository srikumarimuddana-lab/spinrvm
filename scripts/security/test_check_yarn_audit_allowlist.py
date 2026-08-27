"""
Unit tests for scripts/security/check_yarn_audit_allowlist.py (CR #4548).

Runs directly (mirrors scripts/ci-audit/'s test convention — this lives
outside backend/pytest.ini's testpaths):

    pytest scripts/security/test_check_yarn_audit_allowlist.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_yarn_audit_allowlist.py"


def _advisory(module: str, severity: str, url: str) -> str:
    return json.dumps({
        "type": "auditAdvisory",
        "data": {"resolution": {}, "advisory": {
            "module_name": module, "severity": severity, "url": url,
        }},
    })


def _run(tmp_path: Path, lines: list[str]) -> subprocess.CompletedProcess:
    audit_file = tmp_path / "yarn-audit.json"
    audit_file.write_text("\n".join(lines) + "\n" if lines else "")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(audit_file)],
        capture_output=True, text=True,
    )


def test_allowlisted_image_size_advisories_pass(tmp_path):
    lines = [
        _advisory("image-size", "high", "https://github.com/advisories/GHSA-w3rx-r6r6-pgpr"),
        _advisory("image-size", "high", "https://github.com/advisories/GHSA-5p2g-fcmc-qvqq"),
    ]
    result = _run(tmp_path, lines)
    assert result.returncode == 0
    assert "Allowlisted" in result.stdout


def test_unrelated_high_advisory_still_blocks(tmp_path):
    lines = [
        _advisory("left-pad", "high", "https://github.com/advisories/GHSA-xxxx-yyyy-zzzz"),
    ]
    result = _run(tmp_path, lines)
    assert result.returncode == 1
    assert "left-pad" in result.stdout


def test_image_size_with_different_ghsa_still_blocks(tmp_path):
    # Same module, but not one of the two allowlisted advisory IDs —
    # a future new image-size finding must not slip through.
    lines = [
        _advisory("image-size", "high", "https://github.com/advisories/GHSA-new1-new1-new1"),
    ]
    result = _run(tmp_path, lines)
    assert result.returncode == 1
    assert "image-size" in result.stdout


def test_moderate_severity_never_blocks(tmp_path):
    lines = [
        _advisory("some-pkg", "moderate", "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"),
    ]
    result = _run(tmp_path, lines)
    assert result.returncode == 0


def test_empty_audit_passes(tmp_path):
    result = _run(tmp_path, [])
    assert result.returncode == 0


def test_mixed_allowlisted_and_blocking(tmp_path):
    lines = [
        _advisory("image-size", "high", "https://github.com/advisories/GHSA-w3rx-r6r6-pgpr"),
        _advisory("other-pkg", "critical", "https://github.com/advisories/GHSA-real-real-real"),
    ]
    result = _run(tmp_path, lines)
    assert result.returncode == 1
    assert "other-pkg" in result.stdout
    assert "Allowlisted" in result.stdout
