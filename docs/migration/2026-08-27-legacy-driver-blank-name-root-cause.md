# Legacy driver blank-name root cause, and a second collision finding surfaced while fixing it

**Status:** decision made and shipped (option b below). One follow-on finding (existing-match
collision rate) is new, real, and **explicitly not decided** in this doc — see §3.

**Scope:** this is the deep-dive behind one line item in
`docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 Phase 1, and directly feeds
`docs/runbooks/legacy-migration-playbook.md`'s Oct 30 checklist (new item #11 added there today).
Everything here was measured against the real 2026-08-22 Mongo export
(`drivers.csv`, 925 rows after `read_mongo_export_csv` parsing) and, for the collision finding,
the live production Supabase project (`soavhtdhefowwvforzwb`, `ca-central-1`), read-only.

## 1. The question that was open

`backend/services/driver_import_service.py`'s new `build_mongo_driver_import_plan` (Phase 1)
originally treated a blank `name` field as a row-level **error**. `commit_mongo_driver_import_plan`
refuses to write anything while `plan.errors` is non-empty, so this didn't just skip 588 bad rows —
it blocked the entire 925-row batch's commit until every one was resolved. Two options were on the
table:

- **(a)** keep strict rejection, matching `booking_import_service.py`'s own precedent for an
  unusable field.
- **(b)** treat it as a warning, synthesize an obviously-fake placeholder name, and let the row
  import (still forced `needs_review`/unverified/offline like every other row).

**Decided 2026-08-27: option (b).** The reasoning below is why — this was root-caused against the
real export first, not assumed.

## 2. Finding 1 — blank name is not a data-quality bug, it's abandoned onboarding

Method: loaded the real `drivers.csv` (925 rows) with Python's `csv.DictReader`, split on whether
`name` was blank after stripping, and cross-tabulated every other column against that split.

| | blank name (588 rows, 63.6%) | non-blank name (337 rows, 36.4%) |
|---|---|---|
| `set_up_profile` | `false` — **588/588 (100%)** | `true` — **337/337 (100%)** |
| `is_email_verify` | `false` — 588/588 | mixed |
| `is_approved` | `''` (never reached approval) — 588/588 | `true` 177, `''` 91, `false` 69 |
| `is_phone_verify` | `true` — 588/588 | mixed |
| `status` | `offline` — 588/588 | `offline` 324, `online` 13 |
| phone present | 588/588 | 337/337 |
| email present | **0/588** | mixed |

`set_up_profile` is a perfect bijection with a blank name in this export — every row with a blank
name has `set_up_profile=false`, and every row with `set_up_profile=true` has a name. Read
together with `is_phone_verify=true` + `is_email_verify=false` + `is_approved=''`, the shape is
unambiguous: **the person verified their phone via OTP and never completed the in-app profile
step** — no name entered, no documents uploaded, no vehicle added, never reviewed for approval.
This is the old app's own onboarding funnel drop-off, captured verbatim in the export, not an
export/parsing defect.

**Ride-linkage check** (the fact that actually settles the policy question): does any of these 588
rows ever drive a real trip? Joined `drivers._id` against every `driver_id`-shaped column in
`bookings.csv` (1,301 rows: `driver_id`, `rate_by_driver`, `tip_driver`).

| | blank-name drivers (588) | named drivers (337) |
|---|---|---|
| distinct drivers referenced by `bookings.driver_id` | **0** | 102 |

Zero of the 588 abandoned-onboarding rows are ever referenced by a booking. They never drove a
trip. Importing them carries no ride-history-accuracy value — the only value is completeness (§0
of the parent plan doc: "all data from the old app should be in the new DB"), which is exactly
what option (b) delivers without the strict-rejection collateral damage of blocking the 337 rows
that *do* matter.

**Fix shipped:** blank name → warning (not error), placeholder name
`"Unnamed Legacy Driver {old_id[-6:]}"` (last 6 chars of the Mongo `_id`, so it stays traceable
back to the source row without leaking anything PII-shaped), and a new
`legacy_import_metadata.incomplete_profile_in_source` boolean derived from `set_up_profile`
(not merely from the name being blank, so a future export where the two diverge is still labeled
correctly). Every row — named or placeholder — still lands `status='needs_review'`,
`is_verified=False`, `is_online=False`, `is_available=False` unconditionally; nothing about this
change touches dispatch eligibility. See `backend/services/driver_import_service.py`'s
`build_mongo_driver_import_plan` section-header comment for the code-level version of this
reasoning, and `backend/tests/test_legacy_mongo_driver_import_service.py` for coverage
(`test_missing_name_is_warning_with_placeholder_not_error`,
`test_missing_name_with_set_up_profile_true_still_imports_but_flag_false`).

Re-run against the real export after the fix: **924/925 rows now build cleanly** (587 warnings,
all blank-name; 1 remaining error, an unrelated invalid-phone row — see §4).

## 3. Finding 2 — a second, larger blocker surfaced while validating the fix (NOT yet decided)

Fixing the blank-name error doesn't make the batch actually committable against **production**,
because `commit_mongo_driver_import_plan` also refuses on the pre-existing "matching user or
driver already exists" rule — which is a **row-level error**, not a warning, and this rule fires
far more than the smoke test (run against an empty mock DB) suggested.

Read-only query against the live Supabase project (`soavhtdhefowwvforzwb`), joining all 910 unique
normalized phone numbers from `drivers.csv` against the real `users`/`drivers` tables:

| | count | % of 910 |
|---|---|---|
| already match an existing `users` row | 324 | 35.6% |
| — of which, role is rider (`role='rider'` or `is_rider`) | 324 | 100% of the above |
| already match an existing `drivers` row (by phone) | 212 | 23.3% |
| union (rows the current code would reject as "existing match") | 324 | 35.6% |
| already imported by *this* importer (safe resume case) | 0 | — |

Production baseline for context: 213 real driver rows exist today (187 from the earlier Saskatoon
CSV import, ~24 organic signups, 2 other), 1,138 total users, 207 total rides. None of the 324
matches are a prior run of this importer (`legacy_mongo_driver_import` has never been applied) —
they're genuine collisions with **other** account-creation paths, overwhelmingly the rider-phone
matching `booking_import_service.py` already did when the ride history was imported.

**Why this matters and isn't a bug in the check itself:** the "existing match = error, never
silently merge" rule is deliberate and correct as a *safety* rule — it's the same rule
`booking_import_service.py` uses, and this section's own docstring already flagged that a large
share of rows were *expected* to hit it, since many of these phones already resolved during ride
import. What's new here is the **scale**, confirmed against real data for the first time: 35.6%,
not "some." Under the current all-or-nothing `commit_mongo_driver_import_plan`, this is a second
whole-batch blocker of the same shape as the blank-name one — except unlike blank names, simply
downgrading "existing match" from error to warning-and-skip would be a real, debatable policy
change (it decides what happens to 324 real accounts), not a data-quality call. **Not made here.**

Two sub-populations behave differently and probably deserve different treatment:

1. **Phone matches a `users` row only, no `drivers` row (324 − 212 = 112 rows):** this person has
   an account in the new Spinr DB (almost always created as a rider via `booking_import_service`'s
   phone-matching) but no driver profile yet. The old-app driver history is real, unlinked
   information about a real new-app account. Silently skipping loses it entirely.
2. **Phone matches an existing `drivers` row (212 rows):** this person already has a real Spinr
   driver account (from the Saskatoon CSV import or organic signup). The old-app driver row is
   very likely *the same person's* history under the old app, not a duplicate account to create.

## 4. Residual: one row still needs manual handling either way

Independent of both findings above, 1 row in the real export has a phone number that fails
`_PHONE_RE` (not a valid 10-digit North American number) — a genuine bad-data row, correctly still
a hard error under any policy. `commit_mongo_driver_import_plan` will keep refusing to commit while
this single row is present; the operational fix on import day is to either correct or drop that one
row from the CSV before running `--apply`, same as any other batch import. Not a design gap, just
a note for whoever runs Oct 30's batch so it isn't mistaken for a repeat of finding 1 or 2.

## 5. Riders — same method applied for completeness, no code change today

Out of caution against assuming Finding 2's shape generalizes, the same phone-collision check was
run against `customers.csv` (1,238 rows, 1,233 unique phones) — riders are explicitly out of scope
for today's code change, but Oct 30 needs the same numbers. A 250-phone random sample (not a full
join, to keep this session's footprint small) matched an existing `users` row **203/250 (81.2%)**,
extrapolating to roughly 1,000 of 1,233 old-app riders already having a phone-stub account in the
new DB (again, mostly via `booking_import_service`'s ride-time phone matching). The practical
implication for Oct 30: most riders already *exist* as accounts, so the real remaining rider gap
is **profile enrichment** (name/email backfill from `customers.csv` onto the phone-stub accounts
already there), not account creation — a smaller, different-shaped problem than Phase 1's driver
gap. Also worth flagging without further digging today: 64/1,238 (5.2%) of `customers.csv` rows
have a blank name too, and — unlike drivers — at least one of those blank-name rows *is* referenced
by a real booking (`customer_id` linkage), so the "zero ride linkage, safe to synthesize a
placeholder" argument from §2 does **not** automatically carry over to riders without re-checking.

## 6. What this means for Oct 30 — recommendation, not a decision

1. Finding 1 (blank name) is closed — shipped in this session, no further action.
2. Finding 2 (existing-match collision) needs an explicit call before Oct 30, the same way the
   blank-name policy did. Recommended shape of that decision (not yet made): treat a phone match
   against an existing `drivers` row as a **metadata-enrichment merge** (attach
   `legacy_import_metadata` history to the existing driver, create no new row) rather than a hard
   error, since it is almost certainly the same person; treat a phone match against a `users`-only
   (rider) row as **safe to skip** the driver-creation attempt entirely, logging it as an
   informational note rather than a batch-blocking error, since the person's new-app identity is
   already the rider account and the ride history linkage this import exists to build doesn't apply
   to a driver profile nobody will use. Both of these are genuine merge-policy changes to
   `build_mongo_driver_import_plan`'s existing-match branch — deliberately not implemented in this
   session (surgical-change discipline: today's task was the blank-name fix, not a redesign of the
   existing-match rule) and flagged here for a real decision before the Oct 30 run.
3. Riders (§5) need their own gap-analysis pass before Oct 30 — profile enrichment from
   `customers.csv`, not account creation, is the actual remaining work, and 5.2% of rows carry the
   same blank-name pattern with a materially different ride-linkage answer than drivers had.
4. `docs/runbooks/legacy-migration-playbook.md` (the canonical Oct 30 checklist) has a new item
   #11 recording all of the above so it isn't rediscovered later.

## 7. Verification performed / not verified

**Performed:** all counts above were run against the real 2026-08-22 Mongo export files and,
for Finding 2, live read-only SQL against the production Supabase project (`soavhtdhefowwvforzwb`).
`ruff check`/`ruff format --check` clean on the changed service/test files;
178/178 tests pass across the full driver-import test family
(`test_legacy_mongo_driver_import_service.py` + the 6 sibling files); the CLI's plan-build step was
re-run end-to-end against the real `drivers.csv` (925 rows) with a mocked, empty Supabase client to
confirm the blank-name fix behaves as described (924/925 clean, 1 residual phone error).

**Not verified:** Finding 2's numbers were computed by phone-matching only (the same predicate the
code itself uses) — a full `commit_mongo_driver_import_plan` dry run against the real production
DB was **not** executed (no write path was exercised against production from this session; the
324/212 figures come from read-only `SELECT`s, not from actually running the importer). The riders
sample (§5) is a 250-of-1,233 random sample, not an exhaustive join — treat the 81.2%/extrapolated
~1,000 figure as an estimate, not an exact count, until a full pass is run. No visual/UI
verification was performed (this session's change is backend-only; the admin-dashboard driver page
already renders `legacy_import_metadata`/`needs_review` badges generically, confirmed by reading
`admin-dashboard/src/app/dashboard/drivers/page.tsx`, but a placeholder name like "Unnamed Legacy
Driver 5439f4" was not screenshotted in that UI).
