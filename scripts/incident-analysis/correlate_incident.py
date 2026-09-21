"""
Correlate Sentry events, backend structured log lines, and audit_logs rows
by request_id (and, secondarily, by domain/entity id) to produce a
candidate root-cause timeline for an incident window.

Phase 3 of the 2026-09-19 security automation roadmap
(docs/audit/2026-09-19-security-automation-roadmap.md). Scaffolded against
MOCKED data because the Sentry MCP connector is not yet authorized for this
session -- see "Wiring in real Sentry data" below for exactly what changes
once it is. Nothing here talks to a network or a database; it only
transforms three lists of dicts already shaped like the real sources.

Join key: request_id. This is deliberate, not invented -- it's the same key
CLAUDE.md's Observability Conventions and utils/audit_logger.py's own
docstring already establish as shared between a request's log lines,
Sentry event tags, and its audit_logs row (utils/log_context.py's
ContextVar, populated by core/middleware.py's request-id middleware).
Correlating on it is joining data the codebase already agrees should be
joinable, not inventing a new cross-reference.

Expected input shapes (each a JSON array of objects):

  sentry_events.json — one object per Sentry event/issue, shaped like a
  subset of what `mcp__Sentry__search_events`/`search_issues` returns:
    {
      "id": str,
      "title": str,
      "level": "fatal" | "error" | "warning" | "info",
      "timestamp": ISO8601 str,
      "tags": {
        "domain": one of CLAUDE.md's Sentry domain tags (dispatch, payments,
                  auth, corporate, safety, drivers, rides, admin, ai),
        "surface": backend | rider-app | driver-app | admin,
        "env": production | staging | development,
        "request_id": str,       # may be absent on an older/untagged event
        "ride_id": str | None,   # PIPEDA-safe: an id, never PII
        "driver_id": str | None,
        "rider_id": str | None,
      },
    }

  log_lines.json — one object per structured backend log line:
    {"request_id": str, "level": str, "message": str, "timestamp": ISO8601 str, "module": str}

  audit_rows.json — one object per audit_logs row (utils/audit_logger.py shape):
    {"request_id": str | None, "action": str, "entity_type": str, "entity_id": str,
     "actor_id": str, "created_at": ISO8601 str}

Wiring in real Sentry data (once the connector is authorized): replace the
`_load_json_safe(args.sentry_events)` call in main() with a call to
`mcp__Sentry__search_events`/`search_issues` for the incident's time window,
mapped into the shape above -- correlate() itself does not change, since it
only depends on the shape, not the source. Same for log_lines (would come
from whatever log aggregation Spinr adopts) and audit_rows (a live
`get_rows("audit_logs", ...)` call). This module intentionally has zero
imports of backend/db_supabase or any Sentry SDK so it can be developed and
tested with mocked data today without those dependencies installed.

PII defense-in-depth: a Sentry event's `title` and a log line's `message`
are free text, not the bare-id tag convention CLAUDE.md's Observability
Conventions section governs. Both are commonly auto-derived from an
exception's `str(e)`, which can embed an email, phone number, exact
coordinate pair, or similar -- CLAUDE.md's own "~50 backend modules use
loguru" note documents a real failure mode where structured context/scrub
paths silently don't fire as intended. This module does NOT assume
upstream log-writing discipline caught every case; `_redact_free_text()`
below is applied to both fields before rendering. Do not remove it on the
theory that "logs are already scrubbed" -- a correlation/RCA tool is
exactly the tool most likely to run during the failure mode it would be
trusting didn't happen.

Output safety: `render_markdown()`'s output is meant for a short-retention
CI artifact or a local file a human reads, NOT for auto-committing to a
permanent repo path (unlike Phase 2's generate_security_summary.py, which
IS designed for that, via security-report.yml's auto-PR step). Do not wire
a future CI job to commit this report straight to docs/audit/ the way
Phase 2's report is committed -- redaction here is defense-in-depth, not a
guarantee, and a false negative in `_redact_free_text()` must not become
permanent, public repo history. If a future phase wants a committed
version, add a human-approval step between generation and commit, not just
copy the Phase 2 auto-commit pattern.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LEVEL_RANK = {"fatal": 4, "error": 3, "critical": 3, "warning": 2, "warn": 2, "info": 1, "debug": 0}

# Defense-in-depth redaction for free-text fields (Sentry event `title`,
# log line `message`) -- see the module docstring's "PII defense-in-depth"
# section for why this exists even though upstream is supposed to already
# scrub these. Order matters: email/phone before the generic digit-group
# pattern, so a phone number doesn't get partially eaten by the SIN-shaped
# pattern first.
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[redacted-email]"),
    # Known over-redaction, accepted as-is (safe direction): this also
    # matches a bare 10-digit run that isn't phone-shaped at all (a Unix
    # epoch timestamp, a numeric ride/order id) since word-boundary anchors
    # can't distinguish "phone number" from "any 10 digits" -- confirmed via
    # spinr-security-auditor review, non-blocking. A human reading the
    # report may see an unnecessary [redacted-phone] on a timestamp; that
    # never re-exposes anything, so it's left as-is rather than chasing a
    # more precise pattern that risks a false negative instead.
    (re.compile(r"\(?\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[redacted-phone]"),
    # {2,} (not {4,}): a casually-logged coordinate pair is not guaranteed
    # to carry full GPS precision -- catch 2-decimal pairs too rather than
    # only high-precision ones.
    (re.compile(r"-?\d{1,3}\.\d{2,}\s*,\s*-?\d{1,3}\.\d{2,}"), "[redacted-coordinates]"),
    (re.compile(r"\b\d{3}[-\s]\d{3}[-\s]\d{3}\b"), "[redacted-id-number]"),
]


def _redact_free_text(text: Any) -> str:
    """Best-effort redaction of a free-text field before it reaches a
    rendered report. Never a guarantee -- see the module docstring."""
    if not isinstance(text, str):
        return ""
    out = text
    for pattern, replacement in _REDACT_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _load_json_safe(path: Optional[str]) -> list[dict[str, Any]]:
    """Return the parsed JSON array, or [] if the path is unset, missing,
    or not valid JSON/not a list. Never raises -- a missing/broken input
    source must degrade this tool's own correlation, not crash it."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def correlate(
    sentry_events: list[dict[str, Any]],
    log_lines: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group all three inputs by request_id, and return one "incident
    cluster" per request_id that has at least one Sentry event, sorted by
    severity (highest Sentry event level first) then event count.

    A request_id with log lines/audit rows but no Sentry event is not
    surfaced as a cluster here -- this tool is for correlating AROUND a
    known Sentry event, not for finding new candidate incidents from logs
    alone (that's a different, noisier problem: most requests produce log
    lines and never need an incident review).
    """
    events_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in sentry_events:
        req_id = (e.get("tags") or {}).get("request_id")
        if req_id:
            events_by_req[req_id].append(e)

    logs_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in log_lines:
        req_id = line.get("request_id")
        if req_id:
            logs_by_req[req_id].append(line)

    audit_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audit_rows:
        req_id = row.get("request_id")
        if req_id:
            audit_by_req[req_id].append(row)

    clusters = []
    for req_id, events in events_by_req.items():
        events_sorted = sorted(events, key=lambda e: _parse_ts(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        top_level = max((str(e.get("level", "info")).lower() for e in events), key=lambda lv: LEVEL_RANK.get(lv, 0))
        related_logs = sorted(logs_by_req.get(req_id, []), key=lambda l: _parse_ts(l.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        related_audit = sorted(audit_by_req.get(req_id, []), key=lambda a: _parse_ts(a.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        domains = sorted({(e.get("tags") or {}).get("domain") for e in events if (e.get("tags") or {}).get("domain")})

        clusters.append(
            {
                "request_id": req_id,
                "top_level": top_level,
                "domains": domains,
                "sentry_events": events_sorted,
                "log_lines": related_logs,
                "audit_rows": related_audit,
            }
        )

    clusters.sort(key=lambda c: (LEVEL_RANK.get(c["top_level"], 0), len(c["sentry_events"])), reverse=True)
    return clusters


def _root_cause_hint(cluster: dict[str, Any]) -> str:
    """A best-effort, human-reviewed-required hint -- never a verdict. This
    is scaffolding for a human to start from, not an automated root-cause
    determination; CLAUDE.md's "escalate, don't silently ship" release gate
    applies here as much as to any other change."""
    n_errors = sum(1 for l in cluster["log_lines"] if str(l.get("level", "")).lower() in ("error", "critical"))
    n_audit = len(cluster["audit_rows"])
    if n_errors and n_audit:
        return f"{n_errors} ERROR log line(s) and {n_audit} audit action(s) in the same request -- check whether the audit action preceded or followed the error."
    if n_errors:
        return f"{n_errors} ERROR log line(s) in the same request, no audit trail entry -- likely a read path or an unauthenticated/system-triggered request."
    if n_audit:
        return f"{n_audit} audit action(s) in the same request, no ERROR-level log line -- Sentry event may be a warning-level anomaly rather than a hard failure."
    return "No correlated log lines or audit rows found for this request_id -- Sentry event may predate log_context instrumentation, or the request_id wasn't propagated."


def render_markdown(clusters: list[dict[str, Any]], window_label: str) -> str:
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Incident Correlation Report",
        "",
        f"**Generated:** {report_date}  ",
        f"**Window:** {window_label}  ",
        f"**Clusters found:** {len(clusters)}",
        "",
        "This report groups Sentry events, backend log lines, and audit_logs",
        "rows that share a request_id. It is a starting point for a human",
        "investigation, not an automated root-cause determination -- verify",
        "every hint against the actual code path before acting on it.",
        "",
    ]
    if not clusters:
        lines.append("_No Sentry events with a request_id tag found in this window._\n")
        return "\n".join(lines)

    for c in clusters:
        lines.append(f"## request_id `{c['request_id']}` — {c['top_level'].upper()}")
        if c["domains"]:
            lines.append(f"**Domains:** {', '.join(c['domains'])}")
        lines.append("")
        lines.append(f"**Root-cause hint:** {_root_cause_hint(c)}")
        lines.append("")
        lines.append(f"- Sentry events: {len(c['sentry_events'])}")
        for e in c["sentry_events"]:
            lines.append(f"  - `{e.get('timestamp', '?')}` [{e.get('level', '?')}] {_redact_free_text(e.get('title')) or '(no title)'}")
        lines.append(f"- Log lines: {len(c['log_lines'])}")
        for l in c["log_lines"][:10]:
            lines.append(f"  - `{l.get('timestamp', '?')}` [{l.get('level', '?')}] {l.get('module', '?')}: {_redact_free_text(l.get('message'))}")
        if len(c["log_lines"]) > 10:
            lines.append(f"  - ... and {len(c['log_lines']) - 10} more")
        lines.append(f"- Audit rows: {len(c['audit_rows'])}")
        for a in c["audit_rows"]:
            lines.append(f"  - `{a.get('created_at', '?')}` {a.get('action', '?')} {a.get('entity_type', '?')}/{a.get('entity_id', '?')} by {a.get('actor_id', '?')}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Correlate Sentry events, logs, and audit rows by request_id")
    parser.add_argument("--sentry-events", help="Path to sentry_events.json")
    parser.add_argument("--log-lines", help="Path to log_lines.json")
    parser.add_argument("--audit-rows", help="Path to audit_rows.json")
    parser.add_argument("--window-label", default="(unspecified)", help="Human-readable label for the time window covered")
    parser.add_argument("--output", required=True, help="Path for the rendered markdown")
    args = parser.parse_args()

    sentry_events = _load_json_safe(args.sentry_events)
    log_lines = _load_json_safe(args.log_lines)
    audit_rows = _load_json_safe(args.audit_rows)

    clusters = correlate(sentry_events, log_lines, audit_rows)
    report = render_markdown(clusters, args.window_label)
    Path(args.output).write_text(report)
    print(f"Report written to {args.output}")
    print(f"Clusters found: {len(clusters)}")


if __name__ == "__main__":
    main()
