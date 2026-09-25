# Change Impact & Risk Log — KYB decision / resubmit must not reopen a closed company

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/fix-kyb-decision-reopens-closed` (local, not pushed) — c7a15e4, b7a0192, b8e5a68, d36ab5d, e997f31, f472d2b, c6cdc9e (renumber to 478), 3dbf734 (admin-writable flag), 15347a9, 7a02e87, 3dd711f (staff-suspension guard, §3a) |
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

## 3a. Staff-suspension guard (owner decision, 2026-09-25)

**Owner decision (via AskUserQuestion):** a KYB approval must **not** reactivate a company that staff suspended for a reason unrelated to KYB. Only a suspension caused by a KYB rejection may be lifted by a KYB approval. It sits behind the same kill switch `corporate_kyb_refuses_closed_company`; turning the flag off restores the old behaviour.

**Rule used to detect a staff suspension:** the company portal's own `derive_kyb_state` (`routes/corporate_company_kyb.py`), reused rather than re-derived. `status == 'suspended'` with `kyb_last_decision != 'rejected'` is a staff suspension; with `kyb_last_decision == 'rejected'` it is a KYB suspension.
- The `change_company_status` audit row was considered and rejected as a signal. That audit write is best-effort (a failure is logged and swallowed), so a missing row proves nothing.
- No `corporate_accounts` column records who suspended a company or why.

**Behaviour on a staff-suspended company (flag on):**
- **Approve:**
  - The review is recorded: `kyb_reviewed_at`, `kyb_reviewed_by`, `kyb_last_decision='approved'`, the note, and the admin audit row.
  - Status stays `suspended`. The write omits `status` and is compare-and-set on `status='suspended'`.
  - The response carries `status_unchanged: "staff_suspension"`.
- **Reject:**
  - Status stays `suspended`, as before.
  - The review stamp is written, but `kyb_last_decision` is **not** set to `'rejected'`. That value is what makes the portal treat a suspension as KYB-caused and resubmittable. Setting it would let the company resubmit (`suspended → pending_verification`) and then be approved to `active`, a two-step way around the owner's rule.
- **Decision email:** skipped for staff-suspended companies. Both templates would be false there: the approve email says "your account is now active", and the reject email says "sign in and resubmit" while the portal tells a staff-suspended company to contact support. A correct third template would be new notification copy, so it was left out of this change.
- **Wallet / Stripe customer provisioning on approve: still runs.** The coordinator suggested deferring it until reactivation, but nothing on the reactivation path (`change_company_status`) provisions a wallet. KYB approval is the only place a `corporate_wallets` row is ever created. Deferring would leave a pending-then-staff-suspended company that is later reactivated `active` with no wallet. Running it now is safe:
  - `ensure_corporate_wallet` is idempotent and creates a zero balance with auto top-up off (the column default).
  - A Stripe customer is created, but never charged.
  - A suspended company cannot top up manually, because the wallet endpoints check status.

**Before / after (flag on):**

| Company state at review | Decision | Before | After |
|---|---|---|---|
| `pending_verification` | approve | `active` + email | unchanged |
| `suspended`, `kyb_last_decision='rejected'` (KYB-suspended) | approve | `active` + email | unchanged |
| `suspended`, `kyb_last_decision` NULL/`approved` (staff-suspended) | approve | **`active`**, wallet, "now active" email | stays `suspended`, decision stamped, wallet ensured, no email, `status_unchanged='staff_suspension'` |
| staff-suspended | reject | `suspended`, `kyb_last_decision='rejected'` (now portal-resubmittable), "resubmit" email | stays `suspended`, review stamped, `kyb_last_decision` untouched, no email |
| any, flag **off** | either | old behaviour | old behaviour (no pre-read) |

**Known misclassification edge.** Consider a company that was KYB-rejected, then reactivated by staff, then suspended again by staff. It still carries `kyb_last_decision='rejected'`, so it looks KYB-suspended and a KYB approval would lift it.
- This is the same classification the portal already uses, so it is no worse than today's resubmit rule.
- Closing it properly needs a durable "suspended by" field, or clearing `kyb_last_decision` on staff status changes. That touches `change_company_status` and is left as a follow-up.

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
  - ~~A KYB approve on a staff-suspended company still reactivates it.~~ Closed in §3a (owner decision).
- **§3a blast radius:**
  - `record_kyb_decision(preserve_status=...)` is a new keyword with default False; its only caller is `kyb_review`.
  - `corporate_accounts.py` now imports `derive_kyb_state` from `corporate_company_kyb.py` (dual-import). That module does not import `corporate_accounts`, so there is no cycle.
  - `KYBReviewResponse` gains an optional `status_unchanged` field, which is additive. The admin dashboard's only `reviewKyb` caller is `kyb-queue/page.tsx`, which now reads that field (see §5).
  - The admin `kyb-document` confirm route has no status guard. It changes no status.
- No interaction with background loops, the ride state machine, or wallet deltas. No money moves, and a refused review now also skips wallet and Stripe-customer provisioning.
- **§3a wallet dependency.** An approval on a staff-suspended company still provisions the wallet and Stripe customer, because only this path creates the wallet row. The company still cannot book: `require_company_bookable` (`services/corporate_policy_service.py`) refuses any non-`active` company. That block has its own kill switch, `corporate_inactive_company_blocks_booking` (default on), and a code comment at the provisioning call site now records the dependency.

## 5. User-experience effect

- **Internal admin (admin-dashboard corporate-account detail page, KYB approve/reject):**
  - On a closed company the admin now gets a 409 with a message, instead of the company silently flipping to active or suspended.
  - A review that races a status change now gets a 409 asking them to refresh.
  - The dashboard surfaces the error detail through its normal API-error path. No UI code changed.
- **Corporate admin (company portal):**
  - A closed company no longer gets an "approved" or "needs attention" email for a decision that should never have happened.
  - The portal already hides resubmit for `closed`, because `can_resubmit` is false. The new "account is closed" 409 is only reachable by a direct API call or a stale page.
  - A resubmit racing a close or suspend now gets a 409 instead of silently reopening the company.
- **Internal admin: KYB decision on a staff-suspended company (§3a).**
  - The API returns 200 with `status: "suspended"` and `status_unchanged: "staff_suspension"`. The company stays suspended on its detail page until staff reactivate it through the status control.
  - The KYB queue page (`kyb-queue/page.tsx`) lists only `pending_verification` companies, so this path is reached only when staff suspend a company while it sits in the queue, or through a direct API call.
  - In that case the queue now shows a toast: "Decision recorded — company still suspended", telling the admin to reactivate it from its account page if appropriate. Before, the admin saw a silent success and the row just left the queue.
  - The toast is new copy on an internal admin screen only. It appears only when the backend returns `status_unchanged`, so every other approve/reject looks exactly as before.
- **Corporate admin: staff-suspended company (§3a).** No KYB decision email; the portal keeps showing "suspended — contact support".
  - Open product question: on a rejection, the reviewer's note is stored and visible to staff, but the portal never shows it to a staff-suspended company, because the portal shows the note only in the `rejected` state. A company with a fixable document problem therefore gets no specific feedback until staff reactivate it. This follows the portal's existing rule and is not a data loss.
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
| `admin-dashboard/src/lib/api/corporate.ts` | `reviewKyb` response type gains optional `status_unchanged` | Type the new backend field |
| `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` | Toast when approve/reject returns `status_unchanged: "staff_suspension"` | Don't show a silent success when the company stays suspended |

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

- **Behavioural, no deploy.** Turn the flag off with `PUT /api/admin/settings` and `{"corporate_kyb_refuses_closed_company": false}`. It is admin-writable as of `3dbf734`, after the migration-reviewer follow-up. Alternatively run `UPDATE settings SET corporate_kyb_refuses_closed_company = false WHERE id = 'app_settings';`. Both routes then skip the pre-read and CAS and write unconditionally, exactly as before. That also restores KYB approval lifting a staff suspension (§3a). The settings cache TTL is 60 seconds.
- **Schema.** Run `ALTER TABLE settings DROP COLUMN IF EXISTS corporate_kyb_refuses_closed_company;`. It is safe with the code still deployed, because the code defaults to true when the column is absent.
- **Data.** Nothing to remediate. The fix only refuses or narrows writes. The only data §3a writes that the old code would not is a `kyb_last_decision='approved'` stamp on a company that stays suspended. That stamp does not change the company's portal state, which remains `suspended`.

## 9. Verification performed

- [x] **Automated tests.** Ran `pytest -k corporate --ignore=tests/rls`: 1093 passed, 3 skipped (baseline before the change was 1078 passed). The 15 new tests all use `mock_supabase_client` or route-level `AsyncMock` patches.
- [x] **§3a tests.** After the `origin/main` merge, `pytest -k "corporate or kyb" --ignore=tests/rls` gave 1116 passed, 3 skipped. That run includes 2 new repo tests and new route tests for staff-suspended approve (×2 prior-decision values), staff-suspended reject, flag-off lifting a staff suspension, and pending, active and KYB-suspended approval going to `active`. Against the previous route, all the staff-suspension tests failed.
- [x] **Red check.** The new route tests were run against the pre-fix route files: 8 of the `kyb_review` tests and 5 of the `kyb_submit` tests failed. Then they were run against the fixed files: all pass.
- [x] **Coverage (`--cov`, corporate tests).**
  - `routes/corporate_accounts.py`: 96%
  - `routes/corporate_company_kyb.py`: 97%. The only uncovered lines are the relative-import branch of the dual-import.
  - `repositories/corporate_repo.py`: 90%
- [x] **Lint.** `ruff check` and `ruff format --check` are clean on every touched file, and the pre-commit hook passed on each commit.
- [x] **Blast-radius grep.** Listed in §4.
- [x] **Admin dashboard.** After `npm ci`, `npx tsc --noEmit` was clean and a real production build (`npm run build`) succeeded, including `/dashboard/corporate-accounts/kyb-queue`. ESLint on the two changed files shows 0 errors and 1 warning (`react-hooks/set-state-in-effect` on the page's existing `load()` effect). `main`'s copy of the page gives the same warning.
- [x] **Feature flag.** Default-true kill switch per `domain-corporate.md`.
- [ ] **Staging / manual repro.** Not done.
- [ ] **Migration against real Postgres.** Not run.

`tests/rls/` was excluded: that tier needs a real Postgres and errors without one. It is unrelated to this change.

## 10. What was NOT verified

- Not tested against live or staging Supabase; tests used mocked clients only. The CAS relies on PostgREST applying both `.eq()` filters to the UPDATE. #5793 makes the same assumption for `update_corporate_account_status`.
- Migration 478 was not applied anywhere, including `--dry-run`, because no `DATABASE_URL` was available. It was renumbered from 477, which `claude/fix-admin-money-action-caps` already uses (`477_disputes_resolution_columns.sql`). The highest number on `origin/main` is 473, and 474–477 are held by in-flight PRs. CHECK B will flag a collision if one lands first.
- The admin-dashboard rendering of the new 409 detail and of the new toast was not exercised in a browser. The existing Playwright test (`e2e/corporate.spec.ts`) mocks the KYB-review response without `status_unchanged`, so it covers the unchanged path only. `kyb-queue` is not one of the 6 seeded visual-regression pages.
- Reviewer agents (gate 10): `spinr-migration-reviewer` and `spinr-corporate-billing-reviewer` reviewed the closed-company guard, and `spinr-corporate-billing-reviewer` reviewed the §3a delta separately. All returned SAFE with no blockers. Their should-fix items are addressed here: admin-writable flag, queue toast, wallet dependency comment. The one exception is the product question in §5.
- §3a's staff-suspension rule was not checked against production data. Nobody has counted how many suspended companies carry each `kyb_last_decision` value, so the misclassification edge in §3a is reasoned about, not measured.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
