# Runbook — Migrating Stripe Data from the Legacy Spinr App

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
