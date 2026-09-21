"""
Go/no-go decision gate: reads docs/security/tracked-advisories.json and
renders a PR-comment-shaped Markdown summary of every advisory still
awaiting a human decision, with a link to its decision-request document.

Phase 5 of the 2026-09-19 security automation roadmap
(docs/audit/2026-09-19-security-automation-roadmap.md). Delivery channel
is PR comments only, per explicit instruction -- no Slack/email dispatch
is built here.

What this is NOT: a required, merge-blocking GitHub branch-protection
check. Making a status check "required" is a repo-settings change only a
human with admin access can make (Settings -> Branches -> branch
protection rules) -- the same class of step as DAST's Fly/Supabase
provisioning (ACTION_ITEMS E1) and the Sentry connector authorization this
whole roadmap has repeatedly been blocked on. This script and its workflow
produce the SIGNAL (a clear go/no-go verdict, posted where a reviewer will
see it); wiring that signal into "cannot merge until this passes" is a
follow-up GitHub settings step, not something this code can do for itself.

Verdict logic: GO if every entry in tracked-advisories.json has a status
other than "needs_decision" (i.e. someone has recorded fixed/accepted_risk/
wont_fix, with reasoning). NO-GO if any entry is still "needs_decision".
An empty tracker is GO (nothing pending). This mirrors the same posture as
the rest of this roadmap: the tool surfaces what needs a human call, it
never manufactures one.

Usage:
    python3 decision_gate.py --tracked-advisories docs/security/tracked-advisories.json --output /tmp/gate_comment.md
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

_MARKER = "<!-- spinr-decision-gate -->"


def _load_json_safe(path: Optional[str]) -> list[dict[str, Any]]:
    """A missing/unset path is a legitimate empty tracker (nothing tracked
    yet) -- returns []. A path that EXISTS but is malformed or not a JSON
    list is a different case: silently treating it as an empty tracker
    would report GO on a broken tracker, contradicting compute_verdict's
    own fail-closed posture for an incomplete entry. Raise instead, per
    CLAUDE.md's "don't silently swallow errors" -- a corrupted tracker
    file must surface loudly, not quietly present a clean bill of health.
    """
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"decision_gate: {path} exists but is not valid JSON -- refusing to silently treat this as an empty (GO) tracker: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"decision_gate: {path} exists but is not a JSON list -- refusing to silently treat this as an empty (GO) tracker")
    return data


def _days_pending(first_seen: Any) -> Optional[int]:
    if not isinstance(first_seen, str):
        return None
    try:
        seen = date.fromisoformat(first_seen)
    except ValueError:
        return None
    return (date.today() - seen).days


def compute_verdict(tracked: list[dict[str, Any]]) -> str:
    """Returns "GO" or "NO_GO". An entry missing a status entirely is
    treated as needs_decision (fail closed -- an incomplete tracker entry
    is not the same as a resolved one)."""
    for entry in tracked:
        if entry.get("status", "needs_decision") == "needs_decision":
            return "NO_GO"
    return "GO"


def pending_entries(tracked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in tracked if e.get("status", "needs_decision") == "needs_decision"]


def render_comment(tracked: list[dict[str, Any]]) -> str:
    """Render the PR-comment body. Includes `_MARKER` so a workflow can
    find and update this exact comment on re-runs instead of posting a
    new one every time (avoids spamming a PR thread)."""
    verdict = compute_verdict(tracked)
    pending = pending_entries(tracked)
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        _MARKER,
        "## Security Decision Gate",
        "",
        f"**Verdict:** {'✅ GO' if verdict == 'GO' else '🔴 NO-GO'}  ",
        f"**Checked:** {report_date}  ",
        f"**Open decisions:** {len(pending)}",
        "",
    ]

    if not pending:
        lines.append("No dependency-advisory decisions are pending. Nothing blocking on this front.")
    else:
        lines.append(
            "The following advisories are tracked in `docs/security/tracked-advisories.json` "
            "with status `needs_decision` -- a human needs to research and record a decision "
            "(see each linked decision-request doc) before this can be considered resolved. "
            "This does not block merging on its own (no required check is wired up yet -- see "
            "the module docstring) but should not be silently ignored."
        )
        lines.append("")
        lines.append("| Advisory | Module | Surface | Severity | Days pending | Decision doc |")
        lines.append("|---|---|---|---|---|---|")
        for e in pending:
            days = _days_pending(e.get("first_seen"))
            days_str = str(days) if days is not None else "?"
            advisory_id = e.get("advisory_id", "?")
            doc_path = f"docs/security/decision-requests/decision-request-{str(advisory_id).replace('/', '_')}.md"
            lines.append(
                f"| {advisory_id} | `{e.get('module', '?')}` | `{e.get('surface', '?')}` | "
                f"{e.get('severity', '?')} | {days_str} | [{doc_path}]({doc_path}) |"
            )

    lines.append("")
    lines.append("_Generated by `scripts/security/decision_gate.py` (Phase 5 of the security automation roadmap)._")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the security decision-gate PR comment")
    parser.add_argument("--tracked-advisories", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    tracked = _load_json_safe(args.tracked_advisories)
    comment = render_comment(tracked)
    Path(args.output).write_text(comment)

    verdict = compute_verdict(tracked)
    print(f"Verdict: {verdict}")
    print(f"Comment written to {args.output}")


if __name__ == "__main__":
    main()
