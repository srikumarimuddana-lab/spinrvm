# /security-check — Deep Security Audit

Delegate to the `spinr-security-auditor` agent to review auth, payments, RLS,
admin routes, JWT handling, OTPs, and PII exposure in the current diff (or a
named scope). When the diff touches the AI/LLM surface, also dispatch
`spinr-ai-guardrail-reviewer` in parallel (same dual-dispatch pattern as
`/review`'s safety row) — PII scrubbing on provider-egress paths and
prompt-injection resistance on state-mutating AI tools are security concerns
`spinr-security-auditor` doesn't have the AI-specific context to catch on its
own.

## Usage

```
/security-check                  # audits staged + unstaged changes
/security-check backend/routes/admin/auth.py   # audits a specific file
/security-check PR 123           # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to security-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Dispatches the `spinr-security-auditor` subagent with the scope, including
   the PR body if available (the agent cross-checks declared compliance
   boxes against the actual diff)
3. If the scope includes `backend/ai/**`, `backend/routes/ai.py`,
   `backend/routes/admin/ai_console.py`, or `rider-app/app/ai-assistant.tsx`,
   also dispatches `spinr-ai-guardrail-reviewer` **in parallel** with the same
   scope — don't run it sequentially after `spinr-security-auditor`, they're
   independent audits over the same diff
4. Presents both agents' reports to the user, each under its own heading —
   no edits without explicit approval

## Security-relevant paths (auto-included when no args)

- `backend/routes/auth.py`, `backend/routes/admin/auth*.py`
- `backend/routes/admin/**` (all admin routes)
- `backend/utils/crypto.py`, `backend/utils/rate_limiter.py`
- Any `*rls*.sql` / `*policy*.sql` migration
- `backend/core/middleware.py`
- Any file with `jwt`, `otp`, `token`, or `password` in a grep of the diff content
- Anywhere PII could reach a log line, Sentry event, or analytics payload
- `backend/ai/**`, `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`,
  `rider-app/app/ai-assistant.tsx` — triggers `spinr-ai-guardrail-reviewer`
  alongside `spinr-security-auditor` (see step 3 above)

## Output

`spinr-security-auditor`'s report:

```
SPINR SECURITY AUDIT — <scope>
==============================
BLOCKERS ...
WARNINGS ...
IMPACT MISMATCHES ...
INFO ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS HUMAN SECURITY REVIEW
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
uses for its rollup).

## When to run

- Before any PR that touches auth, payments, RLS, admin routes, JWT/OTP handling, or PII-adjacent code
- Before a release that includes an auth or admin-surface change
- After any Next.js/FastAPI middleware or dependency upgrade that could silently change auth enforcement (see CLAUDE.md's PR #310 middleware-naming incident — this is exactly the class of bug worth a fresh look after any framework bump)

## Do NOT

- Skip when the diff is "small" — one trusted-claim bug is enough
- Auto-fix findings — the agent reports, humans decide the fix
