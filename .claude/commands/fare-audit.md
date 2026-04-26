# /fare-audit — Deep Fare & Payment Audit

Delegate to the `spinr-money-auditor` agent to review all money-touching code in the current diff (or a named scope).

## Usage

```
/fare-audit                  # audits staged + unstaged changes
/fare-audit backend/services/fare_service.py   # audits a specific file
/fare-audit PR 123           # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to money-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools

2. Loads context: `@.claude/context/domain-payments.md`

3. Dispatches the `spinr-money-auditor` subagent with the scope

4. Presents the agent's report to the user — no edits without explicit approval

## Money-relevant paths (auto-included when no args)

- `backend/services/fare_service.py`
- `backend/services/corporate_wallet_service.py`, `corporate_allowance_service.py`
- `backend/routes/payments.py`, `routes/wallet.py`, `routes/webhooks.py`, `routes/fares.py`
- `backend/utils/surge_engine.py`, `payment_retry.py`, `stripe_charge.py`
- `backend/migrations/*.sql` touching `ride_fare_breakdown`, `wallet_*`, `corporate_*`
- `shared/src/**/fare*`, `shared/src/**/payment*`
- Any file matching `*fare*`, `*payment*`, `*wallet*`, `*stripe*`

## Output

The agent's report:

```
SPINR MONEY AUDIT — <scope>
===========================
BLOCKERS ...
WARNINGS ...
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS FINANCE/LEGAL REVIEW
```

## When to run

- Before any PR that touches a money-relevant path
- Before a release that includes payment changes
- After a Stripe library upgrade
- On a cadence — e.g. monthly regression sweep of `fare_service.py`

## Do NOT

- Skip when the diff is "small" — one rogue `float()` is enough
- Auto-fix findings — the agent reports, humans decide the fix
