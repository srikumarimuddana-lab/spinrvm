# Runbook — Migrating Stripe Data from the Legacy Spinr App

> **Switching the SAME app from test keys to live keys is a different
> problem — see [test→live key cutover](#appendix--testlive-key-cutover-same-app)
> at the bottom. That data cannot be migrated, and this runbook's CSV importer
> is the wrong tool for it.**


**Goal:** carry drivers' Stripe Connect accounts (bank details, identity
verification) and riders' saved cards from the old Spinr app into this
platform, so drivers do **not** redo onboarding and riders keep their cards.

**Tooling:** the admin Stripe mapping import —
`POST /api/admin/stripe/import/validate`, `POST /api/admin/stripe/import/commit`,
`GET /api/admin/stripe/import/status?batch=…`
(`backend/routes/admin/stripe_import.py`, service:
`backend/services/stripe_mapping_import_service.py`). Requires a
**super_admin** token — module grants are not enough, because mapping
`stripe_account_id` sets a driver's payout destination.

Only drivers stamped by the legacy bulk import
(`legacy_import_metadata.source = legacy_saskatoon_driver_import`) are
matchable — natively-onboarded drivers are structurally out of reach of this
tool, by old ID and by phone alike.

**Prerequisite:** customers/drivers were already imported by the bulk driver
import (drivers matched via `legacy_import_metadata.old_driver_id`).

---

## Step 1 — Determine which scenario you are in

Everything depends on whether the old and new apps use the **same Stripe
platform account**.

Compare the platform account IDs behind each secret key:

```python
import stripe
print(stripe.Account.retrieve(api_key=OLD_APP_SECRET_KEY).id)
print(stripe.Account.retrieve(api_key=NEW_APP_SECRET_KEY).id)   # from app_settings.stripe_secret_key
```

(Or Dashboard → Settings → Business → Account ID in each account.)

- **Same `acct_…` → Scenario A.** Old `acct_…`/`cus_…` IDs are still valid.
- **Different → Scenario B.** Old IDs are useless on the new key; Stripe must
  copy the data across accounts first.

Also confirm both keys are the same mode (`sk_live_` vs `sk_test_`). A
wrong-scenario CSV fails safely: every row errors `not_accessible` at
validate, nothing is written.

## Step 2 — Build the mapping CSVs

**Drivers CSV** (`kind=drivers`) — one row per driver:

| column | required | notes |
|---|---|---|
| `stripe_account_id` | yes | `acct_…` valid on the NEW platform key |
| `old_driver_id` | one of these | preferred — matches the bulk-import provenance |
| `phone` | one of these | fallback match; E.164 or 10-digit |
| `old_stripe_account_id` | no | Scenario B provenance (the old platform's `acct_…`) |

**Riders CSV** (`kind=riders`):

| column | required | notes |
|---|---|---|
| `stripe_customer_id` | yes | `cus_…` valid on the NEW platform key |
| `phone` / `email` | at least one | phone preferred |
| `old_stripe_customer_id`, `old_user_id` | no | provenance only |

Scenario A: export these straight from the old app's database.
Scenario B: join the old export with the ID-mapping file Stripe returns
(Step 4/5) so the CSV carries the **new** IDs.

Max 200 rows per CSV — split larger sets into multiple batches.
CSVs contain PIPEDA-covered personal information; keep them in a secure
location and delete them when the migration is verified.

## Step 3 — Scenario A: validate → commit

```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "mapping_csv=@drivers_mapping.csv" -F kind=drivers -F batch=drivers-2026-07-a \
  https://api-spinr.spinr.ca/api/admin/stripe/import/validate
```

Fix anything under `errors` (commit refuses while any exist):

- `no_match` / `ambiguous_match` — fix the old_driver_id/phone in the CSV.
- `not_accessible` — the ID isn't valid on the new key: wrong scenario, wrong
  mode, or a typo.
- `conflict_existing` (riders, common) — the rider already used the new app,
  so a customer was lazily created. **Drop the row**; the rider keeps the
  new customer and re-adds their card. Only pursue a Stripe-side merge for
  high-value edge cases.
- `already_mapped` warnings are fine — re-runs converge; rows are skipped.
- `onboarding_incomplete` / `requirements_due` warnings are fine — the account
  is preserved; the driver finishes the leftover requirements in the driver
  app ("Set up payouts" → AccountLink on the mapped account; no duplicate
  account is created because `_ensure_stripe_account` short-circuits on the
  existing column).

Then re-send the same CSV to `/commit` (same `batch`!). For drivers, commit
starts a background KYC sync that mirrors real Stripe state (never the CSV)
into the `drivers.stripe_*` columns. Watch it converge:

```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api-spinr.spinr.ca/api/admin/stripe/import/status?batch=drivers-2026-07-a"
# → {"drivers": 42, "kyc_sync": {"ok": 40, "stripe_error": 2}, "payouts_enabled": 38, ...}
```

Retry `stripe_error` drivers individually with the admin
"Refresh from Stripe" button (`POST /api/admin/drivers/{id}/refresh-stripe-kyc`).

> **That button can now detach an account.** It calls `refresh_driver_kyc`
> with `retire_if_unreachable=True`, so if Stripe answers `resource_missing`
> or `PermissionError` for the mapped account, the mapping is nulled and the
> KYC mirror reset — the driver is then asked to re-onboard. That is the
> intended repair after a key-mode change, but it is destructive for a
> *deliberately* mapped Scenario-B account that has become unreachable for
> some other reason (wrong key loaded, platform access revoked).
>
> **Undo:** the old id is kept in `drivers.stripe_account_id_superseded`, and
> the mapping importer only ever fills NULL columns — so re-running the
> drivers CSV with that `acct_…` restores the mapping, and the next KYC sync
> re-populates the mirror from Stripe. The retire is also in `audit_logs`.
> Confirm the key is right before clicking it on a migrated driver.

## Step 4 — Scenario B: riders (Customers + cards)

Stripe copies Customers/PaymentMethods between accounts on request — card
data never transits our systems:

1. From the **old** account's Dashboard, file a *copy payment data to another
   Stripe account* request (Support → Data migrations). The new account must
   authorize receiving.
2. Stripe delivers an old→new ID mapping file when the copy completes.
3. Join it to the old app's export → riders CSV with the **new** `cus_…` IDs
   → Step 3 flow.

## Step 5 — Scenario B: drivers (Connect Express accounts)

Connected accounts can move between platforms only with Stripe's
assistance:

1. Both platform owners contact Stripe support requesting a
   platform-to-platform connected-account migration (same legal entity on
   both sides makes this straightforward).
2. **Approved:** Stripe provides the account mapping → drivers CSV with the
   new `acct_…` IDs → Step 3 flow. KYC sync then mirrors the real state — no
   driver re-onboarding.
3. **Declined:** drivers must re-onboard, but through the normal in-app flow
   (Express account is auto-created with their email prefilled). Send driver
   comms ahead of time; the friction is bank re-entry + ID verification.
   Do not import anything for drivers in this case.

## Step 6 — Post-checks

- Status endpoint counts converged (`kyc_sync.ok` ≈ batch size).
- Spot-check 2–3 drivers in the admin slideout: payouts enabled, ID on file.
- Optional: canary micro-payout to one consenting driver before the first
  real payday.
- Riders: spot-check that a migrated rider sees their saved card in the app
  (payment-sheet lists PaymentMethods off `users.stripe_customer_id`).

## Rollback

Safe because the importer only ever fills NULL columns (commit-time races are
reported as `conflicts`, never overwritten):

```sql
UPDATE drivers
SET stripe_account_id = NULL,
    legacy_import_metadata = legacy_import_metadata - 'stripe_migration'
WHERE legacy_import_metadata->'stripe_migration'->>'batch' = '<batch>';

UPDATE users
SET stripe_customer_id = NULL,
    legacy_import_metadata = legacy_import_metadata - 'stripe_migration'
WHERE legacy_import_metadata->'stripe_migration'->>'batch' = '<batch>';
```

## Out of scope (v1)

- `corporate_accounts.stripe_customer_id` — the riders path generalizes if
  needed (future `kind=corporate`).
- Payment/charge history — stays queryable in the old Stripe account; do not
  attempt to move it.
- Active subscriptions in the old account — cancel/recreate explicitly if any
  exist; Stripe's customer copy does not migrate subscriptions.

---

# Appendix — test→live key cutover (same app)

This is **not** a migration, and the CSV importer above cannot help. Read this
before trying to "sync cards back" after going live.

## What happened

Stripe object IDs are mode-scoped: a `cus_…` minted with `sk_test_…` does not
exist under `sk_live_…`, and neither does an `acct_…`. The prefixes are
identical in both modes, so the ID gives no hint. When
`app_settings.stripe_secret_key` flips from test to live, every stored
`users.stripe_customer_id`, `drivers.stripe_account_id`, and
`corporate_accounts.stripe_customer_id` becomes a dangling pointer and Stripe
answers `resource_missing` on every call.

Symptoms, all at once:

| Surface | Symptom |
|---|---|
| Rider | Saved cards fail to load; adding a card also fails |
| Driver | "Set up payouts" dead-ends; admin still shows *payouts enabled* |
| Admin | "Could not load cards from Stripe" on the user detail panel |
| Corporate | Wallet auto-topup and subscription charges error |

## What cannot be recovered — read this first

**Test-mode cards and Connect accounts cannot be migrated to live mode.**

- Stripe sandboxes test data completely and offers **no test→live copy path**.
  Their data-migration service (Step 4 above) is live-account→live-account only.
- Any card saved in test mode was a test PAN (`4242…`). There is no real card
  behind it. Nothing of value is lost.
- A test-mode Connect account holds no real bank details and no verified
  identity. The driver must onboard for real regardless.

So the goal is **not** to carry data across. It is to get every account into a
clean live state and tell people what to expect.

## What the backend now does automatically

Since migration 286, identities are stamped with their mode and repaired on the
paths where the affected user is present:

- **Riders** — the first time they open the payment screen, add a card, or top
  up the wallet, the stranded customer is replaced under the live key, the dead
  ID is archived to `users.stripe_customer_id_superseded`, and
  `default_payment_method` is cleared. They see an empty card list and add a
  real card. No admin action needed.
- **Drivers** — tapping "Set up payouts" (or an admin pressing *Refresh from
  Stripe*) retires the dead account, resets the KYC mirror so the app stops
  claiming a bank is linked, and mints a fresh Express account to onboard into.
  The driver re-enters bank details and re-verifies identity. **This is
  unavoidable** — budget for driver comms.
- **Corporate** — repaired where an admin is present; the auto-topup loop
  retires rather than replaces. See "Corporate accounts" below — the company's
  billing card still has to be re-added.

Kill switch: `stripe_reprovision_stale_ids = false` in app_settings stops all of
it within 60 s, no redeploy.

## Step 1 — measure the damage

```bash
curl -s -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  https://api-spinr.spinr.ca/api/admin/stripe/mode-audit
```

```json
{
  "current_key_mode": "live",
  "kinds": {
    "riders":  {"live": 12, "test": 0, "unverified": 340, "known_stranded": 0},
    "drivers": {"live": 3,  "test": 0, "unverified": 41,  "known_stranded": 0}
  }
}
```

`unverified` is everything created before mode tracking existed — their mode was
never recorded and cannot be inferred from the ID. Right after a cutover this is
where essentially everyone sits.

## Step 2 — classify the unverified rows

```bash
curl -s -X POST -H "Authorization: Bearer $SUPER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"riders","limit":100}' \
  https://api-spinr.spinr.ca/api/admin/stripe/mode-audit/probe
```

```json
{"probed": 100, "resolvable": 4, "stranded": 96, "inconclusive": 0,
 "stranded_ids": ["cus_…", "…"]}
```

- `resolvable` — reachable on the live key; stamped, nothing to do.
- `stranded` — repaired on the user's next interaction.
- **`inconclusive` — stop.** These are auth/rate-limit/network failures, not
  evidence about the objects. A non-zero count here usually means the key itself
  is wrong (revoked, or belonging to a different Stripe account). Fix the key
  before reading anything else into these numbers.

Re-run until `unverified` stops shrinking. The probe never re-provisions or
retires — it only classifies and stamps.

## Step 3 — communicate before people hit it

Nothing in the apps explains why a saved card or linked bank disappeared, so
send comms ahead of the first live ride:

- **Riders:** "You'll need to re-add your payment card the next time you book."
- **Drivers:** "You'll need to set up payouts again — bank details and ID
  verification — before your next payday." This one is real friction; give
  notice, not a surprise.

## Step 4 — verify

- One rider: open the payment screen → empty list, not an error → add a card →
  it persists and a booking preauth succeeds.
- One driver: "Set up payouts" → Stripe onboarding opens on a **new** account →
  finish → `/drivers/stripe-sync` reports `payouts_enabled: true`.
- Admin: user detail card panel loads (empty, not an error); driver slideout no
  longer claims payouts are enabled for un-onboarded drivers.
- Re-run the audit: `known_stranded` trending to zero.

## Corporate accounts

Corporate is repaired too, but deliberately in two different ways:

- **Where an admin is present** — KYB approval, subscription assignment,
  internal-admin top-up, company self-serve top-up — the stranded customer is
  replaced automatically, like a rider's. Safe: a fresh customer has no card, so
  nothing is charged until someone adds one.
- **The auto-topup loop** — retires the dead customer (archives the ID, nulls
  the column) and **never** creates a replacement. Nobody is present to consent
  to a new payment identity, and a fresh customer would carry no card, so the
  charge could not have succeeded anyway. This stops the 144-failures-a-day
  retry loop and leaves a state the next admin action repairs.

**The company still has to re-add its billing card.** The old card lived on the
customer that is gone. Until a billing admin adds one, top-ups return a clean
422 ("No payment method on file") and auto-topup skips. Include corporate
billing admins in the comms at Step 3, and watch the logs for
`Retired unreachable corporate Stripe customer` — each one is a company that
cannot fund its wallet until someone acts.

## Rolling back

`stripe_reprovision_stale_ids = false` stops further repair. Identities already
replaced keep their predecessor in `*_superseded` — see the rollback SQL in
`docs/change-log/2026-08-09-stripe-test-to-live-identity-drift.md`. Note that
restoring those pointers restores *unusable test-mode IDs*: it undoes this
feature's writes, it does not restore working payments.

## Avoiding this next time

Rotating `stripe_secret_key` across modes is a **data event**, not a config
change. Treat a mode change like a migration: announce it, expect every stored
Stripe identity to be invalidated, and plan rider/driver comms before the flip.
Rotating a key *within* the same mode (live→live key rotation) is safe and
changes nothing — object IDs stay valid.
