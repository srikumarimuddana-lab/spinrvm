# Change Impact & Risk Log — Incident Correlation Scaffold (Phase 3, mocked data)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** scripts/ (new, standalone — no application code touched)
**Domain:** observability / security automation
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 3

## Issue/gap identified
No tooling correlates a Sentry event, the backend log lines from the same
request, and the audit trail entry it may have triggered — RCA today is a
human manually cross-referencing three different sources by hand.

## Root cause
Never built, and genuinely blocked on the Sentry MCP connector not being
authorized for this session (per the roadmap doc's Phase 3 entry).

## Fix/remediation
Scaffolded the correlation *logic* now, against mocked JSON fixtures shaped
like the real sources, so it's ready the moment Sentry is authorized:
- `scripts/incident-analysis/correlate_incident.py` — joins Sentry events,
  log lines, and audit_logs rows by `request_id` (the same join key
  CLAUDE.md's Observability Conventions and `utils/audit_logger.py` already
  establish as shared across these three sources), ranks clusters by
  severity, and renders a Markdown timeline with a human-reviewed-required
  root-cause hint.
- `scripts/incident-analysis/test_correlate_incident.py` — 27 tests against
  synthetic fixtures.

No network calls, no `backend/db_supabase` or Sentry SDK imports — this is
a pure data-transformation module today. The module's own docstring
documents exactly what changes once Sentry is authorized (swap the mocked
JSON load for a `mcp__Sentry__search_events` call; `correlate()` itself
doesn't change).

## Risk & impact on existing functionality
- **Blast radius: isolated.** New standalone scripts, zero application-code
  changes, not wired into any CI workflow yet (deliberate — there's no real
  trigger source until Sentry is authorized).
- **Security review (`spinr-security-auditor`) found 3 real design gaps on
  first pass**, since this module renders free-text fields (Sentry event
  `title`, log line `message`) that could carry PII if upstream scrubbing
  ever fails — a real risk for a tool whose whole purpose is investigating
  what goes wrong when upstream assumptions fail:
  1. No redaction on `title` — fixed with a defense-in-depth
     `_redact_free_text()` pass (email/phone/coordinate/SIN-shaped patterns).
  2. No redaction on `message`, blindly trusting upstream log-scrubbing
     discipline — same fix applied.
  3. No guardrail against a future CI job copying Phase 2's auto-commit-to-
     `docs/audit/` pattern for this report — fixed with an explicit
     docstring "Output safety" section naming the Phase 2 pattern and
     requiring a human-approval step before any future committed version.
  All 3 confirmed closed on re-review (**SAFE TO MERGE**). Two minor,
  explicitly-accepted regex precision notes remain (phone pattern can
  over-redact a bare 10-digit non-phone number like a Unix timestamp;
  documented in-code as a deliberate safe-direction tradeoff) — not gaps
  that reopen a leak.

## User experience effect
None — no rider/driver/corporate-admin/internal-admin-facing change.
Standalone script, run manually or (once Sentry is wired in) from a future
CI job; not merged into any user-facing path.

## Files modified
| File | What changed | Why |
|---|---|---|
| `scripts/incident-analysis/correlate_incident.py` | New file | Correlation logic + redaction |
| `scripts/incident-analysis/test_correlate_incident.py` | New file, 27 tests | Coverage incl. PII-redaction regression tests |

## Before/after snippet
Before: no correlation tooling existed.
After (the security-critical piece — redaction before render):
```python
def _redact_free_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    out = text
    for pattern, replacement in _REDACT_PATTERNS:
        out = pattern.sub(replacement, out)
    return out
```

## Rollback plan
`git revert` is a complete rollback — standalone scripts with no persisted
state, not wired into CI, not imported by any application code.

## Verification performed
- `pytest scripts/incident-analysis/test_correlate_incident.py -v` — 27/27
  pass.
- Manual CLI smoke test end-to-end against synthetic Sentry/log/audit
  fixture files — confirmed correct grouping, severity ranking, and
  root-cause hint text.
- `spinr-security-auditor` agent run (round 1): found 3 real gaps (detailed
  above). Fixes applied.
- `spinr-security-auditor` agent run (round 2), re-verifying the fixes
  against live file content and a fresh independent test run: **SAFE TO
  MERGE**, all 3 blockers confirmed closed; two minor regex precision notes
  accepted as documented, safe-direction tradeoffs.

## What was NOT verified
- Never run against real Sentry/log/audit data — genuinely blocked on the
  Sentry MCP connector authorization (a user action, not something this
  session can do). The module's shape assumptions (tag names, field names)
  are drawn from CLAUDE.md's own documented conventions, not from a live
  API response — verify against the real `search_events`/`search_issues`
  response shape when wiring this in for real, in case it differs from what
  CLAUDE.md documents.
- The redaction patterns are explicitly best-effort, not a guarantee — see
  the module's own "PII defense-in-depth" docstring section. Do not treat
  this as a substitute for upstream log-writing discipline; it's a second
  layer, not the only layer.
- No CI wiring exists yet — this is scaffolding only, not an active
  pipeline. Phase 3 is not "done"; it's unblocked-and-ready for the moment
  Sentry access lands.
