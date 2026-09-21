# Change Impact & Risk Log — Automated Sentry Error Triage (Discovery → Root Cause → Fix PR → Weekly Report)

**Date:** 2026-09-21
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** infra/tooling (`.claude/agents/`, `.claude/commands/`, `scripts/`, `.mcp.json`, `docs/`)
**Domain:** infra (observability/dev-tooling — not a rides/dispatch/payments/auth/corporate/safety code path)

## Issue/gap identified
No existing automation discovers Sentry errors, confirms root cause, ships a reviewed fix, and reports on it weekly. `backend/routes/admin/sentry.py` gives a human a live issue browser + resolve action, but nothing investigates or fixes on its own. The Sentry MCP connector (piloted 2026-09-08) was also left unscoped — no org/project slug was known, so `.mcp.json` stayed on the unscoped base URL as an explicit, flagged residual risk.

## Root cause
Not a bug fix — a new capability requested directly by the user ("create an agent for identify the sentry errors and address them via a PR..."), scoped and built to fit this repo's existing conventions rather than invent new ones.

## Fix/remediation — what shipped
1. **Connector scoping closed.** The user completed the Sentry OAuth login this session. Called `find_organizations()` → one org, `spinr-backend`. Called `find_projects(organizationSlug: "spinr-backend")` → one project, `crimson-smoke-7445` (consistent with `backend/routes/admin/sentry.py`'s TAG MODE — one project, `surface` tag fan-out, not one project per surface). `.mcp.json`'s `sentry` entry narrowed from the unscoped `https://mcp.sentry.dev` to `https://mcp.sentry.dev/mcp/spinr-backend/crimson-smoke-7445`. `.claude/context/connector-scoping.md`'s Sentry row updated from "piloting, scope unresolved" to "scoped and verified live."
2. **New read-only investigator agent** — `.claude/agents/spinr-sentry-triage-investigator.md`. Matches the exact frontmatter/body convention of the other 25 `.claude/agents/*.md` files (verified against `spinr-observability-reviewer.md` directly). Granted only read/analyze Sentry MCP tools (`find_organizations`, `find_projects`, `search_issues`, `search_events`, `analyze_issue_with_seer`, `get_sentry_resource`) — deliberately **not** granted `update_issue` or any write/mutate Sentry tool, keeping it consistent with every other agent in this repo's "audit, don't edit" posture.
3. **New orchestration command** — `.claude/commands/sentry-triage.md` (`/sentry-triage`). Runs the investigator, triages findings by confidence (`high` → implement; `medium` → confirm with the user first; `low` → report only, never implement), implements confirmed fixes with a guardrail (test/alert/metric/validation) in the same change, dispatches the domain-appropriate `spinr-*-reviewer` for adversarial review, ships one PR per issue with a full Change Impact Log, and generates the weekly report. **Never auto-merges anything** — every fix is a normal, human-reviewed PR, consistent with the existing Seer guardrail in `docs/audit/2026-09-08-agentic-tooling-atlas.md` and `connector-scoping.md`.
4. **New weekly report generator** — `scripts/observability/generate_sentry_weekly_report.py` + `test_generate_sentry_weekly_report.py` (9 tests). Stdlib-only, mirrors `scripts/security/generate_security_summary.py`'s conventions: tolerates missing/malformed input without crashing, distinguishes "no data" from "clean week" from "connector not authorized" (three different states that must never be conflated), and only ever renders fields the investigator agent's own schema provides (short-id, title, dates, counts, domain/surface tags, free-text fields the investigator wrote) — it has no path by which a raw Sentry payload/stacktrace could reach the committed report.
5. **`ACTION_ITEMS.md` C129** — new entry documenting this capability, explicitly cross-referencing C2 (still-open Sentry alert-rule gap, now carried forward weekly rather than re-discoverable) and correcting an initial mischaracterization of C55 (which is fully closed, not an open thread — verified by reading its full entry before citing it).

## Risk & impact on existing functionality
- **Blast radius:** new files only, plus two small edits (`.mcp.json`'s Sentry URL, `connector-scoping.md`'s Sentry row) and one `ACTION_ITEMS.md` addition. No application code (backend/rider-app/driver-app/admin-dashboard) touched.
- **Connector narrowing is strictly a risk reduction**, not a behavior change to any running system — it restricts which Sentry project this session's connector can reach; the admin dashboard's own `backend/routes/admin/sentry.py` uses its own separate `SENTRY_API_TOKEN`-based Web API client, entirely unaffected by this MCP connector's scope.
- **No new capability to auto-merge or auto-deploy anything** — the highest-consequence action this feature can take on its own is opening a draft/normal PR, which still requires a human to merge, same as every other PR in this repo.
- **Not yet live**: no GitHub Actions workflow and no scheduled Routine were created as part of this change — this ships the capability, not a running schedule. (GitHub Actions cannot reach an OAuth-connected MCP server, so the weekly cadence needs a Claude Code Routine, not a `.yml` cron — noted in `ACTION_ITEMS.md` C129 as the remaining step.)

## User experience effect
None — internal tooling/agent-config change, no rider/driver/corporate-admin/internal-admin-facing surface touched.

## Files modified
| File | What changed | Why |
|---|---|---|
| `.mcp.json` | Sentry connector URL narrowed from unscoped `https://mcp.sentry.dev` to `https://mcp.sentry.dev/mcp/spinr-backend/crimson-smoke-7445` | Close the flagged, unresolved scoping gap now that the org/project slug is known |
| `.claude/context/connector-scoping.md` | Sentry row updated from "piloting, scope unresolved" to "scoped and verified live 2026-09-21" | Keep the connector tracking table accurate |
| `.claude/agents/spinr-sentry-triage-investigator.md` | New read-only agent: Sentry issue discovery + root-cause investigation | Investigation step of the new triage loop |
| `.claude/commands/sentry-triage.md` | New `/sentry-triage` command: orchestrates discovery → triage → fix → review → PR → weekly report | Formalizes the full lifecycle the user asked for |
| `scripts/observability/generate_sentry_weekly_report.py` | New: renders the weekly Markdown report from structured JSON findings | Consistent, PII-safe weekly reporting |
| `scripts/observability/test_generate_sentry_weekly_report.py` | New: 9 unit tests for the report generator | Real coverage, not theater |
| `ACTION_ITEMS.md` | New entry C129; cross-reference note added to C2 | Track the new capability; avoid duplicating/rediscovering C2 |

## Before/after snippet
Before (`.mcp.json`):
```json
"sentry": {
  "type": "http",
  "url": "https://mcp.sentry.dev"
}
```
After:
```json
"sentry": {
  "type": "http",
  "url": "https://mcp.sentry.dev/mcp/spinr-backend/crimson-smoke-7445"
}
```

## Rollback plan
`git revert` — no data/schema/runtime state affected. Reverting restores the unscoped connector URL (re-introducing the previously-flagged residual risk) and removes the new agent/command/script files; no cleanup beyond the revert itself.

## Verification performed
- Live-verified the connector scoping via actual `find_organizations()`/`find_projects()` calls (not assumed) before narrowing `.mcp.json`.
- `python3 -m pytest scripts/observability/test_generate_sentry_weekly_report.py -v` — 9/9 passing.
- Matched the new agent file's frontmatter/body structure directly against `spinr-observability-reviewer.md`, `spinr-security-auditor.md`, `spinr-dispatch-reviewer.md`, and `spinr-tooling-hygiene-reviewer.md` (via a research subagent) before writing it.
- Corrected a factual error caught during self-review: C55 was initially miscited as an open thread by upstream research; verified by reading its full `ACTION_ITEMS.md` entry directly, confirmed CLOSED, and fixed all three places that had repeated the wrong claim (`ACTION_ITEMS.md` C129, `sentry-triage.md`, the investigator agent).
- Dispatching `spinr-security-auditor` and `spinr-tooling-hygiene-reviewer` for adversarial review before this is committed (see PR for their verdicts).

## What was NOT verified
- **No live dry run against real Sentry data was performed.** The investigator agent and `/sentry-triage` command are written and unit-tested at the report-generation layer only — the end-to-end loop (real issue → real Seer call → real root-cause confirmation → real fix → real PR) has not been executed even once. First live run will be the actual validation of the design.
- No Claude Code Routine (scheduled trigger) was created — this PR ships the capability, not a running weekly schedule. Setting that up is explicitly called out as the next step in `ACTION_ITEMS.md` C129.
- Did not verify whether `analyze_issue_with_seer` or any other Sentry MCP tool has hidden write/mutate side effects beyond what its name and description claim — took the tool descriptions at face value for the "read-only" tool-list decision. If a future session finds `analyze_issue_with_seer` (or any granted tool) has an undocumented write path, the investigator agent's tool list should be revisited.
