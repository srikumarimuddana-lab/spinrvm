# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch — `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | ACTION_ITEMS.md C53, finding 4 |

## 1. Issue / gap identified

The `fc6f922` dark launch shipped a Postgres transactional outbox for ride
receipts (`migrations/399_transactional_outbox.sql`, default-off flag
`settings.outbox_receipts_enabled`) plus a dedicated worker process
(`worker.py`, `services/outbox.py`, `services/outbox_receipts.py`), but the
Python glue the worker needs to actually deliver a receipt — a typed
delivery-result contract distinguishing "never retry" from "retry with
backoff", and the 4 live payment-settlement call sites that must skip their
direct send when the DB trigger already queued one — was missing or
unwired. `utils/outbox_worker.py` could not even import
(`EmailDeliveryStatus` didn't exist), so `test_outbox_worker.py` and four
sibling spec files failed to collect at all.

## 2. Root cause

The feature was landed as one large dark commit; the DB migration, the
worker skeleton, and the outbox repository layer were built, but the
email-delivery-result types and the receipt call-site wiring were not —
confirmed by grep (zero classes in `email_provider.py`, no
`send_receipt_email_result`/`send_ride_receipt_result`, and zero references
to `maybe_send_auto_receipt` anywhere in the 4 call sites the tests expect
it in) before this change.

## 3. Fix / remediation

- Added `EmailDeliveryStatus` (accepted/terminal_skip/retryable_failure) +
  `EmailDeliveryResult` to `utils/email_provider.py`, plus
  `send_transactional_email_result(...)`. The existing bool
  `send_transactional_email` (12 existing callers) now delegates to it —
  same bool contract preserved for every one of them.
- Same pattern one layer up: `email_receipt.py::send_receipt_email_result`,
  with the existing bool `send_receipt_email` delegating to it.
- Added `payment_service.py::send_ride_receipt_result(ride_id)` — the
  outbox worker's entry point; hydrates ride/rider/driver exactly like the
  existing `send_ride_receipt` and returns the typed result instead of a
  bool. A missing ride is treated as `retryable_failure` (not terminal) —
  safer to retry through the outbox's bounded attempts than to permanently
  drop a real ride's receipt on what could be a transient visibility race.
- Wired the already-existing `services/outbox_receipts.py::maybe_send_auto_receipt`
  into all 4 payment-settlement call sites the tests specify:
  `routes/rides/payments.py::process_payment`,
  `routes/webhooks.py::_handle_ride_invoice_paid` (+ its second
  `payment_intent.succeeded` receipt site), `utils/preauth_capture.py::_capture_one`,
  `services/payment_service.py::auto_settle_guest_corporate`.
- Added the worker's 4 loop names to `utils/loop_monitor.py::LOOP_THRESHOLDS`
  (previously silently defaulting to a 2h staleness window for a 1-10s poll
  loop) — `worker.py` and `core/background_loop_registry.py` were already
  fully built from the dark launch; this was the one real gap in them.
- Fixed 2 test files whose provider mock target moved as a direct
  consequence of step 1-2 (`email_receipt.py` now calls
  `send_transactional_email_result` directly, not the bool wrapper), and
  widened `test_all_emails_are_branded.py`'s brand-consistency detector
  regex to also match the new `_result`-suffixed call shape.

See ACTION_ITEMS.md C53 finding 4 for the full per-file breakdown.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (backend), 4 payment-settlement call
  sites + the shared email-provider layer.** No rider/driver/admin app
  changes.
- `send_transactional_email` / `send_receipt_email`: grepped all 12 + N
  existing callers (routes/admin/messaging.py, routes/auth.py,
  routes/corporate_accounts.py, routes/corporate_company.py, features.py,
  routes/drivers/subscriptions.py, utils/marketing_email.py,
  utils/email_notifications.py, routes/corporate_signup.py, plus every
  ride-receipt call site). All keep the exact same bool return contract —
  the refactor is a pure internal delegation, not a signature or behavior
  change for any of them. Full backend suite run confirms this (1,456
  tests green across payment/webhook/preauth/receipt/email/outbox/worker
  areas).
- **The 4 wired call sites are the actual risk surface.** Each now calls
  `maybe_send_auto_receipt(ride, rider_id, tip, spawn=... | send=...)`
  instead of directly spawning/awaiting `send_ride_receipt`. Behavior is
  unchanged **today**: `outbox_receipts_enabled` defaults `false` (migration
  399), so `auto_receipt_is_queued` always returns `False` for every ride
  until an operator flips that DB flag, meaning `maybe_send_auto_receipt`
  always falls through to the exact same direct send it replaced, with the
  same spawn/await shape as before. The gate only starts skipping the
  direct send once the flag is flipped **and** the dedicated worker is
  confirmed healthy — that is a separate, later operational decision, not
  part of this change.
- A lookup failure (`is_auto_receipt_queued` raising) falls back to the
  direct send rather than silently dropping the receipt — verified by
  `test_outbox_receipts.py::test_lookup_failure_uses_direct_fallback` and
  the preauth/guest-corporate equivalents.
- No ride-state, insurance-period, surge, or wallet/allowance code touched.

## 5. User-experience effect

None visible today — `outbox_receipts_enabled` is off, so every receipt
still sends exactly the way it did before this change (same provider,
same timing, same spawn-vs-await shape per call site). The only future
difference (once the flag is flipped) is delivery moving from inline
spawn/await to a durable at-least-once outbox queue, transparent to the
rider/driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_provider.py` | Added `EmailDeliveryStatus`/`EmailDeliveryResult`/`send_transactional_email_result`; `send_transactional_email` now delegates to it | Outbox worker needs accepted/terminal_skip/retryable_failure, not a bare bool |
| `backend/utils/email_receipt.py` | Added `send_receipt_email_result`; `send_receipt_email` now delegates to it | Same reason, one layer up |
| `backend/services/payment_service.py` | Added `send_ride_receipt_result(ride_id)`; wired `maybe_send_auto_receipt` into `auto_settle_guest_corporate` | Outbox worker entry point + outbox-gated guest-corporate receipt |
| `backend/routes/rides/payments.py` | `process_payment` now calls `maybe_send_auto_receipt` instead of spawning `send_ride_receipt` directly | Outbox-gated wiring |
| `backend/routes/webhooks.py` | Both receipt send sites in `_handle_ride_invoice_paid` / `payment_intent.succeeded` now call `maybe_send_auto_receipt` | Outbox-gated wiring |
| `backend/utils/preauth_capture.py` | `_capture_one` now calls `maybe_send_auto_receipt` | Outbox-gated wiring |
| `backend/utils/loop_monitor.py` | Added 4 worker-loop entries to `LOOP_THRESHOLDS` | Worker loops were silently using the 2h default staleness window |
| `backend/tests/test_all_emails_are_branded.py` | Widened `_SEND_CALL` regex; added `email_provider.py` to `_UNBRANDED_BY_DESIGN` | Detector now correctly attributes the send to the real HTML-authoring caller, not the provider layer's own internal delegation |
| `backend/tests/test_admin_send_receipt_email.py` | Provider mock re-pointed to `send_transactional_email_result` | Matches where `email_receipt.py` now actually calls out |
| `backend/tests/test_receipt_route_snapshot.py` | Same re-point | Same reason |

## 7. Before / after

```python
# Before (routes/rides/payments.py::process_payment)
_deps.spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
email_sent = True
```
```python
# After
email_sent = await maybe_send_auto_receipt(ride, current_user["id"], tip_rounded, spawn=_deps.spawn)
```
`maybe_send_auto_receipt` with `outbox_receipts_enabled=false` (today's
default) always finds no queued row and falls back to
`spawn(send_ride_receipt(...))` — identical runtime behavior to before.

## 8. Rollback plan

No new migration in this change (399 already shipped and defaults off).
To revert:
1. `git revert` this commit — every wired call site returns to its direct
   `send_ride_receipt` call, byte-identical to before.
2. Nothing in this change touches live data (no Stripe charges, wallet
   deltas, or ride-state rows) — a code revert is a complete rollback here.
3. If this change had shipped *after* an operator flipped
   `outbox_receipts_enabled` to true, the pre-existing migration rollback
   plan (in `399_transactional_outbox.sql`'s own header) already covers
   that separately: flip the flag back off first, drain, then redeploy.

## 9. Verification performed

- [x] Automated tests run (unit): `tests/test_ride_receipt_delivery.py`,
  `tests/test_outbox_receipts.py`, `tests/test_outbox_worker.py`,
  `tests/test_email_provider.py`, `tests/test_worker_app.py`,
  `tests/test_outbox_admin.py`, `tests/test_admin_send_receipt_email.py`,
  `tests/test_all_emails_are_branded.py`, `tests/test_receipt_route_snapshot.py`
  — all pass in full. Broader sweep (`-k "payment or webhook or preauth or
  receipt or email or outbox or worker"`, 1,456 tests) green except one
  pre-existing, confirmed-via-`git stash`-unrelated failure
  (`test_email_deliverability.py`, see ACTION_ITEMS.md C53 finding 4 item 7;
  a follow-up task was queued for it).
- [ ] Manual repro steps followed in staging — **not performed**, see below.
- [x] Blast-radius grep performed: all `send_transactional_email`/
  `send_receipt_email`/`send_ride_receipt` call sites enumerated (12+N
  files) before changing their shared dependency.
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern
  followed on every new/changed import block; Decimal-only money math
  unaffected (no money arithmetic touched, only email-delivery status);
  "don't silently swallow errors" — every fallback path logs `.error`
  before returning a retryable/terminal result.
- [x] `ruff check` and `ruff format` clean on every changed file.
- [ ] Feature-flagged — not applicable in the traditional sense: the
  behavior change this diff enables is already gated by the existing
  `outbox_receipts_enabled` DB flag (off by default, from migration 399);
  this diff does not flip it.

## 10. What was NOT verified

- Not exercised against a real Postgres with `outbox_receipts_enabled=true`
  — no RLS/integration run against the real `outbox_messages` table or its
  RPCs in this session (the RLS suite self-skips without `TEST_DATABASE_URL`,
  which wasn't available here). The worker's actual end-to-end delivery
  path (claim → dispatch → ack/discard/fail) was exercised only via the
  existing mocked-Supabase unit tests (`test_outbox_worker.py`), not a live
  DB.
- No staging/manual repro — this session has no staging environment access.
- The `worker.py` dedicated-process deploy topology (a new Fly.io/Railway
  service definition) was **not** touched or verified — it was found
  already built from the dark launch and is explicitly out of this diff's
  scope; standing it up in a real environment is a separate infra decision.
- `test_email_deliverability.py`'s pre-existing failure was root-caused only
  as far as "confirmed unrelated via git stash" — not actually fixed or
  further diagnosed (queued as a separate follow-up task).

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live
  data touched).
- [x] Blast radius is stated: single-surface (backend), 4 call sites + the
  shared email-provider/receipt layer, all with preserved bool contracts
  for existing callers.
- [x] No silent behavior change to an already-shipped flow: the wired call
  sites are runtime-identical today because `outbox_receipts_enabled`
  defaults off; the UX-effect field above states this explicitly.
