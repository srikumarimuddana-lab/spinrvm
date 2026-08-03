# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review, round 2 — "$100k/minute" finding |

## 1. Issue / gap identified

`POST /admin/corporate-accounts/{company_id}/wallet/adjust` accepts up to
$100,000 per call (`AdjustRequest.amount` range `-100000.00`..`100000.00`)
with no limit on how many times the same admin can call it. A single
"finance"-role (or any admin-role) account — whether malicious or simply
compromised — could move an effectively unbounded amount of ledger balance
in minutes, with no daily cap and no second-approver requirement.

## 2. Root cause

The per-call bound (`$100,000`) was the only control ever added; nothing
tracked or limited *cumulative* calls by the same actor over time. This is
the same shape of gap as C2 (no floor on the allowance-debit path) — a
correct-looking single check that doesn't compose into a real limit once
the action can repeat.

## 3. Fix / remediation

Added a daily (UTC calendar day), per-admin, cumulative cap on
`/wallet/adjust` calls: before applying an adjustment, sum the `amount`
(absolute value — an admin inflating a wallet is exactly as much the risk
being capped as one draining it) of every `corporate_wallet_manual_adjust`
audit-log row for that admin since UTC midnight, and reject with `429` if
this call would push the total over the cap.

- New app_settings field `corporate_wallet_admin_adjust_daily_cap`
  (Decimal, optional) — configurable without redeploy, matching this
  codebase's "Settings in DB" convention. Falls back to a built-in
  `$50,000`/day default when unset, so the control is live by default
  rather than dark-shipped (this is closing a real, live money-movement
  gap, not adding new user-facing behavior — same posture as the C1–C3
  fixes earlier in this review).
- New admin-dashboard "Corporate Wallet Safeguards" card (Security tab,
  next to the H5 dual-approval-exports card it's modeled on) so the cap
  can actually be seen/changed without a direct SQL update.
- Explicitly scoped to a cumulative cap only — a full second-approver
  workflow (like H5's dual-approval-exports gate) is out of scope for this
  fix; the report's own suggested fix named either as acceptable ("a
  daily/cumulative cap **or** second-approval threshold").

## 4. Risk & impact on existing functionality

- **Blast radius: one function (`_check_daily_adjust_cap`, new), one call
  site (`manual_adjust` in `routes/corporate_wallet.py`).**
  `manual_topup` (the Stripe-backed path) is unaffected — it's already
  bounded per-call to $10,000 and requires a real Stripe charge to
  succeed, a materially different risk profile than `manual_adjust`'s
  unbacked ledger mutation, so it was left out of this cap by design.
- Grepped every caller of `manual_adjust`/the `/wallet/adjust` route:
  only the admin-dashboard's corporate wallet page calls it; no other
  backend code path.
- Under the cap (the overwhelming majority of real single adjustments),
  behavior is byte-for-byte unchanged — same response shape, same
  `apply_adjustment` call, same audit log write.
- Read path: one extra `get_rows("audit_logs", ...)` query per call,
  scoped to a single actor + action + `created_at >= today`, and
  `get_app_settings()` (already TTL-cached in-process) — negligible
  latency addition, well within this endpoint's non-critical-path budget
  (not one of the SLA-tracked paths in CLAUDE.md's Performance SLA table).
- `apply_adjustment` itself, its floor check, and the underlying
  `corporate_allowance_apply_delta`/wallet RPCs are untouched.

## 5. User-experience effect

**Internal admin-facing only.** An admin who tries to move more than the
configured daily total (default $50,000) via manual wallet adjustments now
gets a `429` with a clear message ("Daily admin wallet-adjustment cap of
$X exceeded ... Have a second admin process the remainder, or wait until
tomorrow (UTC)") instead of the call silently succeeding. No change for
any admin staying under the cap, which is expected to be the normal case.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_wallet.py` | New `_check_daily_adjust_cap` helper; called from `manual_adjust` before `apply_adjustment` | Close the unbounded-repeated-call gap |
| `backend/routes/admin/settings.py` | New `corporate_wallet_admin_adjust_daily_cap` field on `SettingsUpdateRequest`; `admin_update_settings` now converts `Decimal` → `float` at the DB write boundary | Make the cap configurable without redeploy |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | New "Corporate Wallet Safeguards" card with a numeric input for the cap | Give ops a way to see/change the cap without direct SQL |
| `backend/tests/test_corporate_wallet_routes.py` | 3 new tests (blocked over cap, allowed under cap, default-cap fallback); 1 existing test updated to mock the new `get_rows` call | Lock in the cap's block/allow/default behavior |

## 7. Before / after

```python
# Before
@router.post("/{company_id}/wallet/adjust")
async def manual_adjust(company_id, body, current_admin=Depends(get_admin_user)):
    ...
    result = await apply_adjustment(wallet_id=wallet["id"], amount=body.amount, ...)
```

```python
# After
@router.post("/{company_id}/wallet/adjust")
async def manual_adjust(company_id, body, current_admin=Depends(get_admin_user)):
    ...
    await _check_daily_adjust_cap(current_admin["id"], body.amount)
    result = await apply_adjustment(wallet_id=wallet["id"], amount=body.amount, ...)
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written beyond the
pre-existing audit-log row every adjustment already wrote. If the cap
itself is too aggressive in practice, it can be raised instantly via the
new settings field (no redeploy) rather than needing a code rollback at
all — that's the intended first response to a false-positive block, not
reverting the control entirely.

## 9. Verification performed

- [x] Added 3 new tests covering: blocked when a call would exceed the
      configured cap (summing prior rows via `abs()`), allowed when under
      the configured cap, and blocked against the built-in default when no
      app_settings value is configured. Updated 1 existing test
      (`test_manual_adjust_writes_audit_log`) to mock the new `get_rows`
      call so it isn't hitting the real (mocked-empty) DB path unmocked.
- [x] `python3 -c "import ast; ast.parse(...)"` on both touched Python
      files — clean.
- [x] Traced through `mock_supabase_client`'s default `execute()` behavior
      (`response.data = []`) to confirm the *other*, unmodified
      `manual_adjust` tests (which don't mock `get_app_settings`/`get_rows`)
      still pass under the new check: empty audit-log rows + no configured
      cap → falls back to the $50,000 default, and those tests' $25/$-25
      amounts stay well under it.
- [x] Blast-radius grep performed (see §4): confirmed `manual_topup` is a
      structurally different, already-bounded path and was correctly left
      out of scope.
- [ ] Did not run the test suite or any CI gate for this individual fix —
      per explicit instruction, tests/CI run once at the end of this
      second round, not per item.
- [ ] Did not run a real production build (`npm run build`) for the
      admin-dashboard change — reasoned through the existing `update()`
      pattern (loosely typed, `any`) and the established Input/Card
      component usage already in the same file; not visually verified.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, plus an instant
      no-redeploy mitigation (raise the setting) if the cap is too tight
- [x] Blast radius is stated, not assumed — one call site, `manual_topup`
      explicitly named and ruled out with reasoning
- [x] No silent behavior change to a working flow under the cap; the
      behavior change above the cap (a new 429) is the fix's entire
      purpose and is stated as such

## What was NOT verified

Not run against a live Postgres instance — the `audit_logs` query shape
(`actor_id` + `action` + `created_at >= today`) was checked against
`utils/audit_logger.py`'s actual write shape (confirmed `actor_id` and
`created_at` are real top-level columns, not nested in `details`) but not
executed against a real table. Not tested end-to-end in a browser — the
admin-dashboard card was reasoned about via the existing pattern for the
adjacent H5 card, not screenshotted; no visual-regression tooling exists
in this repo for that surface (a standing, previously-flagged gap, not
re-discovered here). The $50,000 default cap value is a judgment call, not
a number pulled from any documented Spinr policy — flagging explicitly in
case ops wants a different starting default (easy: it's a settings field,
changeable without a deploy).
