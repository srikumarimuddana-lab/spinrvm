# Change Impact & Risk Log — KYB decision / resubmit must not reopen a closed company

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/fix-kyb-decision-reopens-closed` (local, not pushed) — c7a15e4, b7a0192, b8e5a68, d36ab5d, e997f31, f472d2b |
| Related issue or gap ID | spinr-corporate-billing-reviewer finding; follow-up to PR #5793 (status compare-and-set, CORP-001) |

## 1. Issue / gap identified

`POST /api/admin/corporate-accounts/{id}/kyb-review` flipped a **closed** company back to `active` (approve) or `suspended` (reject). The wallet had already been wound down and the Stripe subscription cancelled, so this reopened a terminal account. On approve, the route also re-provisioned the wallet and Stripe customer and emailed the company "your account is approved". Found by the spinr-corporate-billing-reviewer.

## 2. Root cause

- `corporate_repo.record_kyb_decision` ran `UPDATE corporate_accounts SET status=… WHERE id=…` with no check on the current status. The `kyb_review` route called it with no pre-read. PR #5793 added a compare-and-set (CAS) to `change_company_status` only.
- The company-side resubmit (`POST /company/{id}/kyb/submit`) already refused a closed company. Its `_derive_kyb_state` returns `closed`, which is not in `_SUBMITTABLE_STATES`. But its `suspended → pending_verification` flip used `update_corporate_account_status` without `expected_status`. A close that lands between the read and the flip was therefore overwritten, and the company reopened as `pending_verification`.

## 3. Fix / remediation

- **Repository.** `record_kyb_decision` takes an optional `expected_status`. When it is set, the UPDATE also filters on `status = expected_status`. This mirrors `update_corporate_account_status` from #5793. It is additive: callers that omit it keep the old UPDATE.
- **`kyb_review` route.**
  - It reads the company first. A missing row returns 404.
  - A `closed` company returns **409** ("Corporate account is closed; a KYB decision cannot change its status."). Nothing is written, no decision is stamped, and no wallet, Stripe or email step runs.
  - Otherwise the route passes the status it read as `expected_status`. If the CAS loses, it re-reads and returns 409 ("status changed while this review was in flight (now 'X')"), or 404 if the row is gone.
- **KYB resubmit.**
  - The flip passes `expected_status=<status read>`. If the CAS loses and the re-read shows a different status, the route returns 409. If the status is unchanged, it still returns the existing 503.
  - The closed-company 409 now says the account is closed, instead of "Verification is already complete". The status code is unchanged.
- **Kill switch.** `corporate_kyb_refuses_closed_company` is added as a settings column by migration 478 and defaults to TRUE. Code reads it with `.get(..., True)`, so the guard is on even before the migration is applied. Setting it to false restores the old unconditional writes on both paths. `domain-corporate.md`'s flag convention requires a default-true kill switch when "the un-flagged behavior was the bug".
- **Decision on the audit trail for a refused closed-company decision: nothing is written.**
  - The "decision row" is the `kyb_reviewed_at`/`kyb_reviewed_by`/`kyb_last_decision`/`kyb_review_note` columns on the `corporate_accounts` row itself; there is no separate decisions table.
  - Stamping those columns on a closed account would overwrite the record of the last real review with a decision that had no effect. It would also leave `kyb_last_decision='rejected'` on a terminal row.
  - A refused request changes no state. It is logged at warning level, and the admin gets a 409.

**Alternative considered and rejected:** an atomic `UPDATE … WHERE status <> 'closed'` inside `record_kyb_decision`, with no pre-read. It has no race window, needs one fewer read, and would not have required updating the existing tests. It was rejected for two reasons. It only protects `closed`: two admins deciding the same KYB at once (approve vs reject) would still silently last-writer-win. It also departs from #5793's read-then-CAS pattern that `change_company_status` uses. KYB review is a rare admin action, so the extra read costs nothing that matters.

## 4. Risk & impact on existing functionality

Blast-radius grep: `record_kyb_decision`, `update_corporate_account_status`, `kyb-review`, `kyb_review` across `backend/` and `admin-dashboard/src`.

- `record_kyb_decision` has exactly one production caller, `routes/corporate_accounts.py::kyb_review`. The new parameter is keyword-only with a default, so every test that patches it with an `AsyncMock` is unaffected.
- `update_corporate_account_status` has two production callers. This change touches only one of them, `routes/corporate_company_kyb.py::kyb_submit`. The other, `change_company_status`, was already CAS-guarded by #5793 and is unchanged.
- **Behavior change on the `kyb_review` happy path:** one extra `get_corporate_account_by_id` read before the write. 15 existing tests in 6 files patched `record_kyb_decision` but not the pre-read. The pre-read hit the mocked client, came back `None` and returned 404, which broke those tests. They now mock the pre-read:
  - `test_corporate_kyb.py`
  - `test_corporate_stripe_customer.py`
  - `test_corporate_wallet_bootstrap.py`
  - `test_corporate_admin_routes.py`
  - `test_corporate_e2e_wallet.py`
  - `test_corporate_e2e_foundation.py` (its `side_effect` list gained a leading pre-read entry)

  No assertion was relaxed.
- **`kyb_review` now reads `get_app_settings()` before the write.** It already read it on the approve path. A settings failure now fails the request before any write, instead of after the status flip.
- **`kyb_submit` resubmit branch:** reads `get_app_settings()` before `set_kyb_document`. A CAS loss writes the new document key but leaves the status alone. That is harmless: the document is only viewable by staff, and the status is what gates access.
- **Not changed:**
  - A KYB **approve on a staff-suspended company** still reactivates it. This is an adjacent, pre-existing gap and is out of scope; it should be decided separately.
  - The admin `kyb-document` confirm route has no status guard. It changes no status.
- No interaction with background loops, the ride state machine, or wallet deltas. No money moves, and a refused review now also skips wallet and Stripe-customer provisioning.

## 5. User-experience effect

- **Internal admin (admin-dashboard corporate-account detail page, KYB approve/reject):**
  - On a closed company the admin now gets a 409 with a message, instead of the company silently flipping to active or suspended.
  - A review that races a status change now gets a 409 asking them to refresh.
  - The dashboard surfaces the error detail through its normal API-error path. No UI code changed.
- **Corporate admin (company portal):**
  - A closed company no longer gets an "approved" or "needs attention" email for a decision that should never have happened.
  - The portal already hides resubmit for `closed`, because `can_resubmit` is false. The new "account is closed" 409 is only reachable by a direct API call or a stale page.
  - A resubmit racing a close or suspend now gets a 409 instead of silently reopening the company.
- Nothing is visible mid-session to riders or drivers.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/corporate_repo.py` | `record_kyb_decision(..., expected_status=None)` filters on status when given | CAS primitive for the route |
| `backend/routes/corporate_accounts.py` | `kyb_review`: flag read, pre-read, closed → 409, CAS, loser → 409/404 | Core fix |
| `backend/routes/corporate_company_kyb.py` | Resubmit flip passes `expected_status`; loser → 409; `_not_submittable_detail()` names `closed`; dual-import `get_app_settings` | Race fix + clearer refusal |
| `backend/migrations/478_settings_corporate_kyb_refuses_closed_company.sql` | New `settings` column, default TRUE, rollback SQL in header | Kill switch without redeploy |
| `backend/tests/test_corporate_repo_guards.py` | +3 repo CAS tests | Regression |
| `backend/tests/test_corporate_kyb.py` | Pre-read mocks; +8 guard tests (closed approve/reject, open statuses, CAS loser 409, row-gone 404, kill switch off) | Regression |
| `backend/tests/test_corporate_company_kyb.py` | Updated flip-args assertion; +4 tests (closed submit, closed upload-url message, CAS loser 409, kill switch off) | Regression |
| `backend/tests/test_corporate_{stripe_customer,wallet_bootstrap,admin_routes,e2e_wallet,e2e_foundation}.py` | Mock the new pre-read | Keep existing coverage valid |

## 7. Before / after

```python
# Before — routes/corporate_accounts.py::kyb_review
row = await record_kyb_decision(company_id=normalized_id, reviewer_id=..., approved=..., note=...)
if not row:
    raise HTTPException(status_code=404, ...)
```

```python
# After
expected_status = None
if (await get_app_settings()).get("corporate_kyb_refuses_closed_company", True):
    current = await get_corporate_account_by_id(validated_id=normalized_id)  # None → 404
    expected_status = current.get("status")
    if expected_status == "closed":
        raise HTTPException(status_code=409, detail="Corporate account is closed; ...")
row = await record_kyb_decision(..., expected_status=expected_status)
if not row:  # CAS lost → re-read → 409 (status changed) / 404 (gone)
```

```python
# Before — routes/corporate_company_kyb.py::kyb_submit
flipped = await update_corporate_account_status(company_id, "pending_verification")
# After
flipped = await update_corporate_account_status(company_id, "pending_verification", expected_status=expected_status)
```

Concrete scenarios:
- **Closed company.** Company C was closed on Monday: wallet refunded, subscription cancelled. On Tuesday an admin clicks "Approve KYB" from a stale tab.
  - Before: C becomes `active`, a new wallet and Stripe customer are provisioned, and C receives an "approved" email.
  - After: 409, and C stays `closed` with nothing written.
- **Race with a close.** Company D is `suspended`/rejected. Its owner resubmits at the same moment an admin closes D.
  - Before: if the close commits between the submit's read and its flip, D ends up `pending_verification` and is back in the review queue.
  - After: the flip matches zero rows, the owner gets a 409, and D stays `closed`.

## 8. Rollback plan

- **Behavioural, no deploy.** Turn the flag off with `PUT /api/admin/settings` and `{"corporate_kyb_refuses_closed_company": false}`. It is admin-writable as of `3dbf734`, after the migration-reviewer follow-up. Alternatively run `UPDATE settings SET corporate_kyb_refuses_closed_company = false WHERE id = 'app_settings';`. Both routes then skip the pre-read and CAS and write unconditionally, exactly as before. The settings cache TTL is 60 seconds.
- **Schema.** Run `ALTER TABLE settings DROP COLUMN IF EXISTS corporate_kyb_refuses_closed_company;`. It is safe with the code still deployed, because the code defaults to true when the column is absent.
- **Data.** Nothing to remediate. The fix only refuses writes; it never writes new data.

## 9. Verification performed

- [x] **Automated tests.** Ran `pytest -k corporate --ignore=tests/rls`: 1093 passed, 3 skipped (baseline before the change was 1078 passed). The 15 new tests all use `mock_supabase_client` or route-level `AsyncMock` patches.
- [x] **Red check.** The new route tests were run against the pre-fix route files: 8 of the `kyb_review` tests and 5 of the `kyb_submit` tests failed. Then they were run against the fixed files: all pass.
- [x] **Coverage (`--cov`, corporate tests).**
  - `routes/corporate_accounts.py`: 96%
  - `routes/corporate_company_kyb.py`: 97%. The only uncovered lines are the relative-import branch of the dual-import.
  - `repositories/corporate_repo.py`: 90%
- [x] **Lint.** `ruff check` and `ruff format --check` are clean on every touched file, and the pre-commit hook passed on each commit.
- [x] **Blast-radius grep.** Listed in §4.
- [x] **Feature flag.** Default-true kill switch per `domain-corporate.md`.
- [ ] **Staging / manual repro.** Not done.
- [ ] **Migration against real Postgres.** Not run.

`tests/rls/` was excluded: that tier needs a real Postgres and errors without one. It is unrelated to this change.

## 10. What was NOT verified

- Not tested against live or staging Supabase; tests used mocked clients only. The CAS relies on PostgREST applying both `.eq()` filters to the UPDATE. #5793 makes the same assumption for `update_corporate_account_status`.
- Migration 478 was not applied anywhere, including `--dry-run`, because no `DATABASE_URL` was available. It was renumbered from 477, which `claude/fix-admin-money-action-caps` already uses (`477_disputes_resolution_columns.sql`). The highest number on `origin/main` is 473, and 474–477 are held by in-flight PRs. CHECK B will flag a collision if one lands first.
- The admin-dashboard rendering of the new 409 detail was not exercised. No frontend code changed, and no production build was run because there is no frontend diff.
- No `spinr-*` reviewer agent was run on the final diff (CLAUDE.md gate 10), because this session had no Agent tool. Recommended before merge: `spinr-corporate-billing-reviewer` and `spinr-migration-reviewer`.
- Out of scope and still open: a KYB approve on a **staff-suspended** company still reactivates it.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
