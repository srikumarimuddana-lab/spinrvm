# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no portfolio-level view of corporate wallet risk" |

## 1. Issue / gap identified

There was no way to see wallet risk (negative balance, at/below the soft
floor, low balance with auto-topup off) across every corporate account at
once — an admin had to open each company's wallet page individually to
notice a problem.

## 2. Root cause

Pure gap, not a regression — `list_wallets_needing_autotopup` and
`list_wallets_low_balance_no_autotopup` already existed for the
*background loops* (auto-topup, low-balance nudge) to consume, but
nothing exposed the equivalent view to an admin.

## 3. Fix / remediation

- New repo function `list_wallet_risk_portfolio()` in
  `repositories/corporate_repo.py` — reuses the same "filter cross-column
  comparisons in Python" pattern the two existing background-loop
  helpers already use (PostgREST can't compare `balance` to a sibling
  threshold column server-side). Flags: `negative_balance`, `at_floor`
  (balance ≤ `soft_negative_floor`), `below_autotopup_threshold` /
  `low_balance_no_autotopup` (same threshold check as the two existing
  helpers, split by whether auto-topup is on). Company name/status
  resolved via a second `$in` query (CLAUDE.md's established pattern for
  a lookup spanning two tables), guarded against an empty-wallets no-op.
  Sorted riskiest first (any flag beats none, most-negative balance first).
- New `GET /admin/corporate-accounts/wallet-portfolio` in
  `routes/corporate_wallet.py`, same `require_module("corporate_accounts")`
  gate as every other endpoint in that router (C3 fix, earlier this
  review). Static path registered ahead of the dynamic
  `/{company_id}/wallet` route — different path shape (one segment vs
  two), so no collision, verified with a dedicated test.
- Admin-dashboard: a "N of M wallets flagged" card on the corporate
  accounts list page, each flagged company linking to its detail page,
  with the specific risk reason(s) on hover.

## 4. Risk & impact on existing functionality

- **Blast radius: one new repo function, one new read-only endpoint, one
  new UI card.** `list_wallets_needing_autotopup`,
  `list_wallets_low_balance_no_autotopup`, and every existing endpoint in
  `routes/corporate_wallet.py` are untouched.
- Grepped every caller of `repositories/corporate_repo.py`'s wallet
  helpers and every route in `routes/corporate_wallet.py` — the new
  function and endpoint are additive, nothing else references them yet.
- Read-only: no writes, no schema change, no migration.
- Two Supabase queries per call (`corporate_wallets` then, if non-empty,
  `corporate_accounts` filtered by `$in`) — same query-count shape as the
  existing background loops, not a new performance pattern.

## 5. User-experience effect

**Internal admin-facing only** (requires the `corporate_accounts` module
grant). Purely additive — a new summary card above the existing account
list; no change to any existing admin-facing behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/corporate_repo.py` | New `list_wallet_risk_portfolio()` | Compute risk flags across every wallet |
| `backend/db_supabase.py` | Re-export the new function (both dual-import branches) | Match the module's existing re-export convention |
| `backend/routes/corporate_wallet.py` | New `GET /wallet-portfolio` endpoint | Expose the portfolio view |
| `admin-dashboard/src/lib/api/corporate.ts` | New `getWalletRiskPortfolio` client function + types | Call the new endpoint |
| `admin-dashboard/src/lib/api.ts` | Re-export the new function/types | Match existing export pattern |
| `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` | New "wallets flagged" summary card | Surface the portfolio view |
| `backend/tests/test_corporate_db_helpers.py` | 4 new tests: empty case, negative/floor flags, low-balance-vs-autotopup-enabled flag split, sort order | Lock in the repo function's behavior |
| `backend/tests/test_corporate_wallet_routes.py` | 3 new tests: flagged count, empty case, no route collision with the dynamic `/{company_id}/wallet` path | Lock in the endpoint's behavior |

## 7. Before / after

```python
# Before — no cross-company wallet view existed

# After
async def list_wallet_risk_portfolio() -> List[Dict[str, Any]]:
    wallets = await run_sync(lambda: supabase.table("corporate_wallets").select("*").execute())
    ...  # flags computed in Python, company name/status resolved via $in
    return sorted_by_risk

@router.get("/wallet-portfolio")
async def get_wallet_risk_portfolio(current_admin=Depends(get_admin_user)):
    wallets = await list_wallet_risk_portfolio()
    return {"total_wallets": ..., "flagged_count": ..., "wallets": wallets}
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — purely additive
read function, endpoint, and UI card.

## 9. Verification performed

- [x] 4 new repo-level tests (empty wallets short-circuits the second
      query, negative-balance/at-floor flags, low-balance flag correctly
      split by `auto_topup_enabled`, sort order puts flagged and
      most-negative first) + 3 new route-level tests (flagged count in
      the response, empty-portfolio shape, no collision with the dynamic
      wallet route).
- [x] `python3 -c "import ast; ast.parse(...)"` on all 5 touched Python
      files — clean.
- [x] Bracket-balance check on all 3 touched `.ts`/`.tsx` files (no
      TS/JS toolchain run, per this round's instruction) — balanced.
- [x] Blast-radius grep performed (see §4): no existing caller of the
      touched repo functions or routes beyond what this change itself adds.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — every existing consumer of
      the touched files grepped and confirmed unaffected
- [x] No silent behavior change to a working flow — nothing existing was
      modified, only added

## What was NOT verified

Did not run `pytest`, `eslint`, `tsc --noEmit`, or a production build —
per this round's explicit instruction, deferred to a single pass at the
end. Did not run against a live Postgres instance — the two-query join
pattern was verified by structural comparison to the codebase's own
established convention (CLAUDE.md's name/email cross-table lookup rule),
not executed. Did not manually click through the new card in a browser —
reasoned through the existing `Card`/`Badge`/`Link` usage already proven
working elsewhere on the same page, rather than screenshotted; no
visual-regression tooling exists in this repo for this surface (a
standing, previously-flagged gap). The specific risk-flag thresholds
(negative balance, at/below floor, below auto-topup threshold) reuse
values and logic already established by the existing background-loop
helpers — not new judgment calls introduced by this change.
