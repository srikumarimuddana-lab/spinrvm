# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend, driver-app (i18n only), rider-app (i18n only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | local worktree commit — see git log (`fix(regulatory): re-check SK driver eligibility ...`) |
| Related issue or gap ID | Ranked blocker #10, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` |

## 1. Issue / gap identified

`routes/drivers/status.py`'s go-online endpoint re-checks document expiry
(licence/insurance/inspection/background-check) on every `go_online` call,
but never re-checks two other Saskatchewan driver-eligibility rules from
CLAUDE.md's regulatory section: licence class (must be Class 5 standard;
Class 1-4 needs separate approval) and vehicle age (< 10 years old). A
third rule — minimum 3 years licensed driving experience — is also unchecked,
but for a different reason (see §2/§3 below). All three were only ever
validated once, at onboarding/import time.

## 2. Root cause

The go-online handler's eligibility gate was built incrementally around
document expiry (with its own `driver_documents`/legacy-column fallback
logic) and a Spinr Pass subscription check, but `license_class` and
`vehicle_year` — both present on the `drivers` table since
`221_drivers_bulk_import_fields.sql` and `08_complete_schema.sql`
respectively — were never wired into that gate. They're self-serve/import
fields (`routes/drivers/profile.py`) with no re-validation anywhere. The
3-years-experience rule was never even captured as data: a repo-wide search
(`grep -rniE "license_issue_date|licensed_since|driving_experience|years_of_experience|license_since|date_licensed"` across every `.py`/`.sql` file) found no
column or field recording a licence-issue or experience-start date, in
`drivers`, `driver_documents`, or anywhere else. The rule was never enforced
at onboarding either — there is nothing to re-check.

## 3. Fix / remediation

Added two new eligibility checks to the go-online flow, in the same
place/pattern as the existing document-expiry check, each behind an
`app_settings` flag `enforce_driver_eligibility_recheck` (default `false`,
see §5 rollout note):

- **License class**: reject Class 1-4 unless `drivers.sgi_approved` is
  `True`. **No dedicated "separately approved for non-standard class" field
  exists in the schema** — `sgi_approved` (added in the same migration as
  `license_class`, documented as "Whether imported records indicate SGI
  approval for passenger-for-hire/rideshare operation") is reused here as
  the closest existing signal. **This is an assumption, not a confirmed
  mapping** — flagging explicitly for reviewer judgment: `sgi_approved` is a
  general operating-approval flag from legacy imports, not documented
  anywhere as meaning "approved despite a non-standard licence class." If
  this mapping is wrong, the fix to make is narrowing/renaming the field
  usage, not adding a migration blind.
- **Vehicle age**: reject when `now.year - drivers.vehicle_year >= 10`.
- **Driving experience (3-year minimum): intentionally NOT implemented.**
  No schema field exists to check against, and per this task's explicit
  instruction and CLAUDE.md's migration conventions, adding one is not a
  decision to make silently inside a bug-fix commit. Tracked as a **required
  follow-up**: decide (a) what field to add (e.g. `license_issued_date`),
  (b) how existing/legacy drivers get backfilled (a mandatory field with no
  historical data would block every existing driver — see the B14 precedent
  where a similar backfill gap already caused a real 22-driver incident),
  and (c) whether it needs its own flag or rides the same
  `enforce_driver_eligibility_recheck` flag. Do not add this without a
  human decision on the backfill story.

Both implemented checks raise `SpinrException` with a distinct
`ErrorCode`/`ErrorKeys` pair each (not a generic "ineligible" bucket):
`DRIVER_LICENSE_CLASS_INELIGIBLE` (5007) / `errors.driver.license_class_ineligible`,
and `DRIVER_VEHICLE_TOO_OLD` (5008) / `errors.driver.vehicle_too_old` — same
400 status code and `SpinrException` shape as the existing document-expiry
check, so the driver app's existing error-handling path needs no changes.
A driver with no `license_class` or no `vehicle_year` on file is left
unblocked (not guessed at) — consistent with how the existing document-expiry
block already treats missing legacy-column data.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, single call site.** `update_driver_status`
  (`PUT /drivers/{id}/status`) has exactly one production caller: the
  driver-app "Go Online" toggle (`driver-app/hooks/useDriverDashboard.ts`,
  wired through `driver-app/components/dashboard/DriverIdlePanel.tsx`).
  Grepped for every other caller of the endpoint/function — no internal
  backend caller invokes it (dispatch, admin, and background loops read
  `is_online`/`is_available` but never write them through this handler);
  `driver-app/e2e/fixtures.ts` and `driver-app/e2e/online-toggle.spec.ts`
  are the only other references and are test-only. No rider-app or
  admin-dashboard caller exists.
- **What else reads `license_class`/`vehicle_year`**: `routes/admin/documents.py`
  (B14 gate, read-only existence check before approving a licence document),
  `routes/admin/compliance.py` and `routes/admin/drivers.py` (admin listing/export,
  read-only), `services/driver_import_service.py` and
  `services/data_transfer/sgi_field_maps.py` (import/export, read-only),
  `routes/admin/rides.py` (read-only surfacing). None of these write these
  fields as a side effect of this change, and none are touched by this fix.
- **Real regression risk this fix introduces (why it's flag-gated, not
  hard-blocking): an unknown number of currently-active, already-verified
  drivers may have a non-Class-5 `license_class` or an aged `vehicle_year`
  on file** (self-serve/import fields that were never enforced anywhere —
  see B14, which documented an almost-identical 22-driver backfill gap for
  the same `license_class`/`license_number` pair). Turning this check on
  unconditionally, in a live-tested app, could silently lock out drivers who
  have been driving fine for months. That's a real fleet-availability
  regression, not a hypothetical one — hence the flag (see §5/§8).
- No interaction with the ride state machine, wallet/money code paths, or
  the 18 background loops. No insurance-period interaction beyond the
  existing rule that Period 1 is blocked while offline (unchanged).

## 5. User-experience effect

- **Driver-facing.** Visible mid-session in the sense that a driver who was
  online, went offline, and taps "Go Online" again could — once the flag is
  turned on — receive a new rejection they didn't get before, with a clear,
  specific reason (distinct message per failure type, not a generic
  "ineligible" bucket message) and an actionable hint ("Contact support" /
  "Update vehicle in Profile").
  - **Not visible while already online mid-trip** — the check only runs on
    the `is_online: true` transition, same as the existing document-expiry
    gate; a driver mid-trip is never re-evaluated.
- **Shipped default is OFF** (`enforce_driver_eligibility_recheck: false`).
  With the flag off — the state this commit actually deploys as — there is
  **no user-visible behavior change at all**. The flag must be explicitly
  turned on via the admin `app_settings` mechanism for any driver to ever
  see the new errors.
- i18n: new keys added to both `rider-app/i18n/en.json` and
  `driver-app/i18n/en.json` under `errors.driver.license_class_ineligible`
  and `errors.driver.vehicle_too_old`, matching `ErrorKeys`' documented
  contract ("every key has an i18n entry in rider-app and driver-app under
  the same dotted path"). Rider-app copy is generic/informational since
  riders never trigger this path directly but the dotted-key contract
  requires the mirror entry to exist.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/status.py` | Added flag-gated license-class and vehicle-age re-checks to the go-online flow; documented why the 3-year-experience check is not implemented | Close ranked audit blocker #10 |
| `backend/utils/error_handling.py` | Added `ErrorCode.DRIVER_LICENSE_CLASS_INELIGIBLE` (5007), `ErrorCode.DRIVER_VEHICLE_TOO_OLD` (5008) | Distinct error codes per failure reason, not a generic bucket |
| `backend/utils/error_keys.py` | Added `DRIVER_LICENSE_CLASS_INELIGIBLE`, `DRIVER_VEHICLE_TOO_OLD` i18n keys | Same reason |
| `backend/schemas.py` | Added `AppSettings.enforce_driver_eligibility_recheck: bool = False` | Feature flag, `app_settings`-in-DB pattern, default off |
| `rider-app/i18n/en.json` | Added the two new `errors.driver.*` copy strings | ErrorKeys i18n-mirror contract |
| `driver-app/i18n/en.json` | Added the two new `errors.driver.*` copy strings (driver-facing wording) | Same |
| `backend/tests/test_go_online_availability.py` | Extended `_driver_row` with valid `license_class`/`vehicle_year` defaults; added `TestGoOnlineEligibilityRecheck` (9 new tests: pass-through, class-4-rejected, class-4-with-sgi-approval, missing-license-class-unblocked, vehicle-10-years-rejected, vehicle-9-years-ok, missing-vehicle-year-unblocked, flag-disabled-no-block, experience-check-documented-as-not-enforced) | Test coverage for the fix, including the flag-off dark-ship path |

## 7. Before / after

```python
# Before (routes/drivers/status.py, go-online flow) — license_class and
# vehicle_year were never read at all in this handler; only document
# expiry (license_expiry_date, insurance_expiry_date, etc.) was re-checked.
        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).

        # Check active Spinr Pass subscription.
        ...
```

```python
# After — flag-gated re-check inserted between the document-expiry gate and
# the Spinr Pass subscription gate:
        if bool(app_settings.get("enforce_driver_eligibility_recheck", False)):
            license_class = (driver.get("license_class") or "").strip().upper()
            if license_class and license_class not in ("5", "5A", "5-A") and not driver.get("sgi_approved"):
                raise SpinrException(
                    message=f"Your driver's licence class ({license_class}) requires separate SGI "
                            "approval to drive for Spinr. Please contact support.",
                    error_code=ErrorCode.DRIVER_LICENSE_CLASS_INELIGIBLE,
                    status_code=400,
                    message_key=ErrorKeys.DRIVER_LICENSE_CLASS_INELIGIBLE,
                    action_hint="Contact support",
                )
            vehicle_year = driver.get("vehicle_year")
            if vehicle_year:
                vehicle_age = now.year - int(vehicle_year)
                if vehicle_age >= 10:
                    raise SpinrException(
                        message=f"Your vehicle ({vehicle_year}) is {vehicle_age} years old and no "
                                "longer eligible to drive for Spinr. Vehicles must be under 10 years old.",
                        error_code=ErrorCode.DRIVER_VEHICLE_TOO_OLD,
                        status_code=400,
                        message_key=ErrorKeys.DRIVER_VEHICLE_TOO_OLD,
                        action_hint="Update vehicle in Profile",
                    )
        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).
```

**Concrete before/after scenario (dry run, per CLAUDE.md gate #4):** a driver
whose vehicle was manufactured in 2016 has been driving actively since 2020
with no issue (vehicle age was never checked). On 2026-08-19 the vehicle
crosses the 10-year mark (2026 - 2016 = 10). The driver ends a shift, goes
offline, and taps "Go Online" the next morning.
- **Before this fix**: succeeds unconditionally — vehicle age was never read.
- **After this fix, flag OFF (the shipped default)**: still succeeds
  unconditionally — identical to before. No behavior change until an
  operator opts in.
- **After this fix, flag ON**: rejected with `DRIVER_VEHICLE_TOO_OLD`
  ("Your vehicle (2016) is 10 years old and no longer eligible..."), 400,
  `action_hint: "Update vehicle in Profile"`. The driver cannot go online
  again until they update their vehicle record (or an admin/support
  intervenes) — this is the intended regulatory-closing behavior, but it is
  a new rejection for input that was previously accepted, which is exactly
  why it ships dark.

## 8. Rollback plan

- **Primary rollback: flip `app_settings.enforce_driver_eligibility_recheck`
  back to `false`** via the admin dashboard / `settings` table — takes
  effect within the existing `settings_loader` cache TTL (60s), no
  redeploy, no code change. This is the intended rollback path and the
  reason the flag exists.
- Since the flag ships `false` by default, **no rollback action is needed
  at merge time** — the new checks are inert until explicitly enabled.
- If a code-level revert is ever needed instead: `git revert` is sufficient
  here because this change writes no data and never mutates `drivers` rows
  — it only reads `license_class`/`vehicle_year` and conditionally raises
  before any DB write in the handler. No wallet/Stripe/ride-state
  side effects to unwind.

## 9. Verification performed

- [x] Automated tests run — unit-style handler tests (not DB-integration):
  `pytest backend/tests/test_go_online_availability.py -v` → **15 passed**
  (6 pre-existing + 9 new). Regression sweep across every other test file
  found touching the go-online handler:
  `pytest backend/tests/test_dual_run_hold_guard.py backend/tests/test_p1_driver_offline.py backend/tests/test_spinr_pass_subscription.py backend/tests/test_subscription_enforcement.py backend/tests/test_drivers_shared_status_profile_coverage.py backend/tests/test_go_online_availability.py -q --no-cov`
  → **162 passed**, using `/tmp/spinr-venv/bin/pytest`. This is a real venv
  run, not just `ruff`/`tsc --noEmit`.
- [x] `ruff check` run on every modified `.py` file
  (`routes/drivers/status.py`, `utils/error_handling.py`, `utils/error_keys.py`,
  `schemas.py`, `tests/test_go_online_availability.py`) — all clean.
- [x] Blast-radius grep performed (see §4) — endpoint has a single caller
  (driver-app "Go Online" toggle); five other files read
  `license_class`/`sgi_approved` but only for admin display/export, none
  write them.
- [x] Reviewed against CLAUDE.md conventions: "Do not silently swallow
  errors" (no new DB calls added — this reuses `driver` and the already-fetched
  `app_settings`; no new failure surface to swallow), "Driver eligibility ...
  checked at onboarding + every go_online" (this is exactly the gap closed),
  gate #3 (feature-flagged), gate #4 (dry-run scenario in §7), gate #7
  (rollback plan stated before merge).
- [x] Feature-flagged (`enforce_driver_eligibility_recheck`, default off) —
  see §3/§5/§8 for the explicit justification.
- [ ] Manual repro steps followed in staging — **not done**; no staging
  environment was available in this session (local worktree only, no
  network egress to Supabase/staging).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flip the flag; verified the
  flag path itself via `test_flag_disabled_does_not_block_otherwise_ineligible_driver`).
- [x] Blast radius is stated, not assumed (single caller, five read-only
  consumers of the touched fields, all named).
- [x] No silent behavior change to an already-shipped flow: the fix ships
  with the new behavior OFF by default; §5 states explicitly that with the
  flag off there is zero user-visible change at merge time.

## What was NOT verified

- **Not tested against a real Supabase instance** — only against
  `mock_supabase_client`-style patched handler calls (`AsyncMock` on
  `db_supabase.get_driver_by_id`/`get_rows`/`update_one` and
  `settings_loader.get_app_settings`), per this repo's standard unit-test
  pattern. No integration or E2E run was performed.
- **The `sgi_approved`-as-"separately approved" mapping is an assumption**,
  not a confirmed product/legal decision — see §3. Needs explicit reviewer
  sign-off before the flag is ever turned on in production; if the mapping
  is wrong, drivers could be wrongly blocked (false positive) or wrongly
  let through (false negative) on the license-class check specifically.
- **The 3-year driving-experience rule is not implemented at all** — see §3
  — because no schema field exists to check against. This is a real,
  intentionally-unclosed portion of CLAUDE.md's regulatory requirement, not
  an oversight; it needs its own follow-up work item (schema decision +
  backfill plan) before it can be closed.
- No visual/UI change was made (only new error strings reachable when the
  flag is on), and no visual regression tooling exists in this repo for the
  driver-app error-toast surface — not screenshotted, reasoned about only.
- **No production build** (`npm run build` / Expo equivalent) was run for
  `driver-app` or `rider-app` — only the two `i18n/en.json` JSON files were
  edited (additive keys, no code path change), and no `admin-dashboard`
  files were touched at all, so a JS/TS production build was judged
  out of scope for this backend-plus-i18n-copy change. Flagging explicitly
  per CLAUDE.md's requirement to state this, rather than let it be assumed.
- Did not check whether an admin-dashboard UI surface exists (or should be
  added) to toggle `enforce_driver_eligibility_recheck` specifically — it's
  reachable via the existing generic `app_settings` admin mechanism today
  (same as `require_driver_subscription`), but a dedicated, clearly-labeled
  toggle with the driver-lockout risk called out was not built as part of
  this fix.
