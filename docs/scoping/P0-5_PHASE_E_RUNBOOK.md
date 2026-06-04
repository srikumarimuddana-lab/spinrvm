# P0-5 Phase E — Runbook for Stripe Test-Mode Validation

**Branch:** `claude/p0-5-stripe-card-charge`
**Prerequisite phases:** A (backend), B (tests), C (rider-app UX), D (E2E) — all shipped.
**Purpose:** The final gate before merging P0-5 to main. Runs against a real
Stripe test account + a deployed staging backend to catch things mocks can't.

This runbook is the **single source of truth** for what has to happen before
P0-5 ships. Complete all sections, tick each box. Anything unticked is a
merge-blocker.

---

## 1. Prerequisites

### 1.1 Infrastructure

- [ ] Staging backend deployed and reachable (note URL: `__________`)
- [ ] Staging backend Stripe keys configured. Verify via:
      ```
      curl -s $STAGING_URL/api/v1/settings | jq .stripe_publishable_key
      ```
      Should return `pk_test_...` (not empty, not `pk_live_...`).
- [ ] `stripe_secret_key` and `stripe_webhook_secret` set in the `settings`
      admin row (DB-side; not env var). Confirmed with an admin login to
      `/admin/settings`.
- [ ] Stripe webhook endpoint in the dashboard points at
      `$STAGING_URL/api/v1/webhooks/stripe` and is in "Listening" state.
- [ ] The Stripe account is in **test mode** (toggle top-right of dashboard).
      Live mode validation is a separate, later runbook.

### 1.2 Test data

- [ ] At least one test rider account with:
      - Phone authenticated
      - Profile complete
      - A `stripe_customer_id` on the user row (populated by calling
        `POST /payments/cards` once via the rider-app, which runs
        `get_or_create_stripe_customer()`)
- [ ] Rider-app build pointed at staging (either `yarn start` in dev or a
      TestFlight/Play Internal build). Confirm `/settings` returns the
      staging `stripe_publishable_key`.

### 1.3 Access

- [ ] You can log into the rider-app as the test rider
- [ ] You can read the `rides` and `users` tables in staging (Supabase
      console, psql, or admin API)
- [ ] You can view the Stripe dashboard (Payments + Webhooks + Events)

---

## 2. Stripe test cards (reference)

| Card number | Expected outcome | PI status | Rider UI |
|---|---|---|---|
| `4242 4242 4242 4242` | Success | `succeeded` | "Ride Complete" → tabs |
| `4000 0000 0000 0002` | Generic decline | raises `CardError` | Decline alert + Change Card CTA |
| `4000 0000 0000 9995` | Insufficient funds | raises `CardError` with `decline_code=insufficient_funds` | Decline alert |
| `4000 0025 0000 3155` | Requires 3DS authentication | `requires_action` | 3DS sheet → success |
| `4000 0082 6000 3178` | Requires 3DS, then declines | `requires_action` → `CardError` | 3DS sheet → decline alert |

Any valid future expiry + any 3-digit CVC + any postal code works.

---

## 3. The three core scenarios

### 3.1 Scenario A — Happy path (`4242 4242 4242 4242`)

1. [ ] In the rider-app, go to **Manage Cards**. Remove any existing cards.
2. [ ] Add card `4242 4242 4242 4242`. Set as default.
3. [ ] Take a ride end-to-end: request → matched (or simulated matched) →
       pickup → start → complete. Driver can be a test driver account.
4. [ ] On the ride-completed screen, verify card shown is `•••• 4242`.
5. [ ] (Optional) Add a tip.
6. [ ] Tap **Pay & Done**.
7. [ ] Expected outcome:
       - [ ] Brief loader; screen navigates to home tabs
       - [ ] Ride row in DB has `payment_status="paid"`, `payment_intent_id=pi_...`
       - [ ] Stripe dashboard shows a **Succeeded** payment with matching metadata
             `ride_id`, `rider_id`, `source=ride_completion_charge`
       - [ ] Receipt email delivered (if Resend is configured in staging)

### 3.2 Scenario B — Card declined (`4000 0000 0000 0002`)

1. [ ] Go to **Manage Cards**. Set `4000 0000 0000 0002` as default.
2. [ ] Complete another ride up to the ride-completed screen.
3. [ ] Tap **Pay & Done**.
4. [ ] Expected outcome:
       - [ ] **"Card declined"** alert appears with:
             - A **Change Card** button → taps routes to `/manage-cards`
             - A **Cancel** button
       - [ ] Screen does NOT navigate away — rider stays on ride-completed
       - [ ] Ride row in DB has `payment_status="failed"` (or remains usable
             for retry — see `payment_retry.py` loop for eventual cleanup)
       - [ ] Stripe dashboard shows a **Failed** payment with decline_code
5. [ ] Change card to `4242 ...`, tap **Pay & Done** again:
       - [ ] Succeeds; DB flips to `paid`; Stripe shows a new Succeeded payment
             (different `payment_intent_id` — we do NOT retry the failed PI)

### 3.3 Scenario C — 3DS challenge (`4000 0025 0000 3155`)

1. [ ] Go to **Manage Cards**. Set `4000 0025 0000 3155` as default.
2. [ ] Complete another ride.
3. [ ] Tap **Pay & Done**.
4. [ ] Expected outcome:
       - [ ] Native Stripe 3DS sheet pops up
       - [ ] Tap "Complete" in the sheet (the test card accepts anything)
       - [ ] Sheet dismisses; screen navigates to home tabs
       - [ ] Ride row in DB: `payment_status="paid"`
       - [ ] Stripe dashboard: payment shows "Authenticated" + "Succeeded"
             with a single `payment_intent_id`

### 3.4 Scenario C-alt — 3DS cancelled by user

1. [ ] Set `4000 0025 0000 3155` as default (reuse if already set).
2. [ ] Complete a ride, tap **Pay & Done**.
3. [ ] When the 3DS sheet appears, tap **Cancel** (or **Fail**).
4. [ ] Expected outcome:
       - [ ] **"Authentication failed"** alert with Change Card + Try Again buttons
       - [ ] Screen stays on ride-completed
       - [ ] Ride row: `payment_status="requires_action"` (or `"failed"`)
       - [ ] Can retry with Try Again — re-runs the 3DS sheet

---

## 4. Webhook replay + idempotency

This is the critical async safety net — if our sync response is lost, the
webhook must backfill, and a replayed webhook must be a no-op.

### 4.1 Replay a `payment_intent.succeeded`

1. [ ] In Stripe dashboard, go to **Developers → Events**
2. [ ] Pick the Succeeded event from Scenario A
3. [ ] Click **Resend** (or use `stripe events resend evt_xxx` CLI)
4. [ ] Expected:
       - [ ] First delivery (already-processed): backend returns 200, logs
             show `claim_stripe_event` deduped the replay
       - [ ] No second charge; ride row unchanged (already `paid`)
       - [ ] `stripe_events` table has exactly one row for this `event_id`

### 4.2 Webhook-arrives-before-sync-response (simulation)

Harder to reproduce naturally. Confirm via unit test:
- [ ] `backend/tests/test_stripe_charge.py` has `TestIdempotencyKey` — pins
      that the same `idempotency_key` is used twice; Stripe dedupes; the
      DB atomic `processing` guard prevents the handler from double-writing.

---

## 5. Verify backend contract

### 5.1 `drivers.py::complete_ride` does NOT write `payment_status`

Run against staging:

```
rideId=<some-test-ride>
curl -X POST $STAGING_URL/api/v1/drivers/rides/$rideId/complete \
  -H "Authorization: Bearer $DRIVER_JWT"
# Then inspect the row:
psql ... -c "SELECT status, payment_status FROM rides WHERE id='$rideId';"
```

- [ ] Expected: `status=completed`, `payment_status=pending` (NOT `completed`)

If `payment_status` flips to anything other than its prior value after the
complete call, the P0-5 premature-write fix regressed.

### 5.2 `/settings` endpoint returns the publishable key

```
curl -s $STAGING_URL/api/v1/settings | jq .stripe_publishable_key
```

- [ ] Returns `pk_test_...` matching what the rider-app shows in its
      StripeProvider init logs

---

## 6. Runbook sign-off

- [ ] All items in §§ 1, 3, 4, 5 are ticked
- [ ] No open Stripe dashboard "disputed" or "uncaptured" events from test runs
- [ ] `stripe_events` table entries are all unique on `event_id`
- [ ] No rides stuck in `payment_status=processing` or `requires_action`
      for >15 min from this test session (check with the query in §8 below)
- [ ] At least one decline + one 3DS + one success captured in a single
      test session (not stale from weeks ago)

Signed off by: `__________`   Date: `__________`

---

## 7. Rollback plan

If Phase E surfaces issues we can't fix in-branch:

1. Do NOT merge the P0-5 branch.
2. Revert any partial deploy.
3. Current production (main) is unchanged — the stub behavior (auto-mark
   paid without charging) is still in place. That's a known revenue bug,
   but it's the status quo and blocks no one.
4. Open issues for the Phase E findings; scope follow-ups on this same
   branch or a new one.

## 8. Useful queries

```sql
-- Rides stuck in a transient payment state
SELECT id, rider_id, payment_status, updated_at
FROM rides
WHERE payment_status IN ('processing', 'requires_action')
  AND updated_at < NOW() - INTERVAL '15 minutes'
ORDER BY updated_at DESC;

-- Recent payment intents from the test rider
SELECT id, payment_status, payment_intent_id, total_fare, updated_at
FROM rides
WHERE rider_id = '<test-rider-id>'
  AND created_at > NOW() - INTERVAL '1 day'
ORDER BY created_at DESC;

-- Stripe event dedupe table — one row per event_id
SELECT event_id, event_type, processed_at
FROM stripe_events
ORDER BY processed_at DESC
LIMIT 20;
```

## 9. See also

- `scripts/smoke/stripe_charge_smoke.py` — scripted counterpart to this
  runbook. Run it once staging is up and you want to exercise the
  Stripe-side parameter shape without poking the rider-app by hand.
- `docs/scoping/P0-5_STRIPE_CARD_CHARGE.md` — original scope doc
- `backend/utils/stripe_charge.py` — the helper under test
