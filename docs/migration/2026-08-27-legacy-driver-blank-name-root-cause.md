# Legacy driver blank-name root cause, and a second collision finding surfaced while fixing it

**Status:** both findings decided and shipped. Finding 1 (blank name, §2) shipped as option b.
Finding 2 (existing-match collision rate, §3) was initially left undecided in this doc; the
recommended link/enrich split was then reviewed, authorized, and built the same day — see the
**[DECIDED AND SHIPPED]** update at the end of §3.

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

## 3. Finding 2 — a second, larger blocker surfaced while validating the fix

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

**[DECIDED AND SHIPPED, 2026-08-27, same day.]** The recommended split above was reviewed and
authorized, then built in `build_mongo_driver_import_plan`/`commit_mongo_driver_import_plan`:

- Sub-population 1 (`users`-only match) → **link, don't skip.** The new driver row is still
  created (nothing lost), but points at the *existing* `user_id` instead of a new one (`users.
  phone` is `UNIQUE`, so a new user is never an option here), and `is_driver=True` is set on that
  existing user if not already true. `is_driver`/`is_rider` is a real, load-bearing dual-role flag
  pair already used elsewhere in this codebase (`backend/routes/auth.py`'s FCM-token-on-logout
  handling explicitly branches on a "dual-role account") — this is not a new pattern.
- Sub-population 2 (`drivers` match) → **enrich, don't duplicate.** No new driver row. The
  matched driver's `legacy_import_metadata` gets an additive `mongo_driver_history` entry
  (append-only list, so a phone re-matched across multiple old-app records accumulates real
  history rather than overwriting it); every other field on that live driver — name, phone,
  status, vehicle, rating, `is_verified`/`is_online`/`is_available` — is never touched.
- Both merges follow the additive-merge-under-a-namespaced-key convention already established
  elsewhere in this codebase (`stripe_mapping_import_service.py`'s `legacy_import_metadata.
  stripe_migration`, `rider_import_service.py`'s `legacy_import_metadata.rider_csv_import`) rather
  than inventing a new shape — confirmed safe against real production `legacy_import_metadata`
  values (fetched read-only, phones only, no names) which already carry unrelated prior-importer
  keys (`stripe_migration`, `rider_csv_import`, `address_present`, …) that the merge correctly
  leaves untouched.
- A driver/account already linked or enriched by a *previous* run of this importer for the same
  `old_driver_id` is a resume (skip, warning), not a duplicate history entry — checked against
  both the original top-level `source`/`old_driver_id` shape (a driver this importer directly
  created) and the new `mongo_driver_history` list shape (a driver/account this importer linked
  or enriched), via `_mongo_driver_already_linked`.
- **Not done, and out of scope for this change:** actually running `--apply` against production.
  This session has no live write credentials for the backend service role, matching every other
  importer in this migration effort — building and validating the code is this session's job,
  executing it is the product owner's, per the established pattern in
  `docs/runbooks/legacy-migration-playbook.md`.

Verification: 25 unit tests (`test_legacy_mongo_driver_import_service.py`, up from 22), covering
both sub-populations, the resume check under both metadata shapes, and that a live driver's own
fields are never touched. Additionally validated against **real production `legacy_import_metadata`
shapes** (fetched read-only via the Supabase MCP, phones only — no names, no full row dumps kept):
confirmed the additive merge preserves an existing driver's `stripe_migration` sub-key and an
existing user's `rider_csv_import` sub-key untouched, and that `role`/`status`/other live fields on
a matched account are never included in the update payload.

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

## 6. What this means for Oct 30

1. Finding 1 (blank name) is closed — shipped, no further action.
2. Finding 2 (existing-match collision) is closed too. **Correction from this doc's own earlier
   draft:** the recommendation originally written here for sub-population 1 was "safe to skip" —
   on reflection (put to the product owner, who authorized building it) that was the wrong call:
   skipping throws away exactly the history this importer exists to build, and the only real
   obstacle (`users.phone` is `UNIQUE`) is solved by linking to the existing user, not by dropping
   the row. What actually shipped is the "link, don't skip" / "enrich, don't duplicate" split
   described in §3's `[DECIDED AND SHIPPED]` block above.
3. Riders (§5) still need their own gap-analysis pass before Oct 30 — profile enrichment from
   `customers.csv`, not account creation, is the actual remaining work, and 5.2% of rows carry the
   same blank-name pattern with a materially different ride-linkage answer than drivers had. One
   incidental finding while validating Finding 2 against real production data, not chased further
   here since it's outside this change's scope: at least some riders already carry a
   `legacy_import_metadata.rider_csv_import` marker (source `legacy_rider_csv_import`, batch
   `20260817023332`) — meaning some rider profile enrichment may have already happened via a path
   this doc didn't account for. Worth a real look before assuming §5's "not yet enriched" framing
   is fully accurate for every rider.
4. `docs/runbooks/legacy-migration-playbook.md` (the canonical Oct 30 checklist) has item #11
   recording all of the above so it isn't rediscovered later.

## 7. Verification performed / not verified

**Performed:** all counts above were run against the real 2026-08-22 Mongo export files and,
for Finding 2, live read-only SQL against the production Supabase project (`soavhtdhefowwvforzwb`).
`ruff check`/`ruff format --check` clean on the changed service/test files; 180/180 tests pass
across the full driver-import test family (25 in `test_legacy_mongo_driver_import_service.py` +
the 6 sibling files, zero collateral breakage). The CLI's plan-build step was re-run end-to-end
against the real `drivers.csv` (925 rows) three times: against an empty mocked Supabase (confirms
the blank-name fix alone, 924/925 clean, 1 residual phone error, unchanged from before the
link/enrich change — a real regression check, not just "tests still pass"); against a store seeded
with real production `legacy_import_metadata` shapes for two real phones from the export (confirms
the additive merge preserves an existing driver's `stripe_migration` key and an existing user's
`rider_csv_import` key untouched, and that no live field — `status`, `role`, etc. — is ever written
by the update payload); and the full-export run surfaced 3 occurrences each of both seeded phones
(the real export has old-app duplicate signups sharing a phone number), confirming the resume check
correctly accumulates distinct history entries rather than colliding on them.

**Not verified:** a full `commit_mongo_driver_import_plan` --apply run against the real production
DB was **not** executed — no write path was exercised against production from this session, only
read-only `SELECT`s to size the findings and confirm metadata shapes; the product owner runs
`--apply`, per the established pattern in `docs/runbooks/legacy-migration-playbook.md`. The riders
sample (§5) is a 250-of-1,233 random sample, not an exhaustive join — treat the 81.2%/extrapolated
~1,000 figure as an estimate, not an exact count, until a full pass is run. No visual/UI
verification was performed (this session's change is backend-only; the admin-dashboard driver page
already renders `legacy_import_metadata`/`needs_review` badges generically, confirmed by reading
`admin-dashboard/src/app/dashboard/drivers/page.tsx`, but a placeholder name like "Unnamed Legacy
Driver 5439f4" was not screenshotted in that UI).
