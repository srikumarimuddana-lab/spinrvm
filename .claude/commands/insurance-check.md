# /insurance-check — TNC Insurance Period Audit

Delegate to the `spinr-insurance-period-auditor` agent to review Period 0-3
classification, the append-only `driver_insurance_periods` audit trail, and
document-expiry gating on `go_online` in the current diff (or a named scope).

## Usage

```
/insurance-check                 # audits staged + unstaged changes
/insurance-check backend/routes/rides.py   # audits a specific file
/insurance-check PR 123          # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to insurance-period-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/regulatory-sk.md`, `@.claude/context/domain-safety.md`
3. Dispatches the `spinr-insurance-period-auditor` subagent with the scope
4. Presents the agent's report to the user — no edits without explicit approval

## Insurance-period-relevant paths (auto-included when no args)

- `backend/routes/rides.py` (any transition that crosses a period boundary)
- `backend/routes/drivers.py` (`go_online` eligibility checks)
- `backend/services/dispatch_service.py`
- Any diff touching `driver_insurance_periods` writes

## Output

The agent's report:

```
SPINR INSURANCE PERIOD AUDIT — <scope>
=======================================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS LEGAL/REGULATORY REVIEW
```

## When to run

- Before any PR touching ride state transitions, `go_online`, or insurance-period writes
- Before a release that changes document-expiry checking
- After any refactor of the ride state machine — period boundaries are derived from `ride.status`, so a state-machine change can silently break period classification even without touching insurance code directly

## Do NOT

- Accept "we'll backfill the period row later" — a missing row for even one trip is a coverage gap if a claim is filed
- Approve any schema change that allows mutation of historical `driver_insurance_periods` rows
- Auto-fix findings — the agent reports, humans decide the fix
