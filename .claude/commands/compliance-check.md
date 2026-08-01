# /compliance-check — SK Transportation Act + PIPEDA Compliance Audit

Delegate to the `spinr-regulatory-compliance-checker` agent to review driver
eligibility, retention carve-outs, tax line items, accessibility, and PII
logging in the current diff (or a named scope). When the diff touches the
AI/LLM surface, also dispatch `spinr-ai-guardrail-reviewer` in parallel (same
dual-dispatch pattern as `/review`'s safety row and `/security-check`'s AI
wiring) — the PIPEDA ban-list applies to provider-egress traffic exactly like
it applies to logs and analytics, but only `spinr-ai-guardrail-reviewer` has
the per-path context (each provider adapter, each tool module, the
persistence/cache sinks) to verify it's actually enforced there rather than
just checking that `scrub_pii` exists somewhere in the codebase.

## Usage

```
/compliance-check                # audits staged + unstaged changes
/compliance-check backend/utils/retention_purge.py   # audits a specific file
/compliance-check PR 123         # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` (this agent is **not** narrowly
     path-scoped — compliance issues can appear in any file, so unlike the
     other single-domain commands, don't pre-filter the diff before handing
     it to the agent)
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/regulatory-sk.md`
3. Dispatches the `spinr-regulatory-compliance-checker` subagent with the full scope
4. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — independent audits over the same diff, not a sequential pass
5. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## What gets checked

From the agent:

- **Driver eligibility** — re-verified at every `go_online`, not just onboarding
- **Retention carve-outs** — trip records (7yr), driver/vehicle linkage (7yr), GPS pickup/dropoff-only trace (3yr), insurance-period transitions (7yr) — PIPEDA deletion **cannot** override these
- **Tax line items** — GST/PST shown separately on receipts, never bundled
- **Accessibility** — WAV requests honored, service-animal accommodation mandatory, WCAG 2.1 AA
- **Driver classification language** — no employment-implying copy (mandatory shifts, uniforms, offline penalties)
- **PIPEDA data minimization** — no raw GPS/phone/name/email/SIN/exact-address in logs, Sentry, or analytics
- **Consent** — copy changes paired with a consent-version bump
- **Data residency** — no non-Canadian region config without legal sign-off

From `spinr-ai-guardrail-reviewer`, when dispatched:

- **PII scrubbing on every provider-egress path independently** — orchestrator (both live-stream and persisted-message sinks), each of the three provider adapters, every `tools_*.py` module, `mcp_server.py`, `response_cache.py` — verified per-path, not inferred from one clean grep hit
- **PIPEDA ban-list applied to provider payloads**, not just logs — raw GPS, full phone/name/email, gov IDs, exact addresses must never reach Anthropic/OpenAI/Gemini either

## Output

`spinr-regulatory-compliance-checker`'s report:

```
SPINR REGULATORY COMPLIANCE AUDIT — <scope>
============================================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS LEGAL REVIEW
```

If `spinr-ai-guardrail-reviewer` was also dispatched (AI-surface paths in
scope), its report follows under its own heading, verbatim — don't merge or
paraphrase the two reports into one:

```
SPINR AI GUARDRAIL AUDIT — <scope>
===================================
BLOCKERS ...
WARNINGS ...
OPEN BACKLOG TOUCHED ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS PRODUCT+LEGAL REVIEW
```

When both ran, the overall verdict is the worst of the two — never soften
one agent's verdict because the other came back clean (same rule `/review`
and `/security-check` use for their rollups).

## When to run

- Before any PR touching driver eligibility, retention/purge logic, receipts, accessibility, logging, or data-deletion flows
- Whenever unsure whether something is a compliance concern — this agent's whole point is that these issues aren't confined to one directory
- Before a release that changes signup/consent copy

## Do NOT

- Skip because the diff "doesn't look compliance-related" — this is exactly the class of bug that hides in an unrelated-looking diff (a debug log line, a new region config)
- Approve a retention-purge change without checking it against all 4 carve-out categories individually
- Auto-fix findings — the agent reports, humans decide the fix
