"""
Generate a structured Markdown audit report from error, impact, and fix data.

Output: Markdown file suitable for:
  - Inclusion in reports/audits/
  - Pasting into a GitHub Issue
  - Slack notification body
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEVERITY_EMOJI = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "⚪"}
CATEGORY_EMOJI = {
    "test":       "🧪",
    "coverage":   "📊",
    "lint":       "🔍",
    "security":   "🔐",
    "build":      "🏗️",
    "deploy":     "🚀",
    "dependency": "📦",
    "infra":      "⚙️",
}
BLAST_EMOJI = {"FULL": "💥", "HIGH": "🔥", "PARTIAL": "⚡", "LOW": "💡", "NONE": "✅"}


def _header(
    workflow: str,
    run_id: str,
    repo: str,
    branch: str,
    commit: str,
    report_date: str,
    top_severity: str,
    total_errors: int,
) -> str:
    sev_emoji = SEVERITY_EMOJI.get(top_severity, "⚪")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    return f"""# CI Error Audit Report

| Field | Value |
|---|---|
| **Date** | {report_date} |
| **Workflow** | `{workflow}` |
| **Run ID** | [{run_id}]({run_url}) |
| **Branch** | `{branch}` |
| **Commit** | `{commit[:12]}` |
| **Top Severity** | {sev_emoji} {top_severity} |
| **Total Errors** | {total_errors} |

"""


def _executive_summary(
    errors_data: dict[str, Any],
    impact_data: dict[str, Any],
    fixes_data: dict[str, Any],
) -> str:
    severity_counts = errors_data.get("severity_counts", {})
    affected_surfaces = impact_data.get("affected_surfaces", [])
    is_production_at_risk = impact_data.get("is_production_at_risk", False)
    top_blast = impact_data.get("top_blast_radius", "LOW")
    cr_count = fixes_data.get("change_requests_needed", 0)
    safe_fix_count = fixes_data.get("immediate_safe_fixes", 0)

    prod_warning = (
        "\n> ⚠️  **PRODUCTION IS AT RISK** — P0 failures detected. Immediate response required.\n"
        if is_production_at_risk else ""
    )

    sev_parts = [
        f"{SEVERITY_EMOJI.get(s, '')} {s}: {c}"
        for s, c in severity_counts.items() if c > 0
    ]

    return f"""## Executive Summary
{prod_warning}
- **Blast Radius:** {BLAST_EMOJI.get(top_blast, '')} {top_blast}
- **Severity breakdown:** {' | '.join(sev_parts) or 'None'}
- **Affected surfaces:** {', '.join(f'`{s}`' for s in affected_surfaces) or 'None'}
- **Immediate safe fixes available:** {safe_fix_count}
- **Change Requests to raise:** {cr_count}

"""


def _error_table(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return "## Errors\n\nNo errors found.\n\n"

    rows = []
    for e in errors:
        sev = e.get("severity", "P3")
        cat = e.get("category", "unknown")
        rows.append(
            f"| {SEVERITY_EMOJI.get(sev, '')} {sev} "
            f"| {CATEGORY_EMOJI.get(cat, '')} {cat} "
            f"| `{e.get('surface', '?')}` "
            f"| `{e.get('job', '?')}` "
            f"| {e.get('description', '')} |"
        )

    table = "\n".join(rows)
    return f"""## Errors Found

| Severity | Category | Surface | Job | Description |
|---|---|---|---|---|
{table}

"""


def _error_details(errors: list[dict[str, Any]], assessments: list[dict[str, Any]]) -> str:
    if not errors:
        return ""

    assess_map: dict[str, dict[str, Any]] = {
        a["error_ref"]: a for a in assessments
    }

    sections = ["## Error Details\n"]
    for i, e in enumerate(errors, 1):
        sev = e.get("severity", "P3")
        cat = e.get("category", "unknown")
        ref = f"{e.get('job', '?')}::{cat}"
        assessment = assess_map.get(ref, {})
        blast = assessment.get("blast_radius", "UNKNOWN")
        impact_note = assessment.get("impact_note", "")
        affected_feats = assessment.get("affected_features", [])
        user_impact = assessment.get("user_impact", {})

        feat_list = "\n".join(f"  - {f}" for f in affected_feats) if affected_feats else "  - Unknown"
        excerpt = e.get("log_excerpt", "").strip()
        log_block = f"\n```\n{excerpt}\n```\n" if excerpt else ""

        sections.append(f"""### {i}. {SEVERITY_EMOJI.get(sev, '')} [{sev}] {cat.upper()} — `{e.get('job', '?')}`

- **Step:** `{e.get('step', '?')}`
- **Surface:** `{e.get('surface', '?')}`
- **Blast Radius:** {BLAST_EMOJI.get(blast, '')} {blast}
- **User Impact:** {user_impact.get('description', 'Unknown')}
- **Response SLA:** {user_impact.get('response_sla', 'Unknown')}
- **Affected Features:**
{feat_list}
- **Impact Note:** {impact_note}
{log_block}
""")

    return "\n".join(sections) + "\n"


def _fix_plan(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return ""

    sections = ["## Fix Plan\n"]
    for rec in recommendations:
        sev = rec.get("severity", "P3")
        safe_label = "✅ Safe to fix directly" if rec.get("is_safe_to_auto_fix") else "⚠️  Requires careful review"
        cr_label = "🔖 Change Request required" if rec.get("requires_change_request") else ""

        steps_md = ""
        for j, step in enumerate(rec.get("steps", []), 1):
            cmd = f"\n   ```bash\n   {step['command']}\n   ```" if step.get("command") else ""
            risk = f" _(risk: {step['risk_level']})_" if step.get("risk_level") != "low" else ""
            steps_md += f"{j}. {step['action']}{risk}{cmd}\n"

        sections.append(f"""### Fix: `{rec['error_ref']}`

**Summary:** {rec['fix_summary']}

**{safe_label}** {cr_label}

**Steps:**
{steps_md}
**Guard Rail:** {rec.get('guard_rail_suggestion', 'N/A')}

**Estimated Effort:** `{rec.get('estimated_effort', '?')}`

""")

    return "\n".join(sections)


def _change_requests_section(change_requests: list[dict[str, Any]]) -> str:
    if not change_requests:
        return ""

    rows = []
    for i, cr in enumerate(change_requests, 1):
        cr_id = f"CR-{datetime.now(timezone.utc).year}-{i:03d}"
        rows.append(
            f"| `{cr_id}` | {cr.get('title', '')} | {cr.get('category', '')} "
            f"| {SEVERITY_EMOJI.get(cr.get('priority', 'P3'), '')} {cr.get('priority', 'P3')} "
            f"| ⏳ Pending |"
        )

    table = "\n".join(rows)
    return f"""## Proposed Change Requests

The following improvements were identified during this audit. They are **not implemented yet** and require approval before work begins.

| CR ID | Title | Category | Priority | Status |
|---|---|---|---|---|
{table}

> To raise a Change Request: use [.github/ISSUE_TEMPLATE/ci_change_request.yml](../../.github/ISSUE_TEMPLATE/ci_change_request.yml)

"""


def _footer(report_path: str) -> str:
    return f"""---

## Audit Metadata

- **Audit System:** Spinr CI Error Audit v1.0
- **Report saved:** `{report_path}`
- **Process:** Error Classification → Impact Assessment → Fix Recommendation → Change Request Generation
- **Next action:** Address P0/P1 items per SLA. File Change Requests for improvements.

_Generated by `scripts/ci-audit/report_generator.py`_
"""


def generate(
    errors_data: dict[str, Any],
    impact_data: dict[str, Any],
    fixes_data: dict[str, Any],
    workflow: str,
    run_id: str,
    repo: str,
    branch: str,
    commit: str,
    report_path: str,
) -> str:
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    top_severity = errors_data.get("top_severity", "P3")
    total_errors = errors_data.get("total_errors", 0)

    errors = errors_data.get("errors", [])
    assessments = impact_data.get("assessments", [])
    recommendations = fixes_data.get("recommendations", [])
    change_requests = fixes_data.get("proposed_change_requests", [])

    report = (
        _header(workflow, run_id, repo, branch, commit, report_date, top_severity, total_errors)
        + _executive_summary(errors_data, impact_data, fixes_data)
        + _error_table(errors)
        + _error_details(errors, assessments)
        + _fix_plan(recommendations)
        + _change_requests_section(change_requests)
        + _footer(report_path)
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CI audit report")
    parser.add_argument("--errors",      required=True)
    parser.add_argument("--impact",      required=True)
    parser.add_argument("--fixes",       required=True)
    parser.add_argument("--workflow",    required=True)
    parser.add_argument("--run-id",      required=True)
    parser.add_argument("--repo",        required=True)
    parser.add_argument("--branch",      required=True)
    parser.add_argument("--commit",      required=True)
    parser.add_argument("--output",      required=True, help="Path for the rendered markdown")
    parser.add_argument("--report-path", required=True, help="Logical path for the report (included in footer)")
    args = parser.parse_args()

    errors_data = json.loads(Path(args.errors).read_text())
    impact_data = json.loads(Path(args.impact).read_text())
    fixes_data  = json.loads(Path(args.fixes).read_text())

    report = generate(
        errors_data, impact_data, fixes_data,
        args.workflow, args.run_id, args.repo,
        args.branch, args.commit, args.report_path,
    )
    Path(args.output).write_text(report)
    print(f"Report written to {args.output}")
    print(f"Report length: {len(report)} chars")


if __name__ == "__main__":
    main()
