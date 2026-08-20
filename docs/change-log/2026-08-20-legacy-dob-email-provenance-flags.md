# Change Impact & Risk Log — Legacy DOB provenance flag + email decision

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (backend agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | branch `claude/spinr-mongodb-migration-u9y6iz` — see final commit SHA in this session's report |
| Related issue or gap ID | `ACTION_ITEMS.md` A41, Oct 30 checklist item #7 (`docs/runbooks/legacy-migration-playbook.md`, "Accuracy-disclosure pass") |

This entry closes the remaining half of checklist item #7. SIN was already
done (`sin_source()`, `docs/change-log/2026-08-19-legacy-migration-transparency-backend.md`).
This session adds the DOB equivalent and records an explicit, documented
decision for email — the item's own wording allows "no, and here's why" as a
valid outcome, and that is the call made here for email, with the reasoning
below.

## 1. Issue / gap identified

DOB had no provenance/verified-flag treatment at all: a date-of-birth
written by the legacy Saskatoon CSV import or the `banks.csv` backfill was
indistinguishable, in every admin read path, from one a human entered or
corrected directly. Email had the same gap, narrower in scope (see decision
below).

## 2. Root cause

Neither field had a derived provenance function analogous to `sin_source()`.
For DOB specifically, the gap was never filled in the earlier
2026-08-19 pass because that pass built `sin_source()` end-to-end but did not
extend the same treatment to the other three fields item #7 names (SIN, DOB,
name, email) — name got a coarser whole-profile "Imported" badge instead,
and DOB/email got nothing.

## 3. Fix / remediation

**DOB — built, additive, no migration:**

- Added `driver_import_service.dob_source(driver) -> "legacy_import" |
  "self_entry" | None`, same contract shape as `sin_source()`, reading only
  existing columns (`date_of_birth`, `legacy_import_metadata`) — no new
  column, no migration.
- Wired into `admin_get_driver_live_stats` (the same admin driver-detail
  read path `sin_source()` already surfaces in) as two new response keys:
  `dob_on_file` (bool) and `dob_source` (the three-value label). The raw DOB
  value is never added to this or any other response — see §6 (PIPEDA).
- **Deliberately NOT wired into the T4A filer-handoff export.** Checked
  `_t4a_filer_handoff_rows` (`backend/routes/admin/compliance.py`) first:
  its `drivers` column projection is `id,name,first_name,last_name,
  stripe_account_id,sin,sin_collected_at,legacy_import_metadata` and its
  output row has no date-of-birth field at all — DOB is not part of that
  export's purpose (CRA T4A filing needs legal name/address/earnings/SIN,
  not DOB), so there is nothing there to attach a provenance flag to. The
  task instructions were explicit that this should only happen if DOB
  actually appears in that export; it doesn't.
- `dob_source()` is **not** a literal copy of `sin_source()`'s single-marker
  check. DOB has a second legacy-import write path SIN never had: the
  original Saskatoon CSV import (`build_plan()`) writes `date_of_birth`
  directly at driver creation, while it never writes `sin` at all — SIN is
  only ever self-entered or backfilled from `banks.csv`. A driver whose DOB
  was set at that original import and never later touched by the
  `banks.csv` backfill carries no `dob_written` marker at all (the backfill
  skips a column already on file), so checking only that one marker — the
  literal mirror of `sin_source()` — would have mislabeled the more common
  case as `"self_entry"`, i.e. it would have *under*-disclosed, claiming
  driver-verified provenance for raw unverified legacy CSV data. Fixed by
  also checking `legacy_import_metadata.source == IMPORT_SOURCE`. Full
  reasoning is in the function's own docstring.
- **Post-review fix (`spinr-security-auditor`, 2026-08-20, BLOCKER — same
  day, before merge):** the `source == IMPORT_SOURCE` check above is
  record-level, not field-level — it's stamped on *every* driver from the
  CSV import regardless of whether that specific row's DOB column was
  actually populated (`build_plan()` never rejects a row for a blank DOB).
  A driver whose CSV row had no DOB, was never matched by the `banks.csv`
  phone-crosswalk backfill, and later had DOB entered by a `super_admin`
  via `admin_update_driver` would have `source == IMPORT_SOURCE` true
  forever, despite the value being admin-entered — the opposite failure
  mode from the one the check was added to fix: *over*-disclosure, labeling
  admin-verified data as raw unverified legacy CSV data. **Fixed** by
  stamping a new, unconditional per-row `legacy_import_metadata.
  dob_present_at_import: bool` marker in `build_plan()` (true or false,
  always present, never inferred) recording whether *this row's* CSV DOB
  column actually had a value at import time, and switching `dob_source()`'s
  fallback check from `source == IMPORT_SOURCE` to `dob_present_at_import
  is True`. 3 new regression tests (2 unit, 1 endpoint-level) cover the
  fixed scenario directly.

**Email — investigated, decided NOT to add a dedicated flag:**

Investigated whether "self-entered vs legacy-imported" is even a meaningful,
stable distinction for email, the way it is for SIN/DOB. Read the importer
code (`build_plan()` writes `email` on the `users` row at import time) and
the self-signup flow (`routes/users.py`'s `create_profile`, `POST /profile`
— the primary profile-completion step every phone-first signup goes
through). `create_profile` unconditionally overwrites `users.email` with
whatever the person types, with **no guard protecting a legacy-imported
value** — the moment a legacy-imported rider or driver (matched by phone,
same phone the CSV imported) completes their profile, their email is fully
self-entered and the import-sourced value is gone, no different from an
organic user editing their profile at any later date.

Decision: **no dedicated `email_source` flag.** Reasons:

1. Email is not a set-once, verification-locked field the way SIN is
   (`PUT /drivers/me` rejects a second write) or the way DOB effectively is
   today (no driver-facing route writes it at all, so an unverified legacy
   value can sit unverified indefinitely). Email is freely editable at any
   time via `POST /profile`, so any accuracy problem self-corrects the next
   time the person touches their profile — there is no long-lived "stale
   unverified value the user never gets a chance to fix" risk the way there
   is for SIN/DOB.
2. There is no existing timestamp/marker analogous to `sin_collected_at` to
   derive a provenance label from. Building one would require either a new
   column (a migration — out of scope per the task's explicit "do not touch
   migrations" instruction, and not a pure-additive, zero-migration change
   the way `sin_source()`/`dob_source()` are) or overloading
   `legacy_import_metadata` with a new marker that `create_profile` would
   also need to clear on every email edit to avoid becoming actively
   misleading (unlike the DOB/SIN markers, which are allowed to stay
   permanent provenance labels precisely because those fields aren't
   silently overwritten on ordinary profile use).
3. The existing whole-profile "Imported" badge (driver/rider list and
   detail) already discloses "this profile may carry unverified
   legacy-sourced data, including its email" at the right granularity for a
   field that is this easy for the profile owner to correct through
   ordinary use of the app.

This is a "no, and here's why" outcome, which the checklist item's own
wording explicitly allows as a valid resolution — not a shortfall.

## 4. Risk & impact on existing functionality

**`dob_source()`** — brand-new function, no prior callers; isolated by
construction. Blast-radius grep performed:

- `sin_source()`'s existing consumers (the template for where a sibling
  field might also need wiring) are exactly two: `admin_get_driver_live_stats`
  (`backend/routes/admin/drivers.py`) and `_t4a_filer_handoff_rows`
  (`backend/routes/admin/compliance.py`). Grepped for any other caller of
  either function across `backend/`, `admin-dashboard/`, `driver-app/`,
  `rider-app/` — none exist. DOB is wired into the first (added
  `dob_source`/`dob_on_file` keys) and deliberately not the second (DOB
  isn't a field that export reads — see §3).
- Grepped `admin-dashboard/`, `driver-app/`, `rider-app/` for any existing
  reader of `sin_source` — none (confirmed: the 2026-08-19 backend pass
  shipped the field with the frontend render still a separately-tracked
  gap). Same applies to the new `dob_source`/`dob_on_file` keys — additive
  response fields with zero existing frontend consumers to break.
- `legacy_import_metadata`'s `LEGACY_BANK_SIN_DOB_SOURCE` marker shape is
  unchanged — `dob_source()` only *reads* the existing `dob_written` key
  that `apply_legacy_sin_dob_import` already writes (added in the
  2026-08-19 pass); no write-path change in this session.
- No touch to SIN's own code path, tests, the vehicle-history backfill, the
  cancelled/failed booking import, or any migration — confirmed by `git
  diff --stat` before committing (see Files modified below; nothing outside
  the four DOB/tests files and the three documentation files).
- No interaction with the ride state machine, wallet/allowance deltas, or
  any of the 18 background loops in `backend/core/lifespan.py`.

**Email decision** — no code change, so no runtime blast radius. The
investigation itself (reading `create_profile`) confirmed no other route
depends on `users.email` remaining the original import value, so choosing
not to add a flag does not leave a silent inconsistency anywhere.

## 5. User-experience effect

Internal admin only — no rider, driver, or corporate-admin-facing change.
The admin driver-detail slideout gains two new response fields
(`dob_on_file`, `dob_source`) that the admin-dashboard frontend does not yet
render (same known, separately-tracked gap `sin_source` already has — not
addressed in this backend-only session). Not visible mid-session to anyone
using the rider/driver apps. No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | Added `dob_source()` | DOB provenance derivation, mirroring `sin_source()`'s contract |
| `backend/routes/admin/drivers.py` | Imported `dob_source`; added `dob_on_file`/`dob_source` keys to `admin_get_driver_live_stats`'s response | Admin driver-detail read path — same surface `sin_source` already uses |
| `backend/tests/test_legacy_sin_dob_import_service.py` | Added 7 unit tests for `dob_source()` (none/no-DOB, CSV-import-source case, banks.csv-marker case, self-entry case, the SIN-only-backfill-stays-legacy_import regression, non-dict-metadata defensive case) | Test coverage mirroring `sin_source()`'s existing suite |
| `backend/tests/test_admin_drivers_coverage.py` | Added 4 HTTP-level tests for `dob_source`/`dob_on_file` in the live-stats response, including a raw-DOB-never-in-response assertion | Test coverage mirroring `sin_source()`'s existing HTTP tests |
| `docs/runbooks/legacy-migration-playbook.md` | Appended a dated re-verification block to item #7 recording DOB as done and the email decision | Checklist status update, per the file's own established annotation style |
| `ACTION_ITEMS.md` | Updated A41's "Remaining Oct 30 checklist items" count/reasoning to include item #7 as fully addressed | Keep the backlog entry in sync with the checklist |
| `docs/change-log/2026-08-20-legacy-dob-email-provenance-flags.md` | This file | Mandatory Change Impact Log for a live-tested-adjacent (admin/drivers) surface |
| `backend/services/driver_import_service.py` (post-review) | Added `dob_present_at_import` stamp in `build_plan()`; switched `dob_source()`'s fallback check to it | Fix `spinr-security-auditor` BLOCKER (over-disclosure for admin-entered DOB on a blank-at-import legacy driver) |
| `backend/tests/test_legacy_sin_dob_import_service.py` (post-review) | Updated 2 existing fixtures + 2 new regression tests | Cover the fixed scenario and the missing-marker default |
| `backend/tests/test_admin_drivers_coverage.py` (post-review) | Updated 1 existing fixture + 1 new endpoint-level regression test | Same coverage at the HTTP-response layer |

No changes to `backend/services/driver_import_service.py`'s SIN code path,
`sin_source()`, its tests, the vehicle-history backfill, the
cancelled/failed booking import, or any migration.

## 7. Before / after

Additive-only diff — no existing field's meaning or written value changed.
`admin_get_driver_live_stats`'s response gains two new keys, alongside the
existing SIN pair:

```python
# Before
return {
    ...
    "sin_last4": (drv or {}).get("sin_last4"),
    "sin_on_file": bool(drv and drv.get("sin")),
    "sin_collected_at": (drv or {}).get("sin_collected_at"),
    "sin_source": sin_source(drv),
}

# After
return {
    ...
    "sin_last4": (drv or {}).get("sin_last4"),
    "sin_on_file": bool(drv and drv.get("sin")),
    "sin_collected_at": (drv or {}).get("sin_collected_at"),
    "sin_source": sin_source(drv),
    "dob_on_file": bool(drv and drv.get("date_of_birth")),
    "dob_source": dob_source(drv),
}
```

## 8. Rollback plan

Pure-additive code change (new function, two new response keys on one
existing admin endpoint), no migration, no mutation of existing data, no
frontend change to roll back. Rollback is a plain `git revert` of the
relevant commit(s) — no feature flag needed because nothing user-visible
(rider/driver/corporate) is touched and no already-applied data needs
correcting.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_legacy_sin_dob_import_service.py backend/tests/test_admin_drivers_coverage.py backend/tests/test_compliance_reports_http.py -q --no-cov` — 214 passed (28 + 186; includes the untouched T4A test file, confirming no regression there from the deliberate decision not to wire DOB into that export).
- [x] Blast-radius grep performed (listed in full in §4): every caller of `sin_source`/`dob_source`, every frontend reader of either field, every reader of the `LEGACY_BANK_SIN_DOB_SOURCE` marker shape.
- [x] Reviewed against relevant CLAUDE.md conventions: additive-over-destructive (no migration, no column repurposing, no write-path change), PIPEDA (no raw DOB value added to any response, log, or Sentry payload — `dob_source` is a three-value enum; `dob_on_file` is a boolean), do-not-silently-swallow-errors (no error handling touched).
- [x] `ruff check` run on the four touched Python files — clean except 4 pre-existing B904 findings in `backend/routes/admin/drivers.py` far from any line touched in this session (confirmed pre-existing via a diff against `origin/main` before any of this session's edits — same 4 findings present either way).
- [ ] Feature-flagged: not applicable — internal-admin-only, additive, no live-tested rider/driver/corporate flow touched.

## 10. What was NOT verified

- **No live Supabase access this session** — all verification is against `mock_supabase_client`/local fakes and the module's own local fake-supabase harness, per this repo's existing test-tier convention. Nothing was checked against a real database or staging environment.
- **No `npm run build`** — this session touched backend Python and documentation only; no `admin-dashboard`/`rider-app`/`driver-app` file was changed, and the admin-dashboard frontend does not yet render either the existing `sin_source` field or the new `dob_source`/`dob_on_file` fields (same known, separately-tracked frontend gap noted in the earlier `sin_source` change log).
- **The email decision is a documented judgment call, not something independently verified against production data** — it rests on reading `create_profile`'s code path, not on observing real legacy-imported users' email-edit behavior in production (no access to that data in this session).
- **No visual/snapshot regression tooling exists in this repo** for the admin-dashboard frontend that would eventually render these new fields — not applicable to this session's backend-only diff, but noted per CLAUDE.md's standing-gap convention.
