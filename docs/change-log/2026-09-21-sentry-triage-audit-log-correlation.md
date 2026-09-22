# Change Impact & Risk Log — Sentry Triage: Wire Real Audit-Log + Railway-Log Correlation

**Date:** 2026-09-21
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** infra/tooling (`.claude/agents/`, `ACTION_ITEMS.md`)
**Domain:** infra (observability/dev-tooling — not a rides/dispatch/payments/auth/corporate/safety code path, but a genuine production-data-access grant)
**Follow-up to:** #5649, #5667 (both merged)

## Issue/gap identified
`correlate_incident.py` supports joining Sentry events, backend log lines, and `audit_logs` rows by `request_id`, but PR #5667 explicitly left the log/audit half unwired — the investigator agent had no Supabase or Railway tool access. This left every "Correlated timeline" field as Sentry-only, never a real three-source picture.

## Root cause
Not a bug — deliberately deferred in #5667 as a separate connector-scoping decision, per this repo's own access-guardrail discipline (never grant new production-data access without a conscious, narrow, documented decision). This PR is that decision, made explicitly with the user via `AskUserQuestion` rather than assumed.

## Fix/remediation
1. Granted the investigator agent (`.claude/agents/spinr-sentry-triage-investigator.md`) `mcp__Supabase__execute_sql` and `mcp__Railway__get-logs`.
2. **Verified live before granting anything**: `mcp__Supabase__list_projects()` → exactly one project (`spinrmobileapp`, `soavhtdhefowwvforzwb`, `ca-central-1`). `mcp__Railway__list-projects()` → two projects; `list-services` confirmed `cooperative-harmony` contains the real `spinrvm` service, while `beautiful-harmony` is unrelated.
3. **Hardcoded the exact `project_id`/`projectId`/`serviceId` values into the agent's own instructions** (not left to the agent to look up or infer at runtime) — `soavhtdhefowwvforzwb` for Supabase, `086abfee-d144-4543-bfcd-6a7674340c07`/`8900d1be-705e-4cf3-9ce3-8193595eaad0` for Railway — with an explicit anti-pattern forbidding any other value.
4. **Accepted, documented risk (not silently shipped)**: the account-level Supabase connector is currently full read/write/admin to production (a prior, separate, time-boxed exception until 2026-10-31, unrelated to this change). Asked the user directly whether to narrow it now, accept the risk, or hold off; they chose to proceed on the existing connector, restricted only by the investigator's own prompt instructions (`SELECT`-only, `audit_logs`-only). This is explicitly a soft control, not a hard boundary, and is documented as such in both `connector-scoping.md` and this log.
5. Updated `.claude/context/connector-scoping.md` (new Railway row, Supabase addendum) in a separate, already-merged prerequisite PR (#5669) to avoid two follow-up PRs independently editing the same file.
6. Updated `ACTION_ITEMS.md` C129 with this decision and its reasoning.

## Risk & impact on existing functionality
- **Blast radius: the investigator agent's tool list and instructions, AND one downstream consumer of the new data they produce.** No application code, no schema change, no new secret. `correlate_incident.py` itself is unchanged (already supported all three inputs; only the caller's access changed) — but the investigator's new `correlated_timeline` field (populated from real audit/Railway content for the first time) is rendered verbatim into `docs/audit/sentry-triage/YYYY-MM-DD-weekly-report.md` via `scripts/observability/generate_sentry_weekly_report.py` (unchanged by this diff, but a real consumer of this diff's new data) — a **permanent, published repo artifact**, not just in-session output. This is why the PII-discipline fixes in this PR extend beyond the investigator's own instructions into `.claude/commands/sentry-triage.md`'s "Do NOT" list, which governs that report's content directly.
- **Real risk, explicitly accepted**: with the Supabase connector's current full-access state, a bug in the investigator's own prompt-following (not a tooling bug — an LLM failing to follow its own instructions) could in principle run something other than a `SELECT` against `audit_logs`. This is a real, non-zero risk the user was told about directly and chose to accept, not a risk this PR conceals.
- **Railway access is properly scoped at the connector level** (project/service IDs are the vendor's real scoping unit, per `connector-scoping.md`'s existing principle) — the `beautiful-harmony` project cannot be reached by a well-formed `get-logs` call using the hardcoded `cooperative-harmony` project ID, only by a malformed one, which the agent's instructions explicitly forbid attempting.
- **No other consumer of this agent or these connectors is affected** — this is additive tool access to one agent, not a change to how any other agent, script, or application code uses Supabase/Railway.

## User experience effect
None — internal tooling/agent-config change, no rider/driver/corporate-admin/internal-admin-facing surface touched.

## Files modified
| File | What changed | Why |
|---|---|---|
| `.claude/agents/spinr-sentry-triage-investigator.md` | Added `mcp__Supabase__execute_sql`/`mcp__Railway__get-logs` to `tools:`; rewrote the Correlate step to build real `audit_rows.json`/`log_lines.json` inputs with hardcoded project/service IDs; updated description, Output format field, and added 2 anti-patterns | Wire the previously-deferred correlation inputs |
| `ACTION_ITEMS.md` | C129 updated with this decision's reasoning | Keep the tracked item accurate |
| `docs/change-log/2026-09-21-sentry-triage-audit-log-correlation.md` | New — this file | Mandatory Change Impact Log |

## Before/after snippet
Before (`spinr-sentry-triage-investigator.md`):
```
- **`log_lines`/`audit_rows` are NOT wired in this pass** — ... you do not
  have DB or production-log tool access (deliberately — that's a separate
  connector-scoping decision, not made yet).
```
After:
```
- **Audit rows** — via `mcp__Supabase__execute_sql`. **Hardcoded, never vary
  these**: `project_id: "soavhtdhefowwvforzwb"` ... Query **only**
  `audit_logs`, **only** `SELECT` ...
- **Railway deploy logs** — via `mcp__Railway__get-logs`. **Hardcoded, never
  vary these**: `projectId: "086abfee-d144-4543-bfcd-6a7674340c07"` ...
```

## Rollback plan
`git-revert-safe` — reverting removes the two tool grants and restores the Sentry-only correlation behavior from #5667. No data was written by this change (it only grants read access going forward); no cleanup needed beyond the revert.

## Verification performed
- Live-verified Supabase (one project) and Railway (two projects, only one relevant) before writing any instruction referencing them — see connector-scoping.md PR #5669.
- Read `correlate_incident.py`'s documented input shapes directly (not from memory) to ensure the new instructions produce exactly the JSON shape it expects.
- Confirmed `mcp__Supabase__execute_sql`'s and `mcp__Railway__get-logs`'s real parameter schemas via `ToolSearch` before writing call instructions, rather than guessing parameter names.
- Explicit `AskUserQuestion` before granting any new access — the connector-scoping decision was made by the user, not assumed.
- **`spinr-security-auditor` review (round 1): FIX BLOCKERS.** Found 2 real blockers and 4 warnings, all fixed before commit:
  1. PII-discipline rule only covered Sentry payloads, not the new audit/Railway content flowing into the same `correlated_timeline` field that gets committed to a permanent, published weekly-report artifact — extended the rule (and `sentry-triage.md`'s own "Do NOT" list, since that file is the actual consumer that commits the report) to cover all three sources.
  2. The `SELECT`-only/`audit_logs`-only restriction didn't forbid a `JOIN`/subquery reaching a second table while remaining nominally "a SELECT against audit_logs" — added an explicit single-relation-only rule.
  3. No mandatory query `LIMIT` (only present in an example) — made explicit and mandatory, capped at 200.
  4. `audit_logs.details`/`.ip_address` (real, documented-unscrubbed-PII columns per `utils/audit_logger.py`) weren't explicitly forbidden — added an explicit column-level rule.
  5. Railway `types` wasn't locked to `["deploy"]` with the same weight as the hardcoded IDs — fixed.
  6. The audit query scoped by a bare time window rather than known `request_id`s — added a preferred, narrower `request_id = ANY(...)` form with the time-window form as fallback only.
  Verified each fix directly against the file content (not assumed from the review's own claims) before considering this closed.

## What was NOT verified
- **No live dry run** — the investigator has never actually called `execute_sql` or `get-logs` for a real issue; the first live run (daily/weekly Routine, or an on-demand invocation) will be the actual validation.
- **Whether the investigator reliably follows the `SELECT`-only/`audit_logs`-only instruction under real conditions** — this is a prompt-level control, not a technically-enforced one; its reliability is exactly the accepted risk documented above, not something a static review can fully verify.
- **Railway log line → `log_lines.json` field mapping fidelity** — Railway's raw log format may not map cleanly to `correlate_incident.py`'s expected `request_id`/`level`/`module` fields; the agent is instructed to leave fields blank rather than guess, but the practical quality of this mapping is unverified against a real log line.
