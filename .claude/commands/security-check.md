# /security-check — Deep Security Audit

Delegate to the `spinr-security-auditor` agent to review auth, payments, RLS,
admin routes, JWT handling, OTPs, and PII exposure in the current diff (or a
named scope).

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
3. Presents the agent's report to the user — no edits without explicit approval

## Security-relevant paths (auto-included when no args)

- `backend/routes/auth.py`, `backend/routes/admin/auth*.py`
- `backend/routes/admin/**` (all admin routes)
- `backend/utils/crypto.py`, `backend/utils/rate_limiter.py`
- Any `*rls*.sql` / `*policy*.sql` migration
- `backend/core/middleware.py`
- Any file with `jwt`, `otp`, `token`, or `password` in a grep of the diff content
- Anywhere PII could reach a log line, Sentry event, or analytics payload

## Output

The agent's report:

```
SPINR SECURITY AUDIT — <scope>
==============================
BLOCKERS ...
WARNINGS ...
IMPACT MISMATCHES ...
INFO ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS HUMAN SECURITY REVIEW
```

## When to run

- Before any PR that touches auth, payments, RLS, admin routes, JWT/OTP handling, or PII-adjacent code
- Before a release that includes an auth or admin-surface change
- After any Next.js/FastAPI middleware or dependency upgrade that could silently change auth enforcement (see CLAUDE.md's PR #310 middleware-naming incident — this is exactly the class of bug worth a fresh look after any framework bump)

## Do NOT

- Skip when the diff is "small" — one trusted-claim bug is enough
- Auto-fix findings — the agent reports, humans decide the fix
