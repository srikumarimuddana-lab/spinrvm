# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (session) |
| Surface(s) | backend (docs only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | claude/b12-corporate-coverage-runbook |
| Related issue or gap ID | ACTION_ITEMS.md B12, `reports/audits/2026-07-28-data-transfer-corporate-lifecycle-audit-v1.md` |

## 1. Issue / gap identified

No runbook exists for correcting a bad `corporate_wallet_apply_delta` /
`corporate_allowance_apply_delta` application. The only documented "rollback"
for these RPCs is dropping the Postgres function, which does not undo money
already moved through `corporate_wallets.balance` / `corporate_member_allowances.used`.

## 2. Root cause

The RPCs were built with reversible-migration rollback notes (`DROP FUNCTION`)
because that is the correct rollback for the *schema change* — but no one wrote
the separate procedure needed for the *data* once a bad delta has already been
applied through the (correctly deployed) function. Confirmed via the 2026-07-28
audit; not a guess.

## 3. Fix / remediation

Added `docs/runbooks/corporate-compensating-transaction.md`: a concrete,
testable procedure covering detection (ledger query + log/Stripe cross-check),
computing a target-balance compensating delta (explicitly not a blind
`-bad_amount` reversal, to account for legitimate deltas applied after the bad
one), applying the correction through the same locked RPC (never a raw
`UPDATE`), and a reconciliation query set to verify the fix. Also notes the KYB
storage-bucket RLS gap and the v2-deferred-scope pointer are out of scope here
and tracked in ACTION_ITEMS.md B12.

## 4. Risk & impact on existing functionality

No application code, migrations, or tests were touched — this commit is
docs-only (one new file). Blast radius: **isolated, no other callers** — the
runbook doesn't change any code path, RPC signature, or existing document.
Nothing reads this file programmatically.

## 5. User-experience effect

None. Not visible to riders, drivers, corporate admins, or internal admins —
it's an internal ops document for engineers/on-call handling a future incident.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/corporate-compensating-transaction.md` | New file | Fills the missing runbook gap identified in ACTION_ITEMS.md B12 |

## 7. Before / after

Not applicable — purely additive new document, no existing behavior changed.

## 8. Rollback plan

Delete the file / `git revert` this commit. Since no code or data path depends
on this document's existence, a plain revert is a complete and sufficient
rollback (the "not a rollback plan for live data" caveat in the template does
not apply here — nothing in this commit touches live data).

## 9. Verification performed

- [x] Reviewed against relevant CLAUDE.md conventions (money-delta handling,
  runbook house style modeled on `docs/runbooks/railway-fly-failover.md`)
- [x] Cross-checked the RPC bodies referenced (migrations 214, 258, 261) against
  the actual current definitions in `backend/migrations/` to keep the runbook's
  SQL and column names accurate
- [ ] Not applicable: no automated tests, no staging repro (docs-only change)

## What was NOT verified

- The runbook's SQL queries were checked for correctness against the schema in
  `backend/migrations/27_corporate_wallet_schema.sql` and the RPC bodies, but
  were **not run against a live/staging Supabase instance** — no throwaway
  corporate wallet data was available to execute the reconciliation queries
  end-to-end in this session.
- KYB document Storage bucket RLS/access scoping remains unverified (explicitly
  flagged in the runbook itself and in ACTION_ITEMS.md B12) — out of scope for
  this change per the task instructions.
