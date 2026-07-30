# /compliance-check — SK Transportation Act + PIPEDA Compliance Audit

Delegate to the `spinr-regulatory-compliance-checker` agent to review driver
eligibility, retention carve-outs, tax line items, accessibility, and PII
logging in the current diff (or a named scope).

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
4. Presents the agent's report to the user — no edits without explicit approval

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

## Output

The agent's report:

```
SPINR REGULATORY COMPLIANCE AUDIT — <scope>
============================================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS LEGAL REVIEW
```

## When to run

- Before any PR touching driver eligibility, retention/purge logic, receipts, accessibility, logging, or data-deletion flows
- Whenever unsure whether something is a compliance concern — this agent's whole point is that these issues aren't confined to one directory
- Before a release that changes signup/consent copy

## Do NOT

- Skip because the diff "doesn't look compliance-related" — this is exactly the class of bug that hides in an unrelated-looking diff (a debug log line, a new region config)
- Approve a retention-purge change without checking it against all 4 carve-out categories individually
- Auto-fix findings — the agent reports, humans decide the fix
