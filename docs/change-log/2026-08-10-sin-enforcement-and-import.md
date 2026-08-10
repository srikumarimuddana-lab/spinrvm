# 2026-08-10 — SIN enforcement gates, immutability, admin correction, bulk import

Follow-on to `2026-08-10-driver-sin-collection.md` (which added collection +
encryption + T4A wiring + Stripe prefill). This entry adds the **enforcement**
around that collection and the migration path for pre-existing drivers.

## Issue/gap identified

SIN collection existed but nothing enforced it: the driver-app checklist
*ordered* SIN before Stripe onboarding, but a driver could tap "Connect payout
account" with no SIN on file (prefill then had nothing to send and Stripe's
form asked instead — the exact double-ask/no-copy failure the ordering exists
to prevent). A driver could also silently overwrite their own SIN at any time,
there was no path to correct a wrong SIN under audit, and ~existing drivers
(whose SIN/GST the operator already holds from the old platform) had no way
into the new columns except retyping in the app.

## Root cause

Round 1 shipped collection without gates — order was expressed only as UI
layout, and mutability was the default of a generic profile-update endpoint.

## Fix/remediation

1. **Onboarding gate** — `/drivers/stripe-onboard` and
   `/drivers/stripe-account-session` return **422** until a SIN is on file:
   our Vault copy (`drivers.sin`) **or** the legacy Stripe-held copy
   (`stripe_id_number_provided`) — legacy drivers are never re-asked.
2. **Payout gate widened** — `_require_sin_for_payout` now accepts the Vault
   copy too (it used to insist on Stripe's flag alone).
3. **Immutability** — a second `sin` write via `PUT /drivers/me` → **403**.
4. **Admin correction** — `POST /admin/drivers/{id}/update-sin`
   (super_admin, mandatory reason, audit **before** write, forced
   `Account.modify` re-push to Stripe with the outcome reported).
5. **Bulk import** — `POST /admin/tax-ids/import/{validate,commit}`
   (super_admin): CSV `phone,sin,gst_bn`, NULL-only fill, Vault-encrypt,
   background Stripe push for drivers holding a Connect account.
6. **Driver app** — Stripe step locked with "Add your SIN first" until the
   gate passes; on-file SIN row reads "Contact support to change it".

## Risk & impact on existing functionality

**Blast radius (grepped every caller):**

- `_require_sin_for_payout` — called by `request_payout` and
  `request_instant_payout` only. Change is *permissive* (accepts one more
  proof), so no driver who could pay out before loses the ability.
- `_require_sin_for_onboarding` — new; wired into the two onboarding
  endpoints only. **Restrictive**: a driver mid-onboarding with no SIN on
  file (and not legacy-flagged) now gets 422 where they previously got a
  Stripe link. This is the intended behaviour change. The app shows the
  locked step, but a driver on an old app build tapping Connect will see the
  422 toast text.
- `PUT /drivers/me` sin branch — 403 only when a SIN already exists;
  first-write, auto-create, and every other profile field unchanged
  (pinned by test `test_other_fields_still_editable_when_sin_locked`).
- `prefill_sin_to_stripe` — now also called by the importer's background
  push with a synthetic `{id, sin}` dict; it only reads those two keys.
- `with_account_repair`, ride/dispatch/fare code paths — untouched.
- Admin router mount — one new sub-router behind `require_super_admin`;
  no existing route shapes changed.

**Could regress:** a legacy driver whose SIN is at Stripe but whose
`stripe_id_number_provided` mirror is stale-false would be wrongly gated at
onboarding — mitigated because that flag is refreshed by `stripe-sync`, the
account.updated webhook, and the admin KYC refresh; and such a driver is
usually already onboarded (gate not hit).

## User experience effect

- **Driver (visible mid-session):** the payout checklist's Stripe row shows a
  lock + "Add your SIN first — it unlocks this step" until SIN is entered;
  an on-file SIN is no longer editable ("Contact support to change it").
  A driver on an outdated build sees a 422 error toast instead of the lock.
- **Internal admin:** two new capabilities (update-sin, tax-ID import),
  both super_admin-only. No existing admin screen changes behaviour.
- **Rider / corporate admin:** none.

## Files modified

| file | what changed | why |
|---|---|---|
| `backend/routes/drivers/payouts.py` | `_sin_on_file` + `_require_sin_for_onboarding` added; gates wired into both onboarding endpoints; `_require_sin_for_payout` repointed | enforce SIN-before-Stripe |
| `backend/routes/drivers/profile.py` | 403 on second `sin` write | immutability |
| `backend/routes/admin/drivers.py` | `POST …/update-sin` (validate, audit-first, encrypt, forced Stripe re-push) | audited correction path |
| `backend/routes/admin/tax_id_import.py` | new: validate/commit CSV importer | migrate existing drivers |
| `backend/routes/admin/__init__.py` | mount importer behind `require_super_admin` | access control |
| `driver-app/app/driver/payout.tsx` | Stripe step lock; read-only on-file SIN copy | mirror backend gates |
| `backend/tests/…` | 27 new tests across 4 files; 3 fixtures updated | pin the gates |

## Before/after

Onboarding, driver with no SIN on file:

```python
# BEFORE — link minted, Stripe's form asks for the SIN, Spinr keeps no copy
POST /drivers/stripe-onboard        -> 200 {"url": "https://connect.stripe.com/…"}

# AFTER
POST /drivers/stripe-onboard        -> 422 "Add your SIN before connecting your
                                          payout account. …"
```

Driver rewriting their SIN:

```python
# BEFORE — silently overwritten, T4A/Stripe copies diverge from history
PUT /drivers/me {"sin": "…"}        -> 200

# AFTER (when one is already on file)
PUT /drivers/me {"sin": "…"}        -> 403 "…Contact support to request a
                                          correction — an admin will verify…"
```

## Rollback plan

All gates are code-only — **no migration, no data shape change** (columns are
from already-shipped 289). `git revert` of the four backend commits restores
the previous behaviour with no data cleanup: rows written by the importer are
ordinary valid `sin`/`gst_bn` values identical to app-collected ones, and
nothing depends on the new endpoints existing. SINs pushed to Stripe cannot
be un-pushed (write-only), but that state is identical to a driver having
typed it into Stripe's own form. No feature flag: the gate must hold for
every driver or the T4A copy guarantee is only a suggestion; reverting is a
single deploy with no live-data repair.

## Verification performed

- `pytest` on the 10 affected test files: **439 passed** (includes 27 new
  tests: onboarding gate, immutability, admin update-sin, importer).
- `ruff check` clean on every touched file.
- `npx tsc --noEmit` clean for `driver-app` (`payout.tsx` change).
- Manual trace of the legacy-driver path (`stripe_id_number_provided` set,
  no Vault copy) through both gates.

## What was NOT verified

- **No real production build was run for the driver app** (`tsc --noEmit`
  only — no `expo export`/EAS build; the change is JS-level conditional
  rendering in one screen).
- Not tested against live Supabase or live Stripe — all API behaviour is
  pinned via mocks; the importer's background Stripe push in particular has
  not been exercised against a real Connect account.
- No visual regression tooling exists in this repo, so the locked-step and
  read-only-SIN UI states were reasoned about, not screenshotted.
- Admin dashboard has no UI yet for update-sin or the tax-ID importer —
  both are API-only until a screen is built (operator can use them via
  curl/Postman with a super_admin token).
