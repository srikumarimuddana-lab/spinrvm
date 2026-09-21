"""
Generate a Markdown weekly Sentry triage report from a structured JSON
findings file produced by the /sentry-triage command (see
.claude/commands/sentry-triage.md and .claude/agents/spinr-sentry-triage-
investigator.md).

Deliberately stdlib-only, no backend/database dependency -- mirrors
scripts/security/generate_security_summary.py's own reasoning: this report
step should not need supabase-py or any live DB connection just to render
Markdown from JSON someone already produced.

PII discipline (non-negotiable, per CLAUDE.md's PIPEDA section and the
investigator agent's own output rules): this script only ever renders
short-id, title, category, dates, counts, domain/surface tags, and free-text
fields the investigator already wrote (root_cause, correlated_timeline,
recommended_fix, etc.) -- it never has access to a raw Sentry payload/
stacktrace/breadcrumb, and it does not accept one as input. If a caller ever
adds a field carrying raw event data to the input schema, that is a bug in
the caller, not something this script should try to detect or scrub -- the
investigator agent's own rules are what keep raw payloads out of the input
in the first place.

`correlated_timeline` (added 2026-09-21, alongside the investigator's new
Correlate step) carries the investigator's own free-text summary of what
correlate_incident.py and local grep/git-history checks found -- including,
when applicable, the mandatory "log/audit correlation not available this
run" disclosure. Rendering it here is what lets that disclosure survive
into the durable, published report rather than existing only in the
in-session chat output.

A missing/empty input (no issues this week) is not an error -- it's the
report's expected content on a clean week, and must say so plainly rather
than rendering a blank or ambiguous file.

Usage:
    python3 generate_sentry_weekly_report.py \\
        --findings /tmp/sentry_triage_findings.json \\
        --window-start 2026-09-14 --window-end 2026-09-21 \\
        --output docs/audit/sentry-triage/2026-09-21-weekly-report.md
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CATEGORY_ORDER = ["new", "regressed", "climbing", "long_tail"]
CATEGORY_LABEL = {
    "new": "New this window",
    "regressed": "Regressed (previously resolved, reopened)",
    "climbing": "Still climbing (unresolved, event count rising)",
    "long_tail": "Long-tail unresolved (not investigated in depth)",
}
ACTION_LABEL = {
    "fixed_pr": "Fix PR opened",
    "needs_human_triage": "Needs human triage",
    "blocked": "Blocked",
    "no_action_needed": "No action needed",
}


def _load_json_safe(path: Optional[str]) -> Optional[dict[str, Any]]:
    """Return the parsed JSON object, or None if the path is unset, missing,
    or not valid JSON. Never raises -- a missing/broken input degrades this
    report, it must never crash the report job (same contract as
    generate_security_summary.py's _load_json_safe)."""
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


def _render_issue(issue: dict[str, Any]) -> str:
    short_id = issue.get("short_id", "UNKNOWN-ID")
    title = issue.get("title", "(no title provided)")
    lines = [f"#### `{short_id}` — {title}", ""]

    meta_bits = []
    if issue.get("first_seen"):
        meta_bits.append(f"first seen {issue['first_seen']}")
    if issue.get("last_seen"):
        meta_bits.append(f"last seen {issue['last_seen']}")
    if issue.get("event_count") is not None:
        meta_bits.append(f"{issue['event_count']} events")
    if issue.get("affected_users") is not None:
        meta_bits.append(f"{issue['affected_users']} affected users")
    if meta_bits:
        lines.append("- " + " · ".join(meta_bits))

    tag_bits = []
    if issue.get("domain"):
        tag_bits.append(f"domain=`{issue['domain']}`")
    if issue.get("surface"):
        tag_bits.append(f"surface=`{issue['surface']}`")
    if tag_bits:
        lines.append("- Tags: " + ", ".join(tag_bits))

    if issue.get("root_cause"):
        lines.append(f"- **Root cause:** {issue['root_cause']}")
    if issue.get("correlated_timeline"):
        lines.append(f"- **Correlated timeline:** {issue['correlated_timeline']}")
    if issue.get("recommended_fix"):
        lines.append(f"- **Fix:** {issue['recommended_fix']}")
    if issue.get("confidence"):
        lines.append(f"- **Confidence:** {issue['confidence']}")

    action = issue.get("action_taken")
    action_line = ACTION_LABEL.get(action, action or "unspecified")
    if issue.get("pr_url"):
        action_line += f" — {issue['pr_url']}"
    lines.append(f"- **Outcome:** {action_line}")

    if issue.get("guardrail_added"):
        lines.append(f"- **Guardrail added:** {issue['guardrail_added']}")
    if issue.get("known_item_ref"):
        lines.append(f"- Relates to `ACTION_ITEMS.md` {issue['known_item_ref']} — not a fresh discovery")

    lines.append("")
    return "\n".join(lines)


def render_report(data: Optional[dict[str, Any]], window_start: str, window_end: str) -> str:
    lines = [
        f"# Sentry Weekly Triage Report — {window_start} to {window_end}",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `/sentry-triage`._",
        "",
    ]

    if data is None:
        lines += [
            "**No findings data available for this window** — the triage run either did not "
            "produce a findings file, or it failed before reaching the report step. This is "
            "distinct from a clean week (see below) and should not be read as \"no errors.\"",
            "",
        ]
        return "\n".join(lines)

    org = data.get("org", "spinr-backend")
    project = data.get("project", "crimson-smoke-7445")
    connector_ok = data.get("connector_ok")
    lines.append(f"Org/project: `{org}` / `{project}`")
    if connector_ok is False:
        lines += [
            "",
            "**⚠️ Sentry connector was not authorized during this run.** This report reflects "
            "no live scan, not a clean week — the connector needs to be re-authorized via "
            "claude.ai connector settings before the next run can actually check for errors.",
        ]
    lines.append("")

    issues = data.get("issues") or []
    if not issues:
        lines += [
            "**No unresolved issues found this window.** Confirmed via a live scan (connector "
            "authorized, org/project verified) — this is a clean week, not a missing-data gap.",
            "",
        ]
    else:
        by_category: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            by_category.setdefault(issue.get("category", "new"), []).append(issue)

        counts = {cat: len(by_category.get(cat, [])) for cat in CATEGORY_ORDER}
        lines.append(
            f"**{len(issues)} issue(s) reviewed** — "
            + ", ".join(f"{counts[c]} {CATEGORY_LABEL[c].lower()}" for c in CATEGORY_ORDER if counts[c])
        )
        lines.append("")

        for cat in CATEGORY_ORDER:
            cat_issues = by_category.get(cat)
            if not cat_issues:
                continue
            lines.append(f"### {CATEGORY_LABEL[cat]}")
            lines.append("")
            for issue in cat_issues:
                lines.append(_render_issue(issue))

    blockers = data.get("blockers") or []
    if blockers:
        lines.append("## Blockers this run")
        lines.append("")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    carry_forward = data.get("carry_forward") or []
    if carry_forward:
        lines.append("## Carried-forward open items (not re-investigated)")
        lines.append("")
        for item in carry_forward:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", help="Path to the structured JSON findings file")
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--output", required=True, help="Path to write the rendered Markdown report")
    args = parser.parse_args()

    data = _load_json_safe(args.findings)
    report = render_report(data, args.window_start, args.window_end)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote {out_path} ({len(report)} bytes)")


if __name__ == "__main__":
    main()
