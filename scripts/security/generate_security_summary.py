"""
Generate a Markdown security-posture summary from the JSON artifacts
security-gates.yml already produces (bandit, Spinr Semgrep rules, npm audit
for admin-dashboard).

Part of the 2026-09-19 security automation roadmap (Phase 2 — automated
report generation), replacing hand-writing a scan-summary doc after each
security-gates.yml run. See docs/audit/2026-09-19-security-automation-roadmap.md.

Deliberately has no dependency on the backend package or a database
connection: this runs in a bare CI job right after security-gates.yml, and
pulling in backend's full dependency set (supabase-py, etc.) for a docs-
generation step would be a heavier footprint than the job needs. If a future
phase wants this run logged to agent_action_log, do that from the workflow
step that invokes this script (a separate, backend-dependent step), not
from inside this module.

Any of the three input JSON files may be absent (a gate can fail before
producing output, or a job can be skipped by path-filtering) -- this must
never crash on a missing/malformed file, only note "not available" in the
report. A missing input is not the same as a clean scan; the two are always
distinguished in the output.

Output is rule id + file + line only, never a tool's free-text finding
message (bandit's `issue_text`, semgrep's `extra.message`). Both fields can
echo a literal matched secret value for a hardcoded-credential finding
(bandit B105/B106/B107, semgrep's `p/secrets` pack) -- safe to leave in a
short-retention CI artifact, not safe to copy into a permanently-committed
docs/audit/*.md file. Do not add these fields back to the rendered report.

Usage:
    python3 generate_security_summary.py \\
        --bandit /tmp/bandit-report.json \\
        --semgrep /tmp/spinr-rules.json \\
        --npm-audit /tmp/npm-audit-admin.json \\
        --repo owner/repo --run-id 12345 --branch main --commit abc123 \\
        --output /tmp/security_summary.md
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def _load_json_safe(path: Optional[str]) -> Optional[dict[str, Any]]:
    """Return the parsed JSON object, or None if the path is unset, missing,
    or not valid JSON. Never raises -- a missing/broken input degrades this
    tool's own report, it must never crash the report job."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def summarize_bandit(data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """bandit -f json shape: {"results": [{"issue_severity", "issue_confidence",
    "filename", "line_number", "issue_text", "test_id"}], ...}."""
    if data is None:
        return None
    results = data.get("results") or []
    counts: dict[str, int] = {}
    findings = []
    for r in results:
        sev = str(r.get("issue_severity", "UNKNOWN")).upper()
        _bump(counts, sev)
        # Deliberately excludes bandit's free-text `issue_text`: bandit's
        # hardcoded-secret checks (B105/B106/B107) can echo the literal
        # matched string value in that field. Rule id + file + line is
        # enough to triage and is safe to commit permanently to
        # docs/audit/ -- unlike the CI artifact this is read from (bounded
        # retention, requires repo/Actions read), a committed report is
        # public/permanent history.
        findings.append(
            {
                "severity": sev,
                "file": r.get("filename", "?"),
                "line": r.get("line_number", "?"),
                "test_id": r.get("test_id", "?"),
            }
        )
    return {"tool": "Bandit (Python SAST)", "total": len(results), "by_severity": counts, "findings": findings}


def summarize_semgrep(data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """semgrep --json shape: {"results": [{"check_id", "path",
    "start": {"line"}, "extra": {"severity", "message"}}], ...}. Severity
    values are semgrep's own ERROR/WARNING/INFO, not bandit/npm's scale --
    kept as-is rather than force-mapped onto SEVERITY_ORDER, to avoid
    misrepresenting a tool's own severity vocabulary as something it isn't."""
    if data is None:
        return None
    results = data.get("results") or []
    counts: dict[str, int] = {}
    findings = []
    for r in results:
        extra = r.get("extra") or {}
        sev = str(extra.get("severity", "UNKNOWN")).upper()
        _bump(counts, sev)
        # Deliberately excludes semgrep's free-text `extra.message`: the
        # `p/secrets` pack (enabled alongside the Spinr ruleset in this same
        # gate) can place a matched secret snippet in that field. Rule id +
        # file + line only -- same rationale as bandit above.
        findings.append(
            {
                "severity": sev,
                "file": r.get("path", "?"),
                "line": (r.get("start") or {}).get("line", "?"),
                "check_id": r.get("check_id", "?"),
            }
        )
    return {"tool": "Semgrep (Spinr custom rules)", "total": len(results), "by_severity": counts, "findings": findings}


def summarize_npm_audit(data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """npm audit --json shape: {"vulnerabilities": {moduleName: {"severity", ...}}, ...}."""
    if data is None:
        return None
    vulns = data.get("vulnerabilities") or {}
    counts: dict[str, int] = {}
    findings = []
    for module, vuln in vulns.items():
        sev = str(vuln.get("severity", "unknown")).upper()
        _bump(counts, sev)
        findings.append({"severity": sev, "module": module})
    return {"tool": "npm audit (admin-dashboard)", "total": len(vulns), "by_severity": counts, "findings": findings}


def _severity_table(by_severity: dict[str, int]) -> str:
    keys = sorted(by_severity.keys(), key=lambda k: (SEVERITY_ORDER.index(k) if k in SEVERITY_ORDER else len(SEVERITY_ORDER), k))
    if not keys:
        return "_no findings_\n"
    lines = ["| Severity | Count |", "|---|---|"]
    for k in keys:
        lines.append(f"| {k} | {by_severity[k]} |")
    return "\n".join(lines) + "\n"


def _tool_section(summary: Optional[dict[str, Any]], max_findings: int = 20) -> str:
    if summary is None:
        return "_No data available for this run (artifact missing or gate did not produce output — this is not the same as a clean scan)._\n\n"

    out = [f"**Total findings: {summary['total']}**\n", _severity_table(summary["by_severity"]), ""]
    findings = summary["findings"][:max_findings]
    if findings:
        out.append(f"<details><summary>Findings (showing {len(findings)} of {summary['total']})</summary>\n")
        for f in findings:
            if "line" in f:
                loc = f"`{f.get('file', '?')}:{f.get('line', '?')}`"
            else:
                loc = f"`{f.get('module', '?')}`"
            label = f.get("test_id") or f.get("check_id") or ""
            out.append(f"- **{f['severity']}** {loc} {label}".rstrip())
        out.append("\n</details>\n")
    return "\n".join(out) + "\n"


def generate(
    bandit_data: Optional[dict[str, Any]],
    semgrep_data: Optional[dict[str, Any]],
    npm_audit_data: Optional[dict[str, Any]],
    repo: str,
    run_id: str,
    branch: str,
    commit: str,
) -> str:
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    bandit_summary = summarize_bandit(bandit_data)
    semgrep_summary = summarize_semgrep(semgrep_data)
    npm_summary = summarize_npm_audit(npm_audit_data)

    header = f"""# Automated Security Scan Report

| Field | Value |
|---|---|
| **Date** | {report_date} |
| **Repo** | `{repo}` |
| **Run** | [{run_id}]({run_url}) |
| **Branch** | `{branch}` |
| **Commit** | `{commit[:12] if commit else "?"}` |

This report is generated automatically from the JSON artifacts
`security-gates.yml` produces (bandit, Spinr Semgrep rules, npm audit for
admin-dashboard). It summarizes finding counts by severity; it does not
replace reading the full CI job output for a real HIGH/CRITICAL finding, and
it does not cover every gate in that workflow (pip-audit, yarn-audit, and
gitleaks do not currently publish a downloadable artifact -- see
docs/audit/2026-09-19-security-automation-roadmap.md Phase 2 for the gap).

"""

    body = (
        "## G1 · Bandit (Python SAST)\n\n" + _tool_section(bandit_summary)
        + "## G3 · Semgrep (Spinr custom rules)\n\n" + _tool_section(semgrep_summary)
        + "## G4c · npm audit (admin-dashboard)\n\n" + _tool_section(npm_summary)
    )

    footer = "\n---\n_Generated by `scripts/security/generate_security_summary.py`, not hand-written._\n"

    return header + body + footer


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an automated security scan summary")
    parser.add_argument("--bandit", help="Path to bandit-report.json")
    parser.add_argument("--semgrep", help="Path to spinr-rules.json (semgrep --json output)")
    parser.add_argument("--npm-audit", help="Path to npm-audit-admin.json")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, help="Path for the rendered markdown")
    args = parser.parse_args()

    bandit_data = _load_json_safe(args.bandit)
    semgrep_data = _load_json_safe(args.semgrep)
    npm_audit_data = _load_json_safe(args.npm_audit)

    report = generate(bandit_data, semgrep_data, npm_audit_data, args.repo, args.run_id, args.branch, args.commit)
    Path(args.output).write_text(report)
    print(f"Report written to {args.output}")
    print(f"Report length: {len(report)} chars")


if __name__ == "__main__":
    main()
