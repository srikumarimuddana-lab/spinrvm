# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend, driver-app (i18n only), rider-app (i18n only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/c63-go-online-experience-recheck` — see git log |
| Related issue or gap ID | ACTION_ITEMS.md C63 (split out of A40 ranked blocker #10 / `docs/change-log/2026-08-19-go-online-sk-eligibility-recheck-fix.md`) |

## 1. Issue / gap identified

`go_online`'s eligibility recheck (`routes/drivers/status.py`) validates
licence class and vehicle age, but not CLAUDE.md's Saskatchewan Regulatory
"minimum 3 years licensed driving experience" rule — the 2026-08-19 fix that
added the other two sub-checks explicitly left this one unimplemented
because no schema field recorded a licence-issue date at all.

## 2. Root cause

No column anywhere in `backend/migrations/` or the `drivers` table ever
captured a licence-issue or "licensed since" date — the rule was never
enforced at onboarding either, so there was nothing to re-check on
go-online (confirmed by the 2026-08-19 change log's repo-wide grep, and
re-confirmed here). This was a scoping decision at the time (schema
additions are not a bug-fix commit's call to make silently), not an
oversight — this change closes that deliberately-tracked follow-up.

## 3. Fix / remediation

Two parts:

1. **Migration 405** (`backend/migrations/405_drivers_license_issue_date.sql`)
   adds a nullable `drivers.license_issue_date DATE` column, named to match
   the existing `license_expiry_date` column already on the table. Additive,
   no backfill (see rationale in the migration header and §4 below), with a
   partial index (`WHERE license_issue_date IS NOT NULL`) matching migration
   221's `idx_drivers_license_class`/`idx_drivers_sgi_approved` pattern.
2. **`routes/drivers/status.py`**: a third sub-check added to the same
   `enforce_driver_eligibility_recheck`-gated block as the license-class and
   vehicle-age checks (no new flag). `driver.get("license_issue_date")` is
   read; if `NULL`/missing, the check is skipped (fail-safe — does not
   retroactively lock out drivers who onboarded before this field existed,
   or any driver whose issue date was never captured). If present and it
   implies fewer than 3 years of licensed driving experience as of `now`,
   the same `SpinrException` shape as the other two sub-checks is raised,
   with a new, distinct error pair: `ErrorCode.DRIVER_INSUFFICIENT_EXPERIENCE`
   (5009) / `ErrorKeys.DRIVER_INSUFFICIENT_EXPERIENCE`
   (`errors.driver.insufficient_experience`), 400 status, `action_hint:
   "Contact support"`.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, single call site — identical to the
  2026-08-19 fix.** `update_driver_status` (`PUT /drivers/{id}/status`) still
  has exactly one production caller: the driver-app "Go Online" toggle
  (`driver-app/hooks/useDriverDashboard.ts` →
  `driver-app/components/dashboard/DriverIdlePanel.tsx`). Grepped for every
  reference to `license_issue_date` and every caller of
  `update_driver_status` across the repo: the only non-test files touching
  `license_issue_date` are the new migration and `status.py` itself — no
  other route, service, admin screen, or import script reads or writes it
  (it did not exist before this change, so there is nothing for anything
  else to have depended on). `update_driver_status` callers are otherwise
  unchanged from the 2026-08-19 log: `test_dual_run_hold_guard.py`,
  `test_drivers_shared_status_profile_coverage.py`,
  `test_subscription_enforcement.py`, `test_p1_driver_offline.py`,
  `test_driver_status_insurance_periods.py`, `test_spinr_pass_subscription.py`
  are the other test-file references; no internal backend caller (dispatch,
  admin, background loops) invokes the handler.
- **New failure mode this specifically introduces (why it stays behind the
  existing flag, not hard-blocking):** an unknown number of currently-active
  drivers have no `license_issue_date` on file (the field is brand new —
  every existing driver row has it `NULL` until a separate backfill/capture
  effort, which is explicitly out of scope here). Because the check
  fail-safes on `NULL`, **turning the flag on today changes nothing for any
  existing driver** — the experience sub-check is a no-op until
  `license_issue_date` is populated for a given driver by some future
  onboarding/profile flow (not built in this change). The real behavior
  change only lands the day a driver's `license_issue_date` gets populated
  AND implies <3 years — at that point they can be rejected on their next
  go-online, which is the intended regulatory-closing effect but is new
  input-rejection for a driver who was accepted before.
- No interaction with the ride state machine, wallet/money code paths, or
  the background loops. No insurance-period interaction beyond the existing,
  unchanged rule that Period 1 is blocked while offline.

## 5. User-experience effect

- **Driver-facing**, same visibility profile as the 2026-08-19 fix: only
  reachable on the `is_online: true` transition (never mid-trip), and only
  once `enforce_driver_eligibility_recheck` is explicitly turned on AND the
  driver has a `license_issue_date` on file implying <3 years — neither of
  which is true for any driver today.
- **Shipped default is a no-op for every existing driver**, because
  (a) the flag itself still ships/stays at whatever value ops has already
  set it to (this change does not touch the flag's default), and (b) even
  with the flag on, every existing driver's `license_issue_date` is `NULL`
  and therefore unblocked. There is no user-visible behavior change at merge
  time for anyone.
- i18n: `errors.driver.insufficient_experience` added to both
  `driver-app/i18n/en.json` (driver-facing) and `rider-app/i18n/en.json`
  (generic; riders never trigger this path directly, but the dotted-key
  mirror contract requires the entry). Only `en.json` was touched in each
  app — matching the precedent already set by `license_class_ineligible`/
  `vehicle_too_old`, neither of which touched the `es`/`fr`/`fr-CA`/`en-CA`/
  `zh` locale files either.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/405_drivers_license_issue_date.sql` | New: nullable `drivers.license_issue_date DATE` column + partial index + column comment | Schema field needed to check the 3-year rule at all |
| `backend/routes/drivers/status.py` | Added the third eligibility sub-check (experience) inside the existing `enforce_driver_eligibility_recheck` block, replacing the "not implemented" comment | Closes ACTION_ITEMS.md C63 |
| `backend/utils/error_handling.py` | Added `ErrorCode.DRIVER_INSUFFICIENT_EXPERIENCE = 5009` | Distinct error code per failure reason, matching the existing two sub-checks' pattern |
| `backend/utils/error_keys.py` | Added `ErrorKeys.DRIVER_INSUFFICIENT_EXPERIENCE` | Same |
| `driver-app/i18n/en.json` | Added `errors.driver.insufficient_experience` copy | ErrorKeys i18n-mirror contract |
| `rider-app/i18n/en.json` | Added `errors.driver.insufficient_experience` copy (generic) | Same |
| `backend/tests/test_go_online_availability.py` | Added `license_issue_date` default to `_driver_row`; replaced the old "not enforced" documentation test with 5 new tests in `TestGoOnlineEligibilityRecheck` (pass-through, rejected, just-over-cutoff, missing-field-unblocked, flag-disabled-no-block) | Test coverage for the new sub-check |

No `backend/schemas.py` change: `license_class`/`vehicle_year` are not
self-serve profile fields either (they are import/admin-set), and
`license_issue_date` follows the same shape — this change does not add a
driver-facing or admin-facing way to *set* the field, only the `go_online`
recheck that *reads* it. Populating it is a separate, unbuilt follow-up.

## 7. Before / after

```python
# Before (routes/drivers/status.py, inside the
# enforce_driver_eligibility_recheck block, after the vehicle-age check):

            # Minimum 3 years licensed driving experience: intentionally NOT
            # enforced here even when the flag is on. Repo-wide search
            # ... found no field that records a licence-issue or
            # experience-start date ... flagged as a follow-up in the change
            # log rather than guessed at.
```

```python
# After:

            license_issue_date = driver.get("license_issue_date")
            if license_issue_date:
                if isinstance(license_issue_date, str):
                    try:
                        license_issue_date = datetime.fromisoformat(license_issue_date.replace("Z", "+00:00"))
                    except ValueError:
                        license_issue_date = None
                if isinstance(license_issue_date, datetime):
                    if license_issue_date.tzinfo is None:
                        license_issue_date = license_issue_date.replace(tzinfo=timezone.utc)
                    years_licensed = (now - license_issue_date).days / 365.25
                    if years_licensed < 3:
                        raise SpinrException(
                            message=(
                                "You need at least 3 years of licensed driving experience to "
                                "drive for Spinr. Please contact support."
                            ),
                            error_code=ErrorCode.DRIVER_INSUFFICIENT_EXPERIENCE,
                            status_code=400,
                            message_key=ErrorKeys.DRIVER_INSUFFICIENT_EXPERIENCE,
                            action_hint="Contact support",
                        )
```

**Concrete before/after scenario (dry run, per CLAUDE.md gate #4):** a
driver's `license_issue_date` is set (by some future, unbuilt onboarding
flow) to 14 months ago, and `enforce_driver_eligibility_recheck` is already
on (as set by ops for the license-class/vehicle-age checks).
- **Before this fix**: the driver's `go_online` call is unaffected by
  driving-experience at all — the sub-check does not exist.
- **After this fix, driver has no `license_issue_date` (every driver today)**:
  identical to before — still unaffected, the check is a no-op.
- **After this fix, driver DOES have a `license_issue_date` 14 months ago**:
  rejected with `DRIVER_INSUFFICIENT_EXPERIENCE` ("You need at least 3 years
  of licensed driving experience..."), 400, `action_hint: "Contact support"`.
  This is the intended regulatory-closing behavior, but only becomes
  reachable once something populates the field for that driver — which
  nothing in this change does.

## 8. Rollback plan

- **Primary rollback: flip `app_settings.enforce_driver_eligibility_recheck`
  back to `false`** — same mechanism, same flag, as the 2026-08-19 fix; no
  redeploy, takes effect within the `settings_loader` cache TTL (60s). This
  disables all three sub-checks (license class, vehicle age, experience)
  together, since C63 intentionally rides the existing flag rather than
  adding a new one.
- **If only the new experience sub-check needs to come out** (e.g. the
  license-class/vehicle-age checks should stay on but experience should not),
  the flag alone cannot do that — a code-level revert of this change's
  `status.py` block is the path, which is safe: it only reads
  `driver.get("license_issue_date")` and conditionally raises before any DB
  write in the handler, so a `git revert` of the `status.py`/error-code
  commits is sufficient with no data to unwind.
- **Migration 405 rollback** (if the column itself needs to come out):
  ```sql
  DROP INDEX IF EXISTS public.idx_drivers_license_issue_date;
  ALTER TABLE public.drivers DROP COLUMN IF EXISTS license_issue_date;
  ```
  Safe at any time since nothing else in the codebase reads or writes this
  column (confirmed by grep, §4) and the column holds no data (never
  backfilled) — dropping it loses nothing.
- Since no existing driver has `license_issue_date` populated, **no rollback
  action is needed at merge time** — this change is behaviorally inert for
  every current driver regardless of the flag's state.

## 9. Verification performed

- [x] Automated tests — `pytest backend/tests/test_go_online_availability.py
  -v` (18 tests: 6 pre-existing `TestGoOnlineAvailability` + 12
  `TestGoOnlineEligibilityRecheck`, 9 pre-existing + 5 new minus the removed
  "not enforced" placeholder = 12 net) run against a fresh venv
  (`/tmp/spinr-venv`, `pip install -r backend/requirements.txt`, no cached
  environment was available in this session). Also ran the same regression
  sweep the 2026-08-19 log used: `test_dual_run_hold_guard.py`,
  `test_p1_driver_offline.py`, `test_spinr_pass_subscription.py`,
  `test_subscription_enforcement.py`,
  `test_drivers_shared_status_profile_coverage.py`,
  `test_go_online_availability.py` together.
- [x] `ruff check` on every modified `.py` file (`routes/drivers/status.py`,
  `utils/error_handling.py`, `utils/error_keys.py`,
  `tests/test_go_online_availability.py`) — clean.
- [x] Blast-radius grep performed (see §4) — `license_issue_date` has
  exactly one reader (`status.py`) and one writer path (none yet — the
  column is read-only from application code until a future onboarding/admin
  flow populates it); `update_driver_status`'s caller set is unchanged from
  the 2026-08-19 log.
- [x] Reviewed against CLAUDE.md: fail-safe-on-missing-data direction
  (matches the other two sub-checks), no new flag (rides
  `enforce_driver_eligibility_recheck`), migration is additive/nullable with
  no backfill (gate #2), rollback plan stated before merge (gate #7).
- [ ] Manual repro steps followed in staging — **not done**; no staging
  environment or live Supabase access is available in this session (worktree
  only, no network egress to Supabase/staging/production).
- [ ] Migration not applied to any real database (no `DATABASE_URL` in this
  session) — verified only by reading it against migration 221's pattern and
  `backend/migrations/CLAUDE.md`'s conventions, not by actually running
  `run_migrations.py --dry-run` against a live schema.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip disables all three
  sub-checks together; migration rollback SQL is stated and safe given the
  column has no readers/writers to unwind).
- [x] Blast radius is stated, not assumed (single caller, no other
  reader/writer of the new column anywhere in the repo, confirmed by grep).
- [x] No silent behavior change to an already-shipped flow: with every
  current driver's `license_issue_date` `NULL`, this change is a no-op
  regardless of the flag's state at merge time — stated explicitly in §5.

## What was NOT verified

- **Not tested against a real Supabase instance.** Only against
  `AsyncMock`-patched handler calls
  (`backend.routes.drivers._deps.db_supabase.get_driver_by_id`/`get_rows`/
  `update_one` and `backend.settings_loader.get_app_settings`), the same
  pattern `test_go_online_availability.py` already used. No integration or
  E2E run was performed, and the migration itself was never applied to any
  real (or throwaway) Postgres instance — only read for correctness against
  migration 221's precedent, not executed.
- **No backfill or capture mechanism was built.** This change adds the
  column and the read-side check only; it does not add any way (admin
  screen, onboarding step, import-script field) to actually populate
  `license_issue_date` for a driver. Every existing driver will read `NULL`
  indefinitely until a separate follow-up builds that. Flagging this
  explicitly so the gap is not assumed closed: the schema now *can* record
  the field, but nothing populates it yet, so the regulatory rule remains
  effectively unenforced in production until that follow-up ships.
- **No production build** (`npm run build` / Expo equivalent) was run for
  `driver-app` or `rider-app` — only the two `i18n/en.json` files were
  edited (additive keys, no code path change), matching the scope and
  reasoning of the 2026-08-19 log's equivalent disclosure. No
  `admin-dashboard` files were touched at all.
- No visual/UI change was made, and no visual regression tooling exists in
  this repo for the driver-app/rider-app error-toast surface — not
  screenshotted, reasoned about only.
- Did not investigate or design the future onboarding/backfill mechanism
  that would populate `license_issue_date` for existing or new drivers —
  that decision (how to source the date, whether from a document upload,
  self-attestation, or admin entry) is explicitly out of scope for this
  change and is a real open follow-up, not a closed question.
