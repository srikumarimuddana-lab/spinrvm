# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend, driver-app, shared |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see commit — filled after push) |
| Related issue or gap ID | ACTION_ITEMS.md B14 — pivot from manual admin backfill to driver self-serve, decided by the product owner 2026-08-31 |

## 1. Issue / gap identified

22 of 209 drivers have `NULL` `license_number`/`license_class`. The
previously-built remediation (`/dashboard/driver-license-backfill`)
required an admin to manually read each driver's uploaded ID photo and
retype the data. The product owner parked that manual-entry approach
today in favour of letting each affected driver enter their own licence
number/class directly in the driver app, prompted by a one-time push
notification.

## 2. Root cause

`license_number`/`license_class` are optional self-serve profile fields,
never required at signup or at document-review approval (unchanged by
this PR — see the 2026-08-18 change log for the now-closed "approval gap
can grow" half of B14). Nothing ever prompted the 22 already-onboarded
drivers to go back and fill them in themselves.

## 3. Fix / remediation

- **Backend, admin-triggered campaign:** `POST
  /admin/drivers/notify-missing-license`
  (`backend/routes/admin/drivers.py::admin_notify_missing_license`) — a
  one-time, admin-clicked action (not a background loop; CLAUDE.md
  "simplicity first" — this remediates a known, bounded ~22-driver
  cohort, not ongoing behaviour) that re-uses the exact same
  `missing_license` filter the existing backfill queue's `GET
  /admin/drivers?missing_license=true` already used (both now read the
  new shared `_MISSING_LICENSE_FILTER` constant, so the two screens can
  never drift onto different cohorts) and pushes each affected driver a
  reminder via the existing `send_push_notification` utility — the same
  one `nudge-expiry` already uses for a similar one-off targeted nudge.
  Gated by the router-level `require_module("drivers")` dependency
  (`backend/routes/admin/__init__.py`), same RBAC as every other endpoint
  in this file, including the existing backfill queue's `GET
  /admin/drivers`.
- **Backend, self-serve write path:** `license_class` added to the
  driver's own `PUT /drivers/me`
  (`backend/routes/drivers/profile.py::update_my_driver`) —
  `license_number` was already self-serve there; `license_class` was not.
  No new endpoint or write path was introduced — both fields now flow
  through the exact same `_encrypt_driver_pii()` call the rest of that
  endpoint already used. `license_class` is not itself Vault-encrypted
  PII (`_VAULT_PII_FIELDS` covers only `license_number`), matching how
  the admin edit endpoint's own `license_class` field is already handled.
- **driver-app UI:** a banner on the existing Profile screen
  (`app/driver/(tabs)/profile.tsx`), shown only when
  `license_number`/`license_class` is missing, opening a small modal
  (reusing the existing Edit-Profile modal's own styles) with two fields
  validated by a new `licenseInfoFormSchema.ts` (zod + exported predicate
  functions, matching the B39 convention in `vehicleInfoFormSchema.ts`).
  Save calls the same `PUT /drivers/me` above.
- **Push deep link:** `app/_layout.tsx`'s push-notification router (both
  the foreground/backgrounded listener and the killed-state listener)
  routes a `{"type": "license_backfill_prompt"}` push to `/driver/profile`
  — the banner there opens the modal.
- **Shared type:** `shared/store/authStore.ts`'s `Driver` interface gained
  optional `license_number`/`license_class` fields (previously only
  reachable via its `[key: string]: unknown` index signature).

The existing `/dashboard/driver-license-backfill` admin tool and
`admin_review_driver_document`'s license-gate (422 on approving a licence
document without both fields on file) are **unchanged** — both remain live
as independent safety nets, per instruction not to touch them.

## 4. Risk & impact on existing functionality

- **`_MISSING_LICENSE_FILTER` refactor** — the literal `$or` filter dict
  used by `GET /admin/drivers?missing_license=true` was extracted to a
  module constant and reused by the new endpoint. Grepped
  `backend/routes/admin/drivers.py` for every other reference to
  `license_number`/`license_class` filtering: the only other call site was
  the one being refactored. Blast radius: isolated to this file.
- **`license_class` added to `PUT /drivers/me`'s `vehicle_fields` set** —
  grepped `backend/routes/drivers/profile.py` and
  `backend/routes/drivers/_shared.py` for every consumer of
  `vehicle_fields`/`changed_vehicle`. The only effect is that a driver
  submitting `license_class` now hits the exact same `changed_vehicle`
  branch `license_number` already triggers: an **active** driver who edits
  it is flipped to `needs_review`, taken offline
  (`is_online`/`is_available` → `False`), and sent the existing
  `needs_review` push/email via `notify_driver_status_change`. This is
  **not new behaviour** — `license_number` already did this; adding
  `license_class` to the same set makes it consistent with its sibling
  field, not a new consequence. Also picked up automatically: the existing
  `record_vehicle_changes` audit trail and the M-5 insurance-period 1→0
  transition on `changed_vehicle` for an online active driver — both
  pre-existing, unmodified logic paths that now also fire for
  `license_class` edits.
- **Push campaign idempotency** — re-running `POST
  /admin/drivers/notify-missing-license` is safe to repeat: the filter
  naturally drops any driver who has since filled in their own data, so
  only drivers still missing it get re-nudged. A driver who has *not* yet
  completed the form and gets nudged twice by an admin re-clicking is a
  UX nit (a duplicate reminder), not a correctness issue — no new DB state
  is required to prevent it, matching "simplicity first" for a one-time
  admin-operated campaign.
- **Shared `Driver` interface change** — additive optional fields only;
  grepped for other consumers of the `Driver` type across `driver-app/`
  and `rider-app/`(driver profile card usages) — no code destructures
  `license_number`/`license_class` today besides the new modal, so this
  cannot break an existing read.
- **No migration.** No new column, no `app_settings` flag. The new push
  `data.type` (`license_backfill_prompt`) is a value the client didn't
  previously recognise; the existing suppression list
  (`app/_layout.tsx`'s native-banner logic) doesn't include it, so the
  OS-level banner shows normally (same as every other non-suppressed type,
  e.g. the `document_expiry_nudge` push it mirrors).

## 5. User-experience effect

- **Driver-facing.** Only the ~22 affected drivers see the new Profile-tab
  banner and receive the one-time push (once an admin triggers the
  campaign — not automatic, not yet sent by this change). A driver who
  already has both fields never sees the banner. If an already-**active**
  driver fills in the form, they are immediately taken offline pending
  re-review — this mirrors the existing `license_number` self-serve
  behaviour exactly; it is surfaced to the driver via the same
  `needs_review` push copy ("We're reviewing your updated details...")
  that already exists for any vehicle/document edit. Not mid-session
  silent — the driver is told why they went offline.
- **Admin-facing.** A new one-click campaign button/endpoint exists (no
  admin-dashboard UI was built in this change — see "What was NOT
  verified" below); the existing backfill queue UI is untouched.
- **Rider-facing.** None.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | Extracted `_MISSING_LICENSE_FILTER`; added `POST /drivers/notify-missing-license` | Reuse the exact backfill-queue cohort filter; one-time admin-triggered push campaign |
| `backend/routes/drivers/profile.py` | Added `license_class` to `UpdateDriverProfileRequest` and `vehicle_fields` in `PUT /drivers/me` | Self-serve write path for the missing field, reusing the existing encrypting update |
| `backend/tests/test_admin_extended.py` | 3 new tests for `admin_notify_missing_license` | Cohort filter reuse, push success, push-failure handling |
| `backend/tests/test_drivers_extended.py` | 1 new test for `license_class` self-serve write | Confirms same write/re-review path as `license_number` |
| `driver-app/app/driver/(tabs)/profile.tsx` | Missing-licence banner + entry modal | Driver-facing self-serve UI |
| `driver-app/app/_layout.tsx` | Route `license_backfill_prompt` push taps to `/driver/profile` | Deep link from the campaign push |
| `driver-app/utils/licenseInfoFormSchema.ts` (new) | zod schema + predicates for the new form | B39 validation convention |
| `driver-app/utils/__tests__/licenseInfoFormSchema.test.ts` (new) | Jest tests for the schema | Coverage for new validation logic |
| `shared/store/authStore.ts` | Added optional `license_number`/`license_class` to `Driver` | Typed access from the new UI |
| `ACTION_ITEMS.md` | B14 entry updated | Records the pivot and what was built |

## 7. Before / after

```python
# Before — backend/routes/drivers/profile.py, PUT /drivers/me vehicle_fields
vehicle_fields = {
    ...
    "license_plate",
    "vehicle_vin",
    "license_number",
    "license_expiry_date",
    ...
}
```

```python
# After
vehicle_fields = {
    ...
    "license_plate",
    "vehicle_vin",
    "license_number",
    "license_class",
    "license_expiry_date",
    ...
}
```

```python
# Before — backend/routes/admin/drivers.py, GET /drivers missing_license
filters["$or"] = [{"license_number": None}, {"license_class": None}]
```

```python
# After — same result, now the single source of truth for the cohort
filters["$or"] = _MISSING_LICENSE_FILTER["$or"]
```

## 8. Rollback plan

- No migration, no `app_settings` flag needed — this is additive
  (a new endpoint, a new allowed field on an existing endpoint, new
  client-only UI gated behind a data condition that is false for 187 of
  209 drivers today).
- To roll back: revert the commit. The admin campaign endpoint simply
  stops existing (no state it wrote needs cleanup — pushes are
  fire-and-forget, no new DB rows). `license_class` becomes non-settable
  via `PUT /drivers/me` again; any driver who already saved it keeps the
  value on their row (harmless — it's the intended data). No Stripe
  charges, wallet deltas, or ride state are touched, so a plain `git
  revert` is sufficient here (unlike the exceptions CLAUDE.md calls out).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_extended.py
  backend/tests/test_drivers_extended.py backend/tests/test_admin_business_logic.py
  --no-cov` — 213/214 passed; the 1 failure
  (`TestBatchFetchDriversAndUsers::test_fetches_by_ids`) reproduces
  identically on a clean `git stash` of this change, confirming it is
  pre-existing and unrelated.
- [x] `ruff check backend/routes/admin/drivers.py backend/routes/drivers/profile.py
  backend/tests/test_admin_extended.py` — 4 pre-existing `B904` findings, all
  in unrelated Stripe-refresh code (lines 3345-3595) far from this diff;
  none in the touched regions.
- [x] driver-app: `yarn jest utils/__tests__/licenseInfoFormSchema.test.ts`
  — 11/11 passed.
- [x] driver-app: `npx tsc --noEmit` — clean, no errors, across the whole
  project (confirms the new modal/banner code and the `Driver` interface
  change type-check against every existing consumer).
- [x] Blast-radius grep performed: `_MISSING_LICENSE_FILTER` (only
  refactor site), `vehicle_fields`/`changed_vehicle` in
  `routes/drivers/profile.py` + `_shared.py`, `Driver` interface consumers
  in `driver-app/` and `rider-app/`, push `data.type` suppression list in
  `app/_layout.tsx`.
- [x] Reviewed against CLAUDE.md: Vault-PII convention (`license_class`
  correctly excluded from `_VAULT_PII_FIELDS`), dual-import pattern
  (unchanged in touched files), RBAC module gating
  (`require_module("drivers")`, unchanged — the new endpoint lives in the
  already-gated router), "do not silently swallow errors" (push failures
  in the campaign are caught, logged with `exc_info=True`, and counted in
  the response rather than dropped or aborting the rest of the cohort).
- [ ] Feature-flagged: **not flagged.** Reasoning: the new endpoint is
  admin-only and inert until an admin explicitly clicks it; the new
  driver-app banner/modal only ever appears for drivers with a `NULL`
  field today (187 of 209 never see it), and for those it enables filling
  in previously-unfillable data rather than changing an existing flow's
  behaviour. The one real behavioural change — `license_class` now also
  flips an active driver to `needs_review` — is not new (that already
  happens for `license_number`); this only extends it to a sibling field
  a driver could not previously edit at all. Judged low-risk enough that
  the added complexity of a flag wasn't justified; flagging is the
  fallback per CLAUDE.md gate #3, not a bar every change must clear.

## 10. What was NOT verified

- **No real push was sent.** Verified only against `mock_supabase_client`
  and a mocked `send_push_notification` — not exercised against real FCM/
  Expo delivery or a real device.
- **No admin-dashboard UI button was built** for the new campaign
  endpoint; it is callable but there is no dashboard click-path yet — an
  admin would need to call it directly (e.g. via `curl`/Postman) until a
  UI is added. Flagging this as the natural next step, not silently
  leaving it implied.
- **No production build was run for driver-app** (`expo export` /
  equivalent) — only `tsc --noEmit` and Jest. Per CLAUDE.md's own
  standing note, driver-app has no active visual-regression tooling, so
  the new banner/modal's actual on-device appearance was reasoned about
  from existing style reuse, not screenshotted.
- **The 22 drivers have NOT completed their data.** This change only
  builds the mechanism (campaign endpoint + self-serve UI). Whether any
  driver actually fills in their licence info depends on the campaign
  being triggered and drivers responding — unconfirmed and out of scope
  for this change.
- **Not tested against a real Postgres/Supabase instance** — only mocked
  Supabase responses, consistent with this repo's unit-test tier.
