# /corporate-check — Corporate Billing & Wallet Audit

Delegate to the `spinr-corporate-billing-reviewer` agent to review
`corporate_wallet_apply_delta` callers, allowance-cap enforcement, payment-source
priority, and idempotency in the current diff (or a named scope).

## Usage

```
/corporate-check                 # audits staged + unstaged changes
/corporate-check backend/services/corporate_wallet_service.py   # audits a specific file
/corporate-check PR 123          # audits a GitHub PR's diff
```

## What it does

1. Scopes the audit:
   - No args → `git diff --cached` + `git diff` filtered to corporate-relevant paths
   - Path args → those files
   - `PR N` → pulls the PR diff via the GitHub MCP tools
2. Loads context: `@.claude/context/domain-corporate.md`, `@.claude/context/domain-payments.md`
3. Dispatches the `spinr-corporate-billing-reviewer` subagent with the scope
4. **Cascade-effect reminder** (from `domain-corporate.md`'s documented lessons
   learned — three separate integration gaps shipped this exact way): if the
   diff touches `routes/rides/booking.py`'s two independent corporate booking
   paths (`company_allowance` and `work_profile`), explicitly confirm the fix
   was applied to **both** blocks, not just the one that prompted the change
5. Presents the agent's report to the user — no edits without explicit approval

## Corporate-relevant paths (auto-included when no args)

- `backend/routes/corporate*.py`
- `backend/services/corporate_*.py`
- `backend/routes/wallet.py`
- `backend/routes/rides/booking.py` (the two corporate booking paths inside `create_ride`)
- `backend/utils/allowance_reset.py`, `backend/utils/corporate_low_balance.py`
- Any caller of `corporate_wallet_apply_delta` anywhere in the codebase, not just the diff

## Output

The agent's report:

```
SPINR CORPORATE BILLING AUDIT — <scope>
========================================
BLOCKERS ...
WARNINGS ...
BLAST RADIUS  (other callers of any shared function touched)
VERIFIED ...
VERDICT: SAFE TO MERGE / FIX BLOCKERS / NEEDS FINANCE REVIEW
```

## When to run

- Before any PR touching corporate accounts, membership, wallet, or allowance code
- Before any lifecycle change (company suspend/close, member offboarding, policy edit) — cross-check against the cascade-effect table in `domain-corporate.md`
- Before a change to `corporate_wallet_apply_delta`'s signature or locking behavior — this is a shared money primitive, list every consumer

## Do NOT

- Accept "just this once" for a direct wallet UPDATE, even in a migration/backfill script
- Approve a payment-source-order change without an explicit product/finance sign-off note
- Auto-fix findings — the agent reports, humans decide the fix
