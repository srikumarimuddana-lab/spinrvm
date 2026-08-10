# Change Impact & Risk — collect and encrypt the driver SIN

**Date:** 2026-08-10
**Branch:** `claude/stripe-card-sync-issue-gepad1`
**Surfaces touched:** driver app (payouts screen), backend driver profile, admin driver detail, `drivers` schema

## Issue/gap identified

Spinr could not file a T4A slip for any driver. It holds no SIN, and the number
it believed it could fetch from Stripe at filing time cannot be fetched.

## Root cause

The design assumed Stripe Connect held the SIN and
`POST /admin/drivers/{id}/reveal-sin` could read it back. `individual.id_number`
is **write-only** on Connect: verified against the installed SDK (generated
from Stripe's API spec), it appears in six request-param modules and in **zero**
response models. Stripe returns `id_number_provided` and `ssn_last_4_provided`
— booleans, never digits. Stripe's tax-form product files US 1099s and does not
file Canadian T4A, so there is no delegation path either.

The gap went unnoticed because the reveal endpoint's failure was reported as a
retryable 502 (fixed in the previous change-log entry, addenda 8–9), so it read
as a flaky call rather than an impossible one.

## Fix/remediation

Collect it ourselves, at the same place and in the same shape as the GST/BN
number, and encrypt it with the mechanism already protecting `license_number`.

- **`sin`** — Supabase Vault token (pgsodium, `drivers_pii_key`), written
  through the existing `encrypt_driver_pii` RPC. Never the number.
- **`sin_last4`** — plaintext, the only part ever displayed.
- **`sin_collected_at`** — drives pre-deadline "who is still missing one"
  reporting.

Validation (`backend/utils/sin.py`) is 9 digits + Luhn + a leading-zero and
repeated-digit guard. Luhn is the point: it catches every single-digit typo and
almost every adjacent transposition, and a typo is otherwise not discovered
until CRA rejects the slip months later.

**Collection without disclosure.** A new `_VAULT_WRITE_ONLY_PII_FIELDS` set is
encrypted on write and deliberately excluded from `_decrypt_driver_pii`. No
application code decrypts `drivers.sin` — the instruction was to collect now and
decide on reveal later, and this makes that structural rather than a convention.
`license_number` keeps round-tripping (a driver may re-read their own licence);
a SIN has no reason to travel back on every profile poll.

## Risk & impact on existing functionality

**Blast radius, checked before writing:**

| Shared thing changed | Every other consumer | Effect |
|---|---|---|
| `_encrypt_driver_pii` | `routes/drivers/profile.py` (×3), `routes/admin/drivers.py:1437` | Set widened, not replaced. `license_number` still encrypted — pinned by test. |
| `_decrypt_driver_pii` | `profile.py` GET+PUT | Unchanged behaviour: still decrypts `_VAULT_PII_FIELDS` only. |
| `_STRIP_FROM_SELF_RESPONSE` | `profile.py`, `tax_exports.py` | One entry added. |
| `UpdateDriverProfileRequest` / `safe_fields` | `PUT /drivers/me` only | `sin` is a *safe* field like `gst_bn` — must not flip a verified driver to `needs_review` and knock them offline mid-shift. Pinned by test. |
| `drivers` table | dispatch, admin, T4A, exports | Three nullable additive columns. Nothing reads them until this deploy. |

**Two pre-existing defects found and fixed en route** (both would have leaked a
SIN, and both already affected `license_number`):

1. `PUT /drivers/me`'s auto-create branch inserted `**updates` **without
   encrypting** — a driver whose first-ever profile write carried a
   `license_number` stored it as plaintext. The update branch has always
   encrypted; only auto-create did not.
2. `PUT /drivers/me` returned the raw driver row **without applying
   `_STRIP_FROM_SELF_RESPONSE`**, which `GET /drivers/me` does apply — so
   `stripe_account_id`, `bank_account` and `fcm_token` came back on every
   profile update. Both verbs now return the same shape.

**Not feature-flagged.** The surface is additive — a new optional field and a
new collapsed form on an existing screen. Nothing existing changes behaviour,
so there is no dark-launch state to verify; the rollback below is a column drop.

## User experience effect

**Driver-facing, visible mid-session** to anyone on the payouts screen:

- The setup checklist's SIN step changes from *"Verify your SIN — added
  securely through Stripe, we never see or store it"* (delegating to Stripe
  onboarding) to *"Add your SIN for tax slips"*, opening a Spinr form.
- **That copy had to change: it is no longer true.** Continuing to tell a driver
  we never store their SIN while storing it would be a false privacy
  representation. The new copy states it is encrypted, used only for the T4A,
  and that only the last 4 are ever displayed.
- The step is no longer gated on Stripe onboarding — it is our own record, and a
  driver can supply it in either order.
- Input is `secureTextEntry`, cleared from component state on save, cancel and
  error. On-file state renders as `On file · ••••1234`.

**Admin-facing:** driver stats gain `sin_on_file`, `sin_last4`,
`sin_collected_at`. **Admins never see the full number** — there is no decrypt
on this path, and `sin` is absent from the admin-editable field allowlist so it
cannot be set through the generic edit endpoint (which would bypass validation
and last-4 derivation).

**Rider/corporate:** none.

## Files modified

| file path | what changed | why |
|---|---|---|
| `backend/migrations/289_driver_sin_encrypted.sql` | New: `sin`, `sin_last4`, `sin_collected_at` + `sin_last4` format CHECK | Somewhere to put it |
| `backend/utils/sin.py` | New: normalize/validate/last4 | A bad number is not caught until CRA rejects the slip |
| `backend/routes/drivers/_shared.py` | `_VAULT_WRITE_ONLY_PII_FIELDS`; encrypt covers both sets; decrypt covers only the round-trip set; `sin` added to the strip list | Collect without disclosing |
| `backend/routes/drivers/profile.py` | `sin` on the request model + `safe_fields`; validate → 422; derive last4 + timestamp; **encrypt on auto-create**; **strip on the PUT response** | Collection point owns the guarantees |
| `backend/routes/admin/drivers.py` | `sin_on_file` / `sin_last4` / `sin_collected_at` on driver stats | T4A readiness without a decrypt |
| `driver-app/app/driver/payout.tsx` | Own SIN form; corrected copy; `sinOnFile` from our record; Stripe flag renamed `stripeIdOnFile` | Where GST is already collected |
| `backend/tests/test_driver_sin_collection.py` | New: 29 tests | Below |

## Before/after

```python
# before — auto-create wrote the payload unencrypted
await db_supabase.insert_one("drivers", new_driver)
return serialize_doc(new_driver)

# after
await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))
return serialize_doc({k: v for k, v in new_driver.items() if k not in _STRIP_FROM_SELF_RESPONSE})
```

```tsx
// before — untrue once we store it
subtitle: sinOnFile ? 'Identity verified'
                    : 'Added securely through Stripe — we never see or store it',

// after
subtitle: sinOnFile ? `On file · ••••${driverMe?.sin_last4}`
                    : 'Encrypted and used only for your year-end T4A slip',
```

## Rollback plan

No feature flag; rollback is a revert plus a column drop, and **the drop is not
sufficient on its own**:

```sql
-- Erase collected SINs FIRST — dropping the column orphans the ciphertext
-- in vault.secrets rather than deleting it, which is a PIPEDA problem, not
-- a clean rollback.
DELETE FROM vault.secrets WHERE id IN (
  SELECT sin::uuid FROM public.drivers WHERE sin IS NOT NULL
);
ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin;
ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin_last4;
ALTER TABLE public.drivers DROP COLUMN IF EXISTS sin_collected_at;
```

Reverting the app code alone is safe and immediate: the form disappears, the
columns go unread, and no other flow depends on them. The two pre-existing
fixes (auto-create encryption, PUT response strip) should be kept even if the
SIN work is reverted — they stand on their own.

## Verification performed

- `pytest -k "driver or admin_drivers or payout or t4a or compliance or pii or
  vault"` — **2109 passed, 1 skipped**.
- `backend/tests/test_driver_sin_collection.py` — **29 passed**, covering: Luhn
  rejects single-digit typos and adjacent transpositions; `000000000` rejected
  despite passing Luhn; leading zero rejected; **leading 9 accepted** (temporary
  residents are lawful workers and must not be locked out of being paid);
  formatting stripped; **no error message contains the digits**; `sin` is in the
  write-only set and not the round-trip set; encrypt covers both sets; decrypt
  leaves `sin` alone; PUT stores a token + last4 + timestamp; invalid input is
  422 with **nothing written**; supplying a SIN does not set `status` or
  `is_online`; the auto-create path encrypts; **no response contains the
  number**.
- Test SINs are **constructed**, not hardcoded, and the check digit is computed
  with an independent table-driven Luhn — so the repo contains no plausible real
  SIN, and a bug in the validator cannot mask itself.
- `ruff check` + `ruff format --check` clean.
- **Driver app: real production build run** — `npx tsc --noEmit` (exit 0) and
  `npm run build:web` (`expo export --platform web`), which bundled and exported
  successfully. This is the production bundler for this surface; there is no
  `npm run build` in `driver-app/package.json`.

## What was NOT verified

- **Migration 289 has not been applied.** Like 286–288, it is written but not
  run; the backend will 500 on a SIN write until it is.
- **The Vault RPC path was not exercised end to end.** `_vault_encrypt` is
  mocked in every test — the `encrypt_driver_pii` RPC itself is unchanged and
  already in production use for `license_number`, but no real encrypt/decrypt
  round-trip was performed for this column.
- **No visual/snapshot regression tooling exists for `driver-app`**, so the new
  form's rendering and the changed checklist copy were reasoned about and
  type-checked, not screenshotted. This is the standing gap in `ACTION_ITEMS.md`,
  not a new one.
- **The `stripe_id_number_last4` column is still dead** — it was never populated
  (Stripe has no such field) and is untouched here. `sin_last4` supersedes it in
  practice; removing it is separate work.
- **T4A generation does not read the new column yet.** This change collects the
  SIN; wiring it into `utils/t4a_pdf.py` / `routes/drivers/tax_exports.py` is
  the next piece and is not done.
- **No legal review of the collection notice.** The in-app copy states purpose
  and handling, which PIPEDA requires, but the wording has not been reviewed by
  anyone qualified. Worth doing before the T4A run.
- **Existing drivers are not prompted.** The field is optional and nothing
  chases the ~111 imported drivers for it; a reminder campaign ahead of the
  deadline is not built.
