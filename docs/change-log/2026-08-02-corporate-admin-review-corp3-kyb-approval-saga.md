# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #3 |

## 1. Issue / gap identified

`POST /admin/corporate-accounts/{id}/kyb-review`'s approve path is a
three-step sequence — DB status flip (`record_kyb_decision`) → wallet
provisioning (`ensure_corporate_wallet`) → Stripe customer creation — with
no compensating rollback for the first step. If wallet provisioning
failed, the endpoint raised a 503 ("KYB approved but wallet provisioning
failed — please retry"), which is misleading: `record_kyb_decision` had
already committed `status='active'` in the DB before the wallet step ran,
so the company genuinely *was* already approved — the admin saw a scary
error implying the approval itself failed. Worse, the Stripe customer
creation call had no error handling at all: a raised exception there
(e.g. a transient Stripe API error) would propagate as an unhandled 500,
again after the status was already committed, with zero visibility into
what had and hadn't completed.

## 2. Root cause

The endpoint was written as a straight-line sequence without accounting
for partial failure once the first (irreversible, no-rollback) step
succeeds. The codebase already has an established pattern for exactly
this shape of problem — `create_corporate_account`'s owner-bootstrap step
(same file) independently catches a bootstrap failure, logs it loudly,
and surfaces `owner_bootstrap_error: bool` on the response instead of
raising, with a comment explicitly documenting the partial-success
rationale — but `kyb_review` was never brought in line with it.

## 3. Fix / remediation

- Wrapped both provisioning steps (`ensure_corporate_wallet`, Stripe
  customer creation) in their own `try/except`, each logging loudly
  (`logger.error(..., exc_info=True)`) with an explicit note that the
  status change already committed and manual follow-up is needed,
  instead of raising.
- Added a new `KYBReviewResponse` model (subclassing
  `CorporateAccountDetailResponse`, mirroring
  `CorporateAccountCreatedResponse`'s shape exactly) with
  `wallet_provisioning_error: bool = False` and
  `stripe_customer_creation_error: bool = False`, changed the route's
  `response_model` to it, and included both flags in the returned dict
  and in the `kyb_review` audit-log `details`.
- No change to the reject path, the notification email step (already
  best-effort/non-raising), or the audit-log call (already
  non-raising) — all three were already correctly fail-open.

## 4. Risk & impact on existing functionality

- **Blast radius: the `kyb_review` function and its response model.**
  Grepped every caller/consumer of the KYB-review response: the
  admin-dashboard's KYB queue UI (reads `status`, which is unchanged —
  the two new fields are purely additive and both default `False`, so
  an admin-dashboard client that doesn't know about them sees identical
  behavior to today); no other backend code parses this endpoint's
  response.
  Grepped every test file referencing `kyb_review`/`kyb-review`/
  `record_kyb_decision` (`test_corporate_kyb.py`,
  `test_corporate_admin_routes.py`, `test_corporate_stripe_customer.py`,
  `test_corporate_wallet_bootstrap.py`, `test_corporate_company_kyb.py`,
  `test_corporate_b2b_schema.py`) — 58 tests, all passing except one
  pre-existing, unrelated failure (`test_actor_user_id_is_text_not_uuid`
  in `test_corporate_b2b_schema.py`, a migration-schema drift check for
  an unrelated column; confirmed failing identically on the unmodified
  tree via `git stash` before this change, so it predates and is
  unaffected by this fix).
- **Behavior change: the wallet-failure case now returns 200 instead of
  503.** This is the entire point of the fix — the old 503 was
  misleading (it implied nothing had happened, when the status flip
  already had). Any external caller that specifically branched on a 503
  from this endpoint to mean "approval failed, nothing happened" would
  need to check the new `wallet_provisioning_error`/
  `stripe_customer_creation_error` fields instead — grepped the
  admin-dashboard for any such branching and found none (the KYB queue
  UI does not special-case a 503 from this endpoint).
- Money/state impact: none beyond what already existed. `record_kyb_
  decision`'s status flip was always the first, always-committed step;
  this fix does not change when or whether it commits — it only changes
  what happens to the response/control-flow if a later step fails.

## 5. User-experience effect

**Internal admin-facing only.** An admin approving a KYB submission whose
wallet or Stripe-customer provisioning fails now sees a 200 response with
`status: "active"` and a `wallet_provisioning_error`/
`stripe_customer_creation_error` flag set `true`, instead of a 503/500
error that looked like the approval itself failed (when in fact the
company was already approved). The admin-dashboard doesn't yet render
these two new fields specially (see "What was NOT verified" below) — this
fix makes the backend behavior correct and observable via logs/audit
trail immediately; a dedicated UI treatment is a reasonable follow-up.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_accounts.py` | New `KYBReviewResponse` model; `kyb_review`'s wallet + Stripe steps now catch-log-and-flag instead of raising; both flags included in the audit-log details and the response | Stop raising after an irreversible step already committed; surface partial failure instead of hiding it behind a misleading error |
| `backend/tests/test_corporate_kyb.py` | New tests: wallet-failure partial-success, success-reports-no-errors | Cover the new non-raising wallet-failure path |
| `backend/tests/test_corporate_stripe_customer.py` | New test: Stripe-customer-creation-failure partial-success | Cover the new non-raising Stripe-failure path |

## 7. Before / after

```python
# Before
if decision.approve:
    try:
        await ensure_corporate_wallet(company_id=normalized_id)
    except Exception as wallet_err:
        logger.error(f"[KYB] Wallet creation failed for company {normalized_id}: {wallet_err}")
        raise HTTPException(
            status_code=503,
            detail="KYB approved but wallet provisioning failed — please retry",
        ) from wallet_err
    if not row.get("stripe_customer_id"):
        ...
        customer = await asyncio.to_thread(lambda: stripe.Customer.create(...))
        await update_corporate_stripe_customer_id(...)  # unguarded — any exception here 500s
...
return row
```

```python
# After
wallet_provisioning_error = False
stripe_customer_creation_error = False
if decision.approve:
    try:
        await ensure_corporate_wallet(company_id=normalized_id)
    except Exception:
        logger.error("[KYB] Wallet creation failed ... needs manual follow-up", ..., exc_info=True)
        wallet_provisioning_error = True
    if not row.get("stripe_customer_id"):
        try:
            ...
            customer = await asyncio.to_thread(lambda: stripe.Customer.create(...))
            await update_corporate_stripe_customer_id(...)
        except Exception:
            logger.error("[KYB] Stripe customer creation failed ... needs manual follow-up", ..., exc_info=True)
            stripe_customer_creation_error = True
...
return {
    **row,
    "wallet_provisioning_error": wallet_provisioning_error,
    "stripe_customer_creation_error": stripe_customer_creation_error,
}
```

## 8. Rollback plan

Plain code change, no migration, no data written differently — the
status flip's timing and semantics are unchanged; only what happens
after a later step fails is different. `git revert` fully restores the
prior (raise-on-wallet-failure, unguarded-Stripe-call) behavior. No
feature flag — this replaces a misleading error with an accurate one
using a pattern already proven elsewhere in the same file; there's no
meaningful dark-ship version of "don't lie to the admin about whether
the approval committed."

## 9. Verification performed

- [x] Automated tests: `test_corporate_kyb.py` (6, incl. 2 new),
      `test_corporate_admin_routes.py` (25),
      `test_corporate_stripe_customer.py` (4, incl. 1 new),
      `test_corporate_wallet_bootstrap.py` (2),
      `test_corporate_company_kyb.py` (18),
      `test_corporate_b2b_schema.py` (3, 1 pre-existing unrelated
      failure) — 58 collected, 55 passed, 2 skipped, 1 pre-existing
      failure confirmed unrelated (see §4). Run via the session's
      `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on all 3 touched files — clean.
- [x] Blast-radius grep performed (see §4): every consumer of the
      response shape, every test file referencing the affected
      functions.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: an admin approves a KYB submission for company X.
      `record_kyb_decision` succeeds (status → active). `ensure_corporate_
      wallet` then raises (e.g. transient DB error). Before this fix: the
      admin's browser shows a 503 "wallet provisioning failed — please
      retry" — but the company is already active, and clicking "retry"
      re-runs `record_kyb_decision` (idempotent) plus a fresh wallet
      attempt. After this fix: the admin sees a 200 with
      `status: "active"` and `wallet_provisioning_error: true` — an
      accurate picture of what happened, logged loudly server-side for
      ops follow-up either way.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every consumer and dependent
      test file grepped; the one failing test confirmed pre-existing and
      unrelated via `git stash`
- [x] User-experience effect stated: this is a deliberate behavior
      change (503/500 → 200 with error flags) for the failure path only;
      the success path (both provisioning steps succeed) is byte-for-byte
      unchanged except for the two new `False`-defaulted fields

## What was NOT verified

Not tested against a live/staging Supabase or a real Stripe API call —
only mocked failures in the unit/integration test suite. Did not add a
UI treatment in the admin-dashboard KYB queue for the two new response
fields (e.g. a warning banner when `wallet_provisioning_error` is true) —
the backend now correctly surfaces and logs the partial failure, but the
admin-dashboard doesn't yet call attention to it beyond whatever generic
JSON the response contains; a dedicated UI affordance ("wallet
provisioning failed — retry from here") is a reasonable follow-up, not
implemented here. Did not build an automated retry/reconciliation job for
companies left in the `wallet_provisioning_error`/
`stripe_customer_creation_error` state — recovery today is still manual
(re-running kyb-review, which retries both steps since `ensure_corporate_
wallet` is idempotent and the Stripe step is skipped once
`stripe_customer_id` is set), same as before this fix, just now
discoverable instead of hidden behind a misleading error. Noted but did
NOT fix a separate, pre-existing, unrelated test failure discovered
while running this file's suite (`test_actor_user_id_is_text_not_uuid` —
a migration-schema drift check for a column this change never touches);
confirmed via `git stash` that it fails identically without this change,
so it's flagged here for visibility but left out of scope.
