# /surge-check — Surge Pricing Audit

Delegate to the `spinr-surge-auditor` agent to review the auto-mode tier
table, the 2.5× hard cap, admin manual-override justification, and the
never-retroactive / never-on-corporate rules in the current diff (or a named
scope).

## Usage

```
/surge-check                     # audits staged + unstaged changes
/surge-check backend/utils/surge_engine.py   # audits a specific file
/surge-check PR 123              # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to surge-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/domain-payments.md` (surge section)
3. Dispatches the `spinr-surge-auditor` subagent with the scope
4. Presents the agent's report to the user — no edits without explicit approval

## Surge-relevant paths (auto-included when no args)

- `backend/utils/surge_engine.py`
- `backend/routes/admin/*surge*`
- Surge application inside `backend/services/fare_service.py`

## Output

The agent's report:

```
SPINR SURGE AUDIT — <scope>
============================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS BUSINESS+LEGAL REVIEW
```

## When to run

- Before any PR touching `surge_engine.py`, the admin surge-override endpoint, or surge application in fare calc
- Before raising the auto-mode tier table or cap — this needs an ADR link and documented business+legal review regardless of what the agent finds
- On a cadence — surge is the single most reputationally/regulatory-sensitive number in the fare calc

## Do NOT

- Skip because "it's just the admin override" — override paths are exactly where the >2.5× justification requirement gets forgotten
- Approve any change that raises `SURGE_CAP` without a business+legal sign-off note in the diff, regardless of what this command reports
- Auto-fix findings — the agent reports, humans decide the fix
