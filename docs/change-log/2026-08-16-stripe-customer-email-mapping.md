# Change Impact & Risk Log — map Stripe customers by rider email

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude Code (session: stripe-booking-error) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/stripe-booking-error-551scp` |
| Related issue or gap ID | Live-testing report: a rider's newly-added card could not be found under `mkkreddy52@gmail.com` in the Stripe dashboard |

## 1. Issue / gap identified

Rider Stripe customers were created with `metadata.user_id` only — no email, no name — so
searching the Stripe dashboard by a rider's address returned nothing. Support, dispute, refund,
and chargeback lookups all had to translate an address into a `cus_…` via Supabase first. The
previous Spinr app mapped customers by email; users expect the same. Reported as "I added a card
to a rider, it's not showing in Stripe customer details for `<email>`" — the card *was* attached,
the customer just wasn't findable that way.

## 2. Root cause

Deliberate, not a bug: `routes/payments.py` withheld email/name from Stripe on both rider
customer-creation paths, commented as a PIPEDA data-minimization decision (Stripe is a US
processor; `metadata.user_id` is sufficient to correlate).

Two things made it the wrong trade in practice:

1. **It was never consistent.** `routes/admin/rides.py:1608` (admin "send payable invoice")
   already creates the customer with `email=rider["email"]`. So a rider who had ever been sent an
   admin invoice *was* findable by email and everyone else was not — paying the operational cost
   in full without getting a clean privacy story.
2. **It was never ratified.** The rationale lived in two inline comments, with no ADR. The `C4`
   tag they cite is also used in `services/fare_service.py:254` for an unrelated surge-disclosure
   item, so the label does not resolve to a recorded decision.

The product owner's decision is to match the old app and map by email.

## 3. Fix / remediation

- New `_customer_identity_fields(user)` in `routes/payments.py` — the single place that decides
  what identity reaches Stripe. Returns `{"email": …}`, or `{}` when the rider has no email yet.
- Both creation paths now use it: `get_or_create_stripe_customer` (first-time) and
  `_reprovision_stripe_customer` (test→live repair). The re-provision path now reads the user row
  for this; it previously did not need one.
- New `sync_stripe_customer_email(user_id)` pushes a changed address onto an existing customer,
  spawned from `routes/users.py`'s profile-update handler on `email_changed`. Without this the
  email would reach Stripe only at creation time, and any later profile edit would drift straight
  back to the unfindable state this change exists to fix.
- New `backend/scripts/backfill_stripe_customer_emails.py` (dry-run default) attaches emails to
  customers created before this change.

`metadata.user_id` is unchanged and remains the authoritative join key — it is immutable where
email is not, and Stripe does not enforce uniqueness on customer email (two customers may share
one address). Email is additive, for lookup only. Name, phone, and address are still withheld.

## 4. Risk & impact on existing functionality

**Blast radius: backend payments identity layer, one function deep, plus one new profile-update
side effect.**

Callers of the changed functions, all traced:

| Consumer | Reaches | Effect |
|---|---|---|
| `payments.py::with_customer_repair` | both creation paths | New customers carry email |
| `payments.py::add_card` (`POST /cards`) | via `with_customer_repair` | Unchanged behaviour; attach/SetupIntent untouched |
| `payments.py::get_cards` (`GET /cards`) | via `with_customer_repair` | Unchanged |
| `payments.py::create_payment_intent` | `get_or_create_stripe_customer` | Unchanged |
| `routes/rides/booking.py::_preauthorize_ride_card` | reads `users.stripe_customer_id` | Unchanged — no new failure mode |
| `routes/users.py::update_profile` | **new** `spawn(sync_stripe_customer_email)` | New backgrounded side effect |

What could regress, and why it does not:

- **Customer creation failing for a rider with no email.** Guarded: `_customer_identity_fields`
  omits the key entirely rather than sending `email=None`, and a test pins mid-signup riders.
- **Profile edit failing because Stripe is down.** `sync_stripe_customer_email` catches everything
  and returns `False`; it is `spawn`ed after the DB write is committed, exactly like the two
  existing welcome/security mails on that handler. A test pins that it never raises out.
- **The sync stranding or re-provisioning a customer.** It deliberately does **not** repair a
  `resource_missing` customer — re-provisioning rewrites `users.stripe_customer_id` *and clears
  `default_payment_method`*, which would break settlement for a rider who merely edited their
  profile. Pinned by a test.
- **Idempotency-key replay.** First-time creation still uses `idempotency_key=f"cus-create-{user_id}"`.
  Within Stripe's 24 h replay window a rider whose customer was created just before this deploy
  gets the cached emailless customer back. Self-correcting after 24 h, and the backfill script
  covers it in the meantime. Called out rather than left to be discovered.

Not touched: ride state machine, dispatch, fare/money arithmetic (no `Decimal` code in this diff),
wallet or allowance deltas, `corporate_wallet_apply_delta`, the 18 background loops, RLS, or any
migration. No schema change. Corporate customers (`services/corporate_stripe_identity.py`) already
sent `email` + `name` and are untouched.

**PIPEDA:** this is a deliberate widening of rider PII sent to a US processor. CLAUDE.md's
Compliance section requires primary storage to be "region-matched or justify exception" — this
entry is that justification: email only, no name/phone/address, purpose is payment-processor
identification and dispute handling, and it restores parity with the mapping the previous app
already operated. Email is still never written to logs, Sentry, or analytics; the new log lines
and the backfill script report `user_id` and `cus_…` only, pinned by a test.

### 4a. The admin button (follow-up in the same branch)

The backfill is exposed as **Users → "Sync Stripe emails"**, requested so the sweep can be run
without shell access. Shape follows the repo's existing precedent exactly
(`recomputeStatementTotals`): the logic lives in a service, and the button and the CLI both call
it, so they can never apply a different repair.

`POST /api/admin/stripe/customer-emails/backfill` is mounted on the `stripe_mode_audit` router,
which `routes/admin/__init__.py:172` already gates with `require_super_admin` — and the handler
re-checks `_require_super_admin` in its body rather than trusting the mount, matching the two
existing endpoints there. That is the right bar: this is a bulk PII transfer to a US processor.

Blast radius of the button itself: the endpoint is new (no existing caller to break), the service
is new, and the CLI was rewritten to delegate to it. The admin dashboard change is additive — one
button and one API function; no existing page, component, or call is modified. `CreditCard` was
already imported on that page.

Two deliberate constraints on the endpoint, both pinned by tests:

- **A stranded customer (`resource_missing`) is reported, never repaired.** Re-provisioning
  rewrites `users.stripe_customer_id` *and clears `default_payment_method`* — doing that from a
  bulk admin sweep would break settlement for riders who are not in the room. Repair stays with
  the rider's own card screen. This matches the deliberate choice the neighbouring `probe`
  endpoint already documents.
- **A write run with per-customer failures returns 502, not 200.** A partial sweep must not read
  as success.

## 5. User-experience effect

> **Correction.** An earlier revision of this entry said "no rider-facing change." That was wrong
> — see the Stripe-receipt risk below, found in review. The rest of this section stands.

**In the Spinr apps: no change.** Nothing renders differently, no copy changed, no notification
changed, and nothing is visible mid-session to a rider mid-ride or a driver online.

**⚠️ Outside the apps — Stripe may begin emailing riders directly. VERIFY BEFORE BACKFILLING.**
No code path sets `receipt_email` on a PaymentIntent; Spinr sends its own receipts through
`utils/email_receipt.py`. Stripe's fallback recipient is the **customer's email**, gated on
Dashboard → Settings → Emails → "Successful payments" (live mode only; Stripe does not send in
test mode). Those customers had no email until now. If that setting is enabled, every rider will
receive a Stripe-branded receipt on top of Spinr's own, from the moment their address is attached
— at scale, to real customers, with no in-app trace.

This is not hypothetical and it is not fixed in code: **check that Dashboard setting before
running the backfill.** If it is on and duplicate receipts are unwanted, turn it off, or set
`receipt_email=None` explicitly on the PaymentIntents in `booking.py` / `payment_service.py`
(a separate change, not made here).

**Internal admin (new):** a "Sync Stripe emails" button on the Users page — see §5a.

### 5a. The admin button

A "Sync Stripe emails" button on the Users page. It previews the first batch — retrieving those
customers from Stripe and reporting exact counts, writing nothing — then asks for confirmation
naming the **Stripe account mode (LIVE/TEST)**, how many riders have no email at all versus a
different address, and how many are unreachable on the current key. Nothing is transferred until
the operator confirms. Confirming then walks *all* remaining batches (the confirm text says so);
the operator is never asked to "run again", because that instruction is what made the first
version of this tool silently under-deliver (§9b, finding 1).

Internal admin effect: support can resolve an email to a Stripe customer directly instead of
querying Supabase first.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/payments.py` | Added `_customer_identity_fields()`; both `Customer.create` calls now send `email`; `_reprovision_stripe_customer` reads the user row; added `sync_stripe_customer_email()` | Make rider customers findable by email, and keep them that way |
| `backend/routes/users.py` | On `email_changed`, `spawn(sync_stripe_customer_email(...))` | A later profile edit would otherwise drift Stripe back to a stale address |
| `backend/services/stripe_customer_email_backfill.py` | New; the shared backfill, dry-run default | One implementation behind both the admin button and the CLI, so they cannot drift |
| `backend/routes/admin/stripe_mode_audit.py` | New `POST /stripe/customer-emails/backfill` (super_admin) | Run the sweep from the dashboard without shell access |
| `backend/scripts/backfill_stripe_customer_emails.py` | New; thin CLI over the service | Same repair, same safety properties, from a terminal |
| `admin-dashboard/src/lib/api/users-wallet.ts` + `src/lib/api.ts` | New `backfillStripeCustomerEmails()` | Typed client for the endpoint |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | "Sync Stripe emails" button, preview-then-confirm | The requested operator entry point |
| `backend/tests/test_stripe_customer_email_backfill.py` | New; 14 cases | Pin dry-run-writes-nothing, idempotency, stranded-not-repaired, no-PII-in-payload |
| `backend/tests/test_stripe_customer_email_mapping.py` | New; 20 cases | Pin both creation paths, the withheld fields, the sync, and the no-PII-in-logs rule |
| `backend/tests/test_stripe_event_loop_offload.py` | Pass the new helper into the isolated namespace | Its `ast`-compiled harness needs every global the function body reads (see §9) |

## 7. Before / after

```python
# Before — backend/routes/payments.py::get_or_create_stripe_customer
# PIPEDA (C4): do NOT send the rider's email or legal name to Stripe…
customer = stripe.Customer.create(
    metadata={"user_id": user_id},
    api_key=stripe_secret,
    idempotency_key=f"cus-create-{user_id}",
)
```

```python
# After
customer = stripe.Customer.create(
    **_customer_identity_fields(user),        # {"email": …} or {} — never name/phone/address
    metadata={"user_id": user_id},            # still the authoritative join key
    api_key=stripe_secret,
    idempotency_key=f"cus-create-{user_id}",
)
```

```python
# After — backend/routes/users.py::update_profile (new, additive)
if email_changed:
    spawn(sync_stripe_customer_email(current_user["id"]))
```

## 8. Rollback plan

Code-level: `git revert` restores emailless creation. Safe on its own — no Spinr-side schema or
data changes, no money moved, no ride state touched, so there is nothing to remediate in the
database.

Data-level: emails already written to Stripe customers persist through a code revert. To undo the
transfer, re-run the selection and clear the field —
`stripe.Customer.modify(cus_id, email="")` — for the ids the backfill run reported. The script
prints every `user_id:cus_…` it touches, so the affected set is recoverable from the run log
rather than needing a Stripe-side scan. **This is the only part that is not a code revert**, which
is why the backfill defaults to dry-run and must be run deliberately.

No feature flag was added. If it turns out one is wanted, the natural shape is an `app_settings`
key read inside `_customer_identity_fields` (the single decision point, admin-editable, 60 s cache,
no redeploy) — that is precisely why the logic is centralized in one function rather than inlined
at both call sites.

## 9. Verification performed

- [x] **Automated tests run** — full backend suite: **11,631 passed**, 8 skipped, 1 xfailed, 0 failed
      (690 s). Includes 20 new unit cases in `tests/test_stripe_customer_email_mapping.py`.
      Targeted run of the new file plus `test_stripe_customer_mode_drift.py`: 35/35.
      After the admin-button follow-up: 14 further cases in
      `tests/test_stripe_customer_email_backfill.py` (14/14), and the
      `stripe|payment|admin|user` selection re-run green at **3,133 passed**.
- [x] **Real production build run** for the admin-dashboard change — `npm run build`, exit 0,
      `/dashboard/users` compiled; plus `tsc --noEmit` clean. Stated explicitly per CLAUDE.md:
      this was a real build, not just a type-check.
- [x] **Route registration verified** — the new path appears on the router alongside the two
      existing `stripe/mode-audit` endpoints.

### 9b. Self-review pass — defects found in the first cut of the backfill, and fixed

A Stripe/backend review of this branch found six real problems in code I had already committed.
Recording them because the *class* of mistake matters more than the individual fixes: three of
them were the tool quietly under-performing while reporting success.

1. **The sweep processed only the first batch, then reported "nothing to sync".** `_fetch_users`
   had no `ORDER BY` and restarted at `offset = 0` on every call, so run 2 re-read run 1's page,
   found every customer already correct, and told the operator the job was done while most of the
   fleet had never been touched. Fixed with keyset paging: ordered by `id`, resumed from
   `next_cursor`, and both the CLI and the UI now walk every batch themselves instead of printing
   "run again". Safe to page this way because the sweep writes nothing to Supabase, so the
   ordering is stable across calls. Also now filters `stripe_customer_id IS NOT NULL`, so riders
   with nothing to repair no longer consume the per-run budget.
2. **Riders mid-deletion would have had their address shipped to Stripe.** No `deleted_at` filter
   and no `pending_deletion` check. Fully purged users were safe only by accident (the purge NULLs
   `email`), but a rider inside the 30-day window between requesting deletion and the purge still
   has an address on their row. Now excluded in SQL (`deleted_at IS NULL`) plus a per-row status
   check, counted as `skipped_deleted`. `status` is checked in Python rather than with a `$ne`
   because `!= 'x'` is NULL — and so false — for a NULL status, which would have silently dropped
   those riders from the sweep: the same bug class as finding 1.
3. **The 502 message undercounted.** `result.updated - len(result.failed)` — but a failed row
   never increments `updated`, so 10 updated + 2 failed reported "8 updated". The formula was
   copied from the statements endpoint without checking that the counters compose the same way.
4. **The CLI lost its key-mode warning.** The pre-service version logged `Stripe key mode:
   LIVE|TEST` before touching anything; refactoring to the shared service dropped it to a
   completion-time field. The service now logs it *before* the sweep, the endpoint returns
   `key_mode`, and the confirm dialog names the account — the only moment that fact is actionable.
5. **A Stripe error could have leaked an email into the logs**, contradicting this entry's own
   PIPEDA claim: Stripe quotes the value it rejected, and here that value is the address.
   `str(exc)` is now redacted through `_redact_emails` and `exc_info` is dropped on that path.
6. **A test that proved nothing.** `test_limit_is_clamped_to_max` asserted `updated == 1`, true
   whether or not clamping happened. It now asserts the cap itself.

One further finding was **not** fixed and is carried as an open item: the Stripe receipt-email
risk (§5, needs a Dashboard check, not a code change). The request-duration ceiling was left open
too — and then caused the incident in §9c, so it is now fixed.

### 9c. Production incident — Stripe rate limit, first live run

**What happened.** The first real run of the button (2026-08-16 22:09 UTC, request
`88899aae-e5bf-4ab9-a743-41212d950220`) throttled almost immediately: **19 riders updated, 97
failed** with `Request rate limit exceeded`, and the endpoint returned 502.

**Root cause — mine.** `MAX_CONCURRENCY = 8` bounded how many Stripe calls were *in flight*, not
the *rate*. Each call completes in ~50–100 ms and the next starts immediately, so 8 workers at
~2 calls per rider (retrieve + modify) sustained well over 100 req/s — past Stripe's live ceiling
(100/s) and far past test mode's (25/s). The SDK's own `max_network_retries = 2`
(`utils/stripe_config.py`) was already retrying and could not rescue it: **retries do not fix a
job that is structurally too fast.** I had reviewed this file twice and treated a semaphore as a
rate limit both times.

**Fixes:**

| Change | Why |
|---|---|
| New `_Pacer`, `STRIPE_CALLS_PER_SECOND = 10` | Bounds the actual rate across all workers. Deliberately far below Stripe's ceiling because this account is simultaneously taking real bookings — a maintenance sweep must never be why a rider's authorization gets throttled |
| `MAX_CONCURRENCY` 8 → 4 | Overlaps latency only; the pacer is the real limiter now |
| `RATE_LIMIT_RETRIES = 3`, exponential backoff + jitter, on top of the SDK's 2 | A 429 means "come back later". Jitter so workers throttled together don't return in lockstep and re-throttle each other |
| `DEFAULT_LIMIT` 500 → 50, `MAX_LIMIT` 2000 → 150 | At 10 calls/s a 150-rider batch is ~30 s of Stripe time — inside a 60 s gateway timeout with room for retries. This is the §9b open item, now closed by the incident. Callers sweep the fleet with `next_cursor`, not by raising the cap |
| New `throttled` bucket, separate from `failed` | A 429 is not a broken write |
| 502 now fires only for genuine failures | Throttling returned a 502 that discarded the counts into a prose `detail` string and aborted the client's batch loop, so a rate-limited sweep read as an outage |
| Aggregate throttle log at `warning` | 97 identical ERROR lines is not a signal. Degraded-but-recovered → warning + count, per CLAUDE.md's observability table |
| `incomplete` flag on the response; UI/CLI report it | A throttled run must never toast as success or exit 0 |

**Rider impact: none.** No ride, payment, or wallet state was touched. The 19 riders whose emails
were written are correct and the operation is idempotent — a re-run leaves them `unchanged`. The
97 throttled riders were simply not written yet.

**Still true:** re-running is safe and picks up where this left off. Nothing needs undoing.
- [x] **A real regression was caught and fixed by the suite.**
      `tests/test_stripe_event_loop_offload.py` compiles `get_or_create_stripe_customer` in an
      isolated namespace via `ast`, so it must be handed every global the body reads — the new
      `_customer_identity_fields` was missing and it failed with `NameError`. Fixed by loading the
      real helper the same isolated way rather than stubbing it, so the test keeps exercising the
      actual argument set sent to Stripe instead of drifting from it.
- [x] **Lint / format** — `ruff check` and `ruff format --check` clean on all four changed files.
- [x] **Blast-radius grep performed** — every caller of `get_or_create_stripe_customer`,
      `_reprovision_stripe_customer`, and `with_customer_repair` traced (table in §4); all four
      `stripe.Customer.create` sites in the repo reviewed
      (`payments.py` ×2, `admin/rides.py`, `corporate_stripe_identity.py`); confirmed
      `settings_loader.get_app_settings` and `db_supabase.get_rows(columns=…, limit=, offset=)`
      signatures against source rather than assumed.
- [x] **Reviewed against CLAUDE.md conventions** — dual-import pattern used for the lazy
      `routes.users → routes.payments` import (cycle-safe); no float money arithmetic in the diff;
      observability (structured `extra={}`, `logger.error` on failure not `warning`, metric named
      `spinr_payments_stripe_customer_email_synced_total` per `spinr_<domain>_<metric>_<unit>`);
      PIPEDA (no email in logs, justification recorded in §4).
- [x] **Escalation** — the decision to send rider email to Stripe was raised as a PIPEDA trade-off
      and explicitly reaffirmed by the product owner ("already in old app these customers are
      mapped using the email id, we should do the same"). Implemented as directed.

## 10. What was NOT verified

- **Not tested against live Supabase or live Stripe.** All tests mock `stripe.Customer.*` and the
  DB layer. No real customer was created or modified.
- **The Stripe receipt-email behaviour (§5) has NOT been checked** against the live Dashboard
  settings. It is stated as a risk to verify, not as a confirmed outcome — I could not read the
  account's email configuration from here.
- **Neither the backfill button nor the CLI has ever been run against a real Stripe account** —
  not even in dry-run — because this environment has no Supabase credentials or Stripe key. The
  service is unit-tested against mocks, but the end-to-end path (real users table → real Stripe
  retrieve) is **unexercised**. Preview first (the button does this automatically; the CLI needs
  no flags) and read the counts before confirming or passing `--apply`.
- **The admin button was not clicked.** No browser or running backend here, so the UI was verified
  by `npm run build` (real production build, exit 0) and `tsc --noEmit`, not by interaction. The
  preview-then-confirm flow, the toast copy, and the `window.confirm` wording were reasoned
  about, not seen. There is also no visual-regression tooling for admin-dashboard (standing gap).
- **The endpoint was not exercised over HTTP.** Route registration was verified by importing the
  router and listing its paths; the super_admin gate is by inspection (it mirrors the two existing
  endpoints on the same router, which is `require_super_admin`-mounted) and by the in-body
  `_require_super_admin` call — not by an authenticated request test.
- **The 24 h idempotency-replay window (§4) was reasoned about, not observed.** No test can
  exercise Stripe's server-side replay cache.
- **`admin/rides.py`'s email-sending path was left as-is.** It is now consistent with the new
  direction by accident rather than by design; it was not refactored to share
  `_customer_identity_fields`, so the two paths can drift again.
- **No integration test covers `update_profile` → `sync_stripe_customer_email` end to end.** The
  sync is unit-tested directly; the wiring in `users.py` is covered only by reading it.

## 11. Sign-off

- [x] Rollback plan is concrete and testable, including the data-level step that a revert does not undo
- [x] Blast radius is stated, not assumed (§4 table)
- [x] No silent behavior change to an already-shipped flow — no user-facing surface changes (§5)
