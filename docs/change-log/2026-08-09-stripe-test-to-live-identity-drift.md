# Change Impact & Risk Log — Stripe test→live identity drift

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-09 |
| Author | Claude Code (session `claude/stripe-card-sync-issue-gepad1`) |
| Surface(s) | backend (rider payments, driver payouts, admin) |
| Domain (Sentry tag) | payments, drivers, admin |
| PR / commit link | branch `claude/stripe-card-sync-issue-gepad1` — commits `eed26d5`, `6c59860`, `1e96db6`, `46561a7` |
| Related issue or gap ID | Reported live: "I have live Stripe, I am unable to sync the driver and rider card details back" |

## 1. Issue / gap identified

After `app_settings.stripe_secret_key` was rotated from `sk_test_…` to
`sk_live_…` on the same database, three surfaces broke at once, reported from
live app testing:

1. riders' saved cards fail to load and no new card can be added;
2. drivers' payout/bank details do not come back into the app or admin;
3. admin cannot see a rider's cards or a driver's payout method.

## 2. Root cause

Stripe object IDs are **mode-scoped**. A `cus_…` created with a test key does
not exist under a live key, and neither does an `acct_…`. The `cus_`/`acct_`
prefixes are byte-identical in both modes, so an ID carries no evidence of its
own mode — and nothing in the codebase recorded it.

Because `stripe_secret_key` lives in the `app_settings` table (deliberately, so
it rotates without a redeploy), the key can change mode *underneath* rows that
already hold IDs from the previous mode. Every subsequent Stripe call for those
rows returns `resource_missing`:

- `users.stripe_customer_id` → `GET /payments/cards` 502s; `POST /payments/cards`
  also fails, because attaching a PaymentMethod addresses the same dead
  customer, so the rider had **no in-app way out**.
- `drivers.stripe_account_id` → `AccountLink.create` and `Account.retrieve` fail,
  so "Set up payouts" dead-ends and admin's "Refresh from Stripe" reports a
  generic error. Worse, the migration-92 KYC mirror columns kept describing the
  dead account, so admin and the driver app both still showed *payouts enabled*
  and *bank on file* for a destination that does not exist.
- Admin's card panel collapsed every failure into "Could not load cards from
  Stripe", which reads as an outage.

**Not recoverable, and the fix does not pretend otherwise:** Stripe sandboxes
test data and offers no test→live copy path (their data-migration service is
live-account→live-account only). Any card saved in test mode was a test PAN
(`4242…`) with no real card behind it; a test-mode Connect account holds no real
bank details and no verified identity. There is nothing of value to "sync back".
The correct outcome is a working, empty state that riders and drivers can
re-populate — not a broken screen.

## 3. Fix / remediation

Record the mode, detect the mismatch, and recover on the paths where the
affected user is present.

- **Record** — migration 286 adds `*_mode` and `*_superseded(_at)` columns to
  `users`, `drivers`, and `corporate_accounts`. New identities are stamped from
  the returned object's own `livemode` (evidence, not inference). No backfill:
  an existing ID's mode is genuinely unknowable, so those rows stay NULL.
- **Detect** — `utils/stripe_mode.py`, pure classification, no I/O. The
  load-bearing piece is `is_missing_on_key()`, which counts `resource_missing`
  and `PermissionError` but explicitly **not** `AuthenticationError`,
  `APIConnectionError`, `RateLimitError`, or `CardError`.
- **Recover (riders)** — `with_customer_repair()` wraps a customer-addressed
  Stripe call: on `resource_missing` it mints a replacement, archives the dead
  ID, and retries once. Wired into `/payments/cards` (GET+POST),
  `/payments/methods`, `/payments/setup-intent`, `/payments/payment-sheet`,
  `/wallet/top-up`.
- **Recover (drivers)** — `retire_stripe_account()` archives the ID and resets
  the whole KYC mirror. It deliberately does **not** auto-create a replacement;
  the driver re-onboards through the existing in-app flow, which mints the new
  Express account.
- **See** — `GET/POST /api/admin/stripe/mode-audit[/probe]` (super_admin) report
  how many identities are stranded and which. The probe classifies only.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** Grepped every reader/writer of both columns.

`users.stripe_customer_id` — `routes/payments.py` (7 endpoints), `routes/wallet.py`,
`utils/stripe_charge.py` (charge/preauth/capture), `routes/rides/booking.py`
(preauth), `routes/rides/cancellation.py`, `services/payment_service.py`
(settlement), `utils/scheduled_rides.py`, `routes/webhooks.py:1235`
(subscription lookup), `routes/admin/users.py`, `routes/admin/rides.py`,
`services/rider_import_service.py`, `services/stripe_mapping_import_service.py`.

`drivers.stripe_account_id` — `routes/drivers/payouts.py` (onboard, standard and
instant payout), `services/stripe_kyc_sync.py`, `services/stripe_payout_sync_service.py`,
`routes/admin/drivers.py:2689`, `routes/admin/compliance.py`,
`services/stripe_mapping_import_service.py`.

`corporate_accounts.stripe_customer_id` — affected by the same drift; columns
added, but **no repair logic applied to the corporate path in this change**
(see "What was NOT verified").

Specific regression risks considered:

- **A `pm_` outliving its customer.** Re-provisioning clears
  `users.default_payment_method` in the same write. Without that, settlement
  (`services/payment_service.py:966`) and cancellation-fee capture
  (`routes/rides/cancellation.py:189,479`) would read a `pm_` scoped to the
  replaced customer and fail at trip end — converting a fixed cards screen into
  failed charges. Covered by a test.
- **Mass false-positive repair.** The nightmare case is a valid-but-wrong key
  (revoked, or another Stripe account): every identity would look missing and a
  naive implementation would orphan the live fleet. Mitigated by the narrow
  `is_missing_on_key()` contract, by archiving rather than deleting, and by the
  kill switch. Tests assert no write occurs on auth/transient/card errors.
- **Idempotency-key replay.** Re-provisioning uses
  `cus-reprovision-{user}-{stale}` / `connect-acct-{driver}-{superseded}` rather
  than the first-time key, so a replay inside Stripe's 24 h window cannot hand
  back the identity just proved unusable.
- **Concurrent healers.** Every repair write filters on the stale ID, so a
  racing healer or a completed re-onboarding is never clobbered. The existing
  unique indexes (migration 257) remain the DB-level backstop.
- **Payout safety.** The payout path was left alone: `Transfer.create` against a
  dead account already fails and marks the payout `failed`, leaving the balance
  with the driver. Retiring resets `stripe_id_number_provided`, so
  `_require_sin_for_payout` re-gates payouts until re-onboarding — a
  deliberate, safe tightening.
- **Background loops** (`core/lifespan.py`): none call the changed helpers. The
  Stripe reconcile and payment-retry loops read `stripe_customer_id` via
  `utils/stripe_charge.py`, which is unchanged — a stranded customer still
  fails there loudly, as before.
- **No change to the ride state machine, dispatch, surge, or any money
  arithmetic.** No `Decimal` code paths touched.

## 5. User-experience effect

- **Rider (visible, and visible mid-session).** The cards screen changes from a
  hard error to a working, empty list they can add a card to. A rider who saved
  a card during test mode will find it gone — unavoidable, since it never
  existed as a real card. No new copy shipped; the existing empty-state is used.
- **Driver (visible).** A driver whose Connect account was test-mode now sees
  "Set up payouts" instead of a false "bank linked", and `/drivers/stripe-sync`
  returns `reonboarding_required: true` so the app can explain why. **The driver
  app does not yet render that explanation** — it currently just shows the
  not-set-up state (see "What was NOT verified").
- **Internal admin (visible).** The card panel now names the key-mode cause
  instead of "Could not load cards from Stripe", and two new super_admin
  endpoints exist. No admin-dashboard UI was built for them.
- **Corporate admin:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/286_stripe_id_mode_tracking.sql` | New. `*_mode`, `*_superseded(_at)` on `users`/`drivers`/`corporate_accounts`; NOT VALID CHECKs then VALIDATE; partial indexes | Record a mode that was never stored; keep the ALTER inside the <30 s SLA |
| `backend/utils/stripe_mode.py` | New. `key_mode`, `object_mode`, `is_missing_on_key`, `stale_by_mode` | One place that decides what counts as evidence an identity is stranded |
| `backend/routes/payments.py` | Stamp on create; `_reprovision_stripe_customer`; `with_customer_repair`; re-raise `HTTPException` before catch-alls | Rider recovery without added latency on the healthy path |
| `backend/routes/wallet.py` | Use the shared helper; drop the duplicated get-or-create and its redundant user read | Removes a PIPEDA-divergent duplicate and gains the repair |
| `backend/routes/drivers/payouts.py` | Mode pre-check in `_ensure_stripe_account`; `_create_stripe_account` stamps mode; `stripe-onboard` retires+recreates on `resource_missing`; `stripe-sync` returns `reonboarding_required` | Driver recovery at the moment they ask for it |
| `backend/services/stripe_kyc_sync.py` | `retire_stripe_account`, `account_is_stale_by_mode`; `refresh_driver_kyc` distinguishes `account_not_on_key` | Stop the mirror advertising a payout destination that does not exist |
| `backend/routes/admin/stripe_mode_audit.py` | New. Audit + probe endpoints | Operator visibility that did not exist |
| `backend/routes/admin/__init__.py` | Mount the audit router under `require_super_admin` | Payout-adjacent reads + Stripe quota |
| `backend/routes/admin/users.py` | Card panel names the key-mode cause | "Could not load cards" sent operators hunting a non-existent outage |
| `backend/schemas.py`, `backend/routes/admin/settings.py` | `stripe_reprovision_stale_ids` flag (default true) | Kill switch, flippable without redeploy |
| `backend/tests/test_stripe_mode.py`, `test_stripe_customer_mode_drift.py`, `test_stripe_connect_mode_drift.py`, `test_stripe_mode_audit.py` | New. 85 tests | Negative cases are the point |
| `backend/tests/test_wallet.py`, `test_ephemeral_key_signature.py`, `test_p2_promo_wallet_loyalty.py`, `test_stripe_event_loop_offload.py`, `test_payouts_coverage.py` | Repointed patches / updated expectations | The customer lookup moved into `payments.py`; creates now stamp mode |

## 7. Before / after

Rider card list — before, a stranded customer was terminal:

```python
# Before — routes/payments.py::get_cards
customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)
methods = await asyncio.to_thread(
    lambda: stripe.PaymentMethod.list(customer=customer_id, type="card", api_key=stripe_secret)
)
# stale customer -> resource_missing -> except Exception -> 502, forever
```

```python
# After
async def _list(cid: str):
    return await asyncio.to_thread(
        lambda: stripe.PaymentMethod.list(customer=cid, type="card", api_key=stripe_secret)
    )

# resource_missing -> mint a live customer, archive the dead ID, retry once
_, methods = await with_customer_repair(current_user["id"], stripe_secret, _list)
```

Driver KYC refresh — before, "gone" and "Stripe is down" were the same answer:

```python
# Before — services/stripe_kyc_sync.py::refresh_driver_kyc
except Exception:
    logger.error(...); return {"status": "stripe_error"}
```

```python
# After
except Exception as e:
    if is_missing_on_key(e):
        await retire_stripe_account(driver, account_id, reason="resource_missing")
        return {"status": "account_not_on_key", "retired": True}
    logger.error(...); return {"status": "stripe_error"}
```

## 8. Rollback plan

**No redeploy needed.** Set `stripe_reprovision_stale_ids = false` in
`app_settings` (admin Settings page). It propagates within the 60 s settings
cache and stops all re-provisioning and retiring; the rider path then surfaces
503 with a support message instead of writing anything.

For identities already re-provisioned, the old ID is archived, not destroyed —
a `git revert` alone would leave rows pointing at the new customer, so restore
by data:

```sql
-- Riders: restore the superseded customer (only where nothing has since been saved)
UPDATE users
SET stripe_customer_id = stripe_customer_id_superseded,
    stripe_customer_id_mode = NULL,
    stripe_customer_id_superseded = NULL,
    stripe_customer_id_superseded_at = NULL
WHERE stripe_customer_id_superseded IS NOT NULL
  AND stripe_customer_id_superseded_at > '<cutover timestamp>';

-- Drivers: same shape (KYC mirror re-populates from the account.updated
-- webhook or the admin "Refresh from Stripe" button afterwards)
UPDATE drivers
SET stripe_account_id = stripe_account_id_superseded,
    stripe_account_id_mode = NULL,
    stripe_account_id_superseded = NULL,
    stripe_account_id_superseded_at = NULL
WHERE stripe_account_id_superseded IS NOT NULL
  AND stripe_account_id_superseded_at > '<cutover timestamp>';
```

Note this restores *pointers to test-mode objects*, which are unusable under a
live key — it is a rollback of this change's writes, not a recovery of function.
Migration 286 itself is additive; its DROP COLUMN rollback is in the file header.

## 9. Verification performed

- [x] **Automated tests — unit.** Full backend suite run; 85 new tests across four
      new files, all passing. Negative cases lead: no repair on
      `AuthenticationError` / `APIConnectionError` / `RateLimitError` / `CardError`;
      `default_payment_method` cleared on re-provision; KYC mirror fully reset on
      retire; probe reports `inconclusive`, never `stranded`, on ambiguous errors.
- [x] **Blast-radius grep performed.** `stripe_customer_id` and
      `stripe_account_id` across all non-test, non-migration backend code (results
      in §4); `default_payment_method`; `get_or_create_stripe_customer` call sites;
      `refresh_driver_kyc` callers; `routes.wallet.db_supabase` patch targets.
- [x] **Route mounting verified** by importing the app and listing
      `/api/admin/stripe/mode-audit[/probe]`.
- [x] **Reviewed against `CLAUDE.md` conventions:** dual-import pattern kept;
      no float arithmetic introduced; Stripe idempotency keys distinct per
      supersede event; errors surfaced not swallowed (kill switch raises 503
      rather than serving a knowingly-wrong card list); observability —
      degraded-but-recovered logged at `warning` with structured `extra`, IDs
      only, no PII; PIPEDA — the wallet path no longer sends rider email/name
      to Stripe.
- [x] **Feature-flagged.** `stripe_reprovision_stale_ids`, default true.
      Default-on is deliberate: the alternative is a permanently broken payment
      surface with no in-app recovery.
- [ ] **Manual repro in staging — NOT performed.** See below.

## 10. What was NOT verified

State the boundary explicitly rather than letting silence imply coverage.

- **No run against a real Stripe account, in either mode.** Every Stripe
  interaction in these tests is mocked. The `resource_missing` error shape,
  `PermissionError` for foreign Connect accounts, and the `livemode` flag are
  taken from Stripe's documented contract, not observed here. **Before relying
  on this in production, exercise one rider and one driver against the live key
  in staging.**
- **Migration 286 has not been applied to any database.** No Supabase
  connection was available in this session. The SQL is unrun — including the
  `NOT VALID` → `VALIDATE CONSTRAINT` sequence and the `DO $$` guards. Apply
  with `--dry-run` first.
- **No production build was run** for any frontend surface, because no frontend
  code changed. `admin-dashboard`, `rider-app`, and `driver-app` are untouched.
- **No admin-dashboard UI** was built for the audit endpoints — they are
  API-only and must be called with a super_admin token (curl/Postman).
- **The driver app does not surface `reonboarding_required`.** The backend
  returns it; the client ignores unknown fields, so today the driver simply sees
  the not-set-up state with no explanation of why their bank vanished. Wiring
  the copy is follow-up work.
- **The corporate path is unrepaired.** `corporate_accounts.stripe_customer_id`
  gets the tracking columns but no re-provision logic, so corporate wallet
  auto-topup and subscriptions will still fail `resource_missing` after a mode
  rotation. Deliberate: those flows charge a company's stored card on a
  schedule with no user present to consent to a new payment identity, so the
  same "repair where the user is" principle points to a different design.
  Tracked as follow-up.
- **No visual/snapshot regression tooling exists** for these surfaces (standing
  gap, `ACTION_ITEMS.md`); no frontend rendering was checked.
- **Concurrency was reasoned about, not load-tested.** The conditional writes
  and idempotency keys are argued correct in §4 and unit-tested single-threaded;
  no concurrent-replica test was run.
