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

**No rider-, driver-, or corporate-facing change.** Nothing in any app renders differently, no copy
changed, no notification changed, and nothing is visible mid-session to a rider mid-ride or a
driver online. The only observable difference is in the Stripe dashboard, which is internal:
searching a rider's email now finds their customer.

**Internal admin (new):** a "Sync Stripe emails" button on the Users page. It always previews
first — it retrieves every customer from Stripe and reports the exact counts, writing nothing —
then asks for confirmation naming how many riders have no email at all versus a different address,
and how many are unreachable on the current key. Nothing is transferred until the operator
confirms. The confirm copy states plainly what leaves the country and what is not touched (no
customer created, no saved card affected).

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
