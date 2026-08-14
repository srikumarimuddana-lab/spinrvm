# Manual test — Stripe identity drift repair

How to verify the test→live identity-drift work by hand, before trusting it in
production. Covers riders, drivers, corporate, and the admin audit.

**Everything here runs on TEST keys.** You do not need to touch live money, and
you do not need to flip a key to reproduce the bug — see below.

---

## The trick that makes this safe

The bug fires when a stored Stripe ID is unreachable on the running key. There
are exactly two ways the code detects that, and **both can be simulated with a
test key by editing one column**:

| Detection path | How to simulate | What it exercises |
|---|---|---|
| **Mode mismatch** (cheap, no Stripe call) | Set `*_mode = 'live'` on a row while running `sk_test_…` | `stale_by_mode` → repair |
| **`resource_missing`** (unstamped rows) | Set the ID column to a bogus `cus_…` / `acct_…` | `is_missing_on_key` → repair |

The second is the important one — it is the state **every one of your existing
rows is in** (they predate mode tracking, so their `*_mode` is NULL).

> You **cannot** reproduce this by setting a live key in staging:
> `routes/admin/settings.py` rejects any key not starting with `sk_test_` when
> `ENV != production`. That guard is deliberate; don't work around it.

---

## Step 0 — apply the migrations (required, do this first)

Nothing below works until migrations **286, 287 and 288** exist. **None has
ever been run.** 287 adds the kill-switch column (without it the flag is stuck
on and the rollback path in Step 6 does not work); 288 adds the bank-payout and
ledger tables used in Step 5b.

```bash
cd backend
# Preview first — prints what would run, writes nothing
python scripts/migrate.py --dry-run

# Then apply
python scripts/migrate.py
```

Verify the columns and indexes landed:

```sql
SELECT table_name, column_name FROM information_schema.columns
WHERE (table_name IN ('users','drivers','corporate_accounts')
       AND (column_name LIKE '%\_mode' OR column_name LIKE '%superseded%'))
   OR (table_name = 'settings' AND column_name = 'stripe_reprovision_stale_ids')
ORDER BY table_name, column_name;
-- expect 10 rows: 3 per identity table, plus the settings flag

SELECT indexname FROM pg_indexes WHERE indexname LIKE '%stripe%mode%';
-- expect 3
```

Confirm the kill switch is actually writable (this is what 287 fixes):

```sql
UPDATE settings SET stripe_reprovision_stale_ids = true WHERE id = 'app_settings';
-- must succeed. PGRST204 / "column does not exist" => 287 did not apply.
```

The indexes are `CREATE INDEX CONCURRENTLY`, so the runner switches to
autocommit and runs statement-by-statement. If it fails partway, re-running is
safe (every statement is `IF NOT EXISTS`), but check for an `INVALID` index
first:

```sql
SELECT c.relname FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid
WHERE NOT i.indisvalid AND c.relname LIKE '%stripe%';
-- any row here: DROP INDEX <name>; then re-run the migration
```

---

## Step 1 — rider cards (the main symptom)

Pick a **staging** rider who already has a Stripe customer. Note their ids:

```sql
SELECT id, stripe_customer_id, stripe_customer_id_mode, default_payment_method
FROM users WHERE id = '<RIDER_ID>';
```

### 1a. `resource_missing` path — what your real rows will hit

Point them at a customer that does not exist:

```sql
UPDATE users
SET stripe_customer_id = 'cus_definitelyNotReal',
    stripe_customer_id_mode = NULL,
    default_payment_method = 'pm_definitelyNotReal'
WHERE id = '<RIDER_ID>';
```

Now open **Manage cards** in the rider app (or curl it):

```bash
curl -s -H "Authorization: Bearer $RIDER_TOKEN" \
  https://<host>/api/v1/payments/cards
```

**Expected:** `200` with `[]` — an empty, working card list. **Not** a 502.

Confirm the repair landed:

```sql
SELECT stripe_customer_id, stripe_customer_id_mode,
       stripe_customer_id_superseded, default_payment_method
FROM users WHERE id = '<RIDER_ID>';
```

- `stripe_customer_id` → a **new** `cus_…` (verify it exists in your Stripe test dashboard)
- `stripe_customer_id_mode` → `test`
- `stripe_customer_id_superseded` → `cus_definitelyNotReal`
- **`default_payment_method` → NULL** ← the one that matters most. A `pm_` is scoped to its customer; if this is still set, settlement would try to charge a card that no longer exists.

Then **add a card in the app** and confirm it saves and appears.

### 1b. Mode-mismatch path (no Stripe call at all)

```sql
UPDATE users SET stripe_customer_id_mode = 'live' WHERE id = '<RIDER_ID>';
```

Open Manage cards again. Same repair, but the logs show
`reason=mode_mismatch` instead of `resource_missing`, and no `Customer.retrieve`
is made.

### 1c. Add-card recovery (this was the dead end)

Re-break the row as in 1a, then **skip the cards screen** and go straight to
adding a card. It must succeed — before this work, attaching addressed the same
dead customer and failed, leaving the rider with no way out.

### 1d. Booking + settlement

Re-break the row, then book and complete a ride without opening the cards
screen. `/payments/create-intent` should repair inline rather than failing at
trip end.

### 1e. Kill switch

```sql
UPDATE settings SET stripe_reprovision_stale_ids = false WHERE id = 'app_settings';
```

Wait 60 s (settings cache TTL), re-break a row, open Manage cards.

**Expected:** `503` "Your payment profile needs attention", and **the row is
unchanged** — no new customer, nothing superseded. Turn it back on when done.

### 1f. The safety property — must NOT repair

This is the one worth being fussy about. A *healthy* customer must survive an
unrelated error.

With the rider's row healthy (real `cus_…`), submit a bogus payment method:

```bash
curl -s -X POST -H "Authorization: Bearer $RIDER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payment_method_id":"pm_doesNotExist"}' \
  https://<host>/api/v1/payments/cards
```

**Expected:** an error about the *payment method* — and
`stripe_customer_id` **unchanged**, `stripe_customer_id_superseded` still NULL.
If the customer got replaced here, stop and report it: that is the failure mode
that would wipe real riders' saved cards.

---

## Step 2 — driver payouts

```sql
SELECT id, stripe_account_id, stripe_account_id_mode, stripe_account_onboarded,
       stripe_payouts_enabled, stripe_id_number_provided
FROM drivers WHERE id = '<DRIVER_ID>';
```

Break it:

```sql
UPDATE drivers
SET stripe_account_id = 'acct_definitelyNotReal', stripe_account_id_mode = NULL
WHERE id = '<DRIVER_ID>';
```

### 2a. Status sync tells the app to re-onboard

```bash
curl -s -X POST -H "Authorization: Bearer $DRIVER_TOKEN" \
  https://<host>/api/v1/drivers/stripe-sync
```

**Expected:** `200` with `"reonboarding_required": true` and
`"payouts_enabled": false` — **not** a 502.

Confirm the mirror was reset (this is what stops the app lying about a linked bank):

```sql
SELECT stripe_account_id, stripe_account_id_superseded, stripe_account_onboarded,
       stripe_details_submitted, stripe_payouts_enabled,
       stripe_id_number_provided, stripe_id_number_last4, stripe_tos_accepted_at
FROM drivers WHERE id = '<DRIVER_ID>';
```

All the booleans `false`, `stripe_account_id` NULL, superseded populated.

Then check the driver app's payout screen shows **"Set up payouts"**, not a bank.

### 2b. Re-onboarding mints a live account

```bash
curl -s -X POST -H "Authorization: Bearer $DRIVER_TOKEN" \
  https://<host>/api/v1/drivers/stripe-onboard
```

**Expected:** a Stripe onboarding URL, and `drivers.stripe_account_id` now
holds a **new** `acct_…` with `stripe_account_id_mode = 'test'`. Confirm in the
Stripe dashboard that exactly **one** new account was created — not two.
(Also worth exercising the embedded flow, `POST /api/v1/drivers/stripe-account-session`,
which takes the same repair path.)

### 2c. Payout history survives

```bash
curl -s -H "Authorization: Bearer $DRIVER_TOKEN" \
  "https://<host>/api/v1/drivers/payouts?limit=20"
```

**Expected:** the driver's past payouts are still listed. Payout rows are keyed
on `driver_id`, not on the Stripe account, so retiring never hides them. This
is the check that answers "will the driver lose his history" — he does not.

---

## Step 3 — corporate

### 3a. Admin top-up repairs in place

```sql
UPDATE corporate_accounts
SET stripe_customer_id = 'cus_corpNotReal', stripe_customer_id_mode = NULL
WHERE id = '<COMPANY_ID>';
```

```bash
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"amount":100}' \
  https://<host>/api/admin/corporate-accounts/<COMPANY_ID>/wallet/topup
```

**Expected:** a new customer is provisioned and you get a payment-method error
(`422` / "no payment method on file") — **not** "no such customer" and **not**
the old `409 "Company has no Stripe customer"`. That 409 was removed precisely
so a company retired by the auto-topup loop isn't locked out forever.

Verify: `stripe_customer_id` is new, `stripe_customer_id_superseded` = the bogus id.

### 3b. Auto-topup retires instead of retrying forever

Set the row bogus again, enable auto-topup on the wallet with a balance below
threshold, and wait one tick (≤10 min) or restart the backend to force one.

**Expected in logs:** `Retired unreachable corporate Stripe customer`, exactly
**once**. Then:

```sql
SELECT stripe_customer_id, stripe_customer_id_superseded FROM corporate_accounts
WHERE id = '<COMPANY_ID>';
-- stripe_customer_id NULL, superseded populated
```

Watch for a second tick — it must skip silently, **no further Stripe calls**.
That is the whole point: 144 failures a day become one.

Finally, re-add a billing card as the company admin and confirm top-up works.

---

## Step 4 — admin audit endpoints (super_admin token required)

```bash
curl -s -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  https://<host>/api/admin/stripe/mode-audit
```

Shows `current_key_mode` and, per table, how many identities are
`live` / `test` / `unverified`. Right after the migration, essentially
everything is `unverified`.

```bash
curl -s -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"kind":"riders","limit":25}' \
  https://<host>/api/admin/stripe/mode-audit/probe
```

**Expected:** `resolvable` + `stranded` + `inconclusive` = `probed`, and the
`unverified` count in the audit drops on the next call (resolvable rows get
stamped).

**`inconclusive > 0` means stop.** That is auth/network, not "the identity is
gone" — usually the key itself is wrong. Do not read anything else into the
numbers until it is zero.

Also confirm the probe is audited (it silently wasn't, before):

```sql
SELECT action, entity_type, entity_id, created_at FROM audit_logs
WHERE action = 'stripe_mode_probe' ORDER BY created_at DESC LIMIT 5;
```

And check a rider's admin detail panel with a broken customer — the card panel
should name the key-mode cause, not the generic "Could not load cards".

---

## Step 5 — payout-history sync (drivers' history into our tables)

Only meaningful if the superseded account is reachable on the current key
(i.e. a live→live platform migration). After a test→live cutover the old
account is unreachable by definition, and you should see the warning path.

```bash
curl -s -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch":"payout-sync-test-1","driver_ids":["<DRIVER_ID>"]}' \
  https://<host>/api/admin/stripe/payout-sync/validate
```

**Expected:** a driver with only a superseded account is still a target (before
this work they were dropped entirely). An unreachable **superseded** account
produces an `account_not_accessible` *warning*; an unreachable **current**
account is an *error* that blocks commit — that asymmetry is deliberate, so a
wrong key can't produce a "successful" sync of zero T4A income.

### 5b. Bank payouts + full ledger (migration 288)

This is the *other* leg — what happened inside the driver's connected account.

```bash
curl -s -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"driver_ids":["<DRIVER_ID>"],"year":2025}' \
  https://<host>/api/admin/stripe/connect-ledger/sync
```

**Expected:** counts for `payouts_upserted` / `ledger_upserted`. Re-run it —
the numbers should stay the same and no rows should duplicate (the Stripe
object id is the primary key).

Then read them back as the driver:

```bash
curl -s -H "Authorization: Bearer $DRIVER_TOKEN" \
  https://<host>/api/v1/drivers/stripe/bank-payouts
curl -s -H "Authorization: Bearer $DRIVER_TOKEN" \
  "https://<host>/api/v1/drivers/stripe/ledger?limit=50"
```

**The check that matters** — these must not inflate income:

```sql
-- T4A must be unchanged by the ledger sync. Record it before and after.
-- (GET /api/v1/drivers/t4a/2025 → total_earnings)

-- And the ledger nets to the balance CHANGE, not to income:
SELECT SUM(amount) FROM driver_stripe_ledger WHERE driver_id = '<DRIVER_ID>';
-- A driver who has been fully paid out nets to ~0, NOT to their earnings.
```

If the T4A total moved after running this sync, stop — something is summing a
display table as income, which would over-report to the CRA.

---

## Step 6 — before you do this on production

1. **Take a backup / snapshot.** The repair writes to `users`, `drivers`, and
   `corporate_accounts`.
2. Run the audit endpoint first to size the blast radius.
3. Have the kill switch ready:
   `UPDATE settings SET stripe_reprovision_stale_ids = false WHERE id = 'app_settings';`
   (60 s to take effect, no redeploy).
4. **Send rider and driver comms first.** Riders re-add a card; drivers redo
   bank + ID verification. Neither is recoverable and both are visible
   immediately.
5. Roll to a small canary group if you can — the repair is per-user and lazy,
   so it naturally affects only people who open the relevant screen.

### Cleaning up after testing

```sql
-- Undo a simulated break (staging only)
UPDATE users SET stripe_customer_id = stripe_customer_id_superseded,
                 stripe_customer_id_superseded = NULL,
                 stripe_customer_id_superseded_at = NULL,
                 stripe_customer_id_mode = NULL
WHERE id = '<RIDER_ID>' AND stripe_customer_id_superseded IS NOT NULL;
```

The Stripe customers/accounts created during testing are harmless test-mode
objects; delete them in the Stripe test dashboard if you want a clean slate.

---

## What this does NOT prove

- Nothing here exercises a **real live key**. The error *shapes*
  (`resource_missing`, `PermissionError`, `livemode`) come from Stripe's
  documented contract; the simulations above produce genuine Stripe errors, but
  from test mode.
- **Concurrency is untested.** The conditional writes and idempotency keys are
  argued correct and unit-tested single-threaded; no multi-replica race was run.
- The driver app **does not yet render** `reonboarding_required` — the backend
  returns it, but the driver currently sees the not-set-up state with no
  explanation of why their bank vanished. Wire that copy before the cutover.
