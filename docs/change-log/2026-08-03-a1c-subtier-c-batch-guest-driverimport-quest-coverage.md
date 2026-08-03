# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate (guest notifications), drivers (bulk import), drivers (quest tracker) |
| PR / commit link | (branch: `claude/a1c-subtier-c-batch-guest-driverimport-quest`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C, "Batch 4" of the itemized 60-80% coverage-band file list |

## 1. Issue / gap identified

Three files sat in the 60-80% Sub-tier C coverage band, below the ≥70%
utilities target:

- `backend/services/guest_notification_service.py` — 70.34% (118 stmts).
- `backend/services/driver_import_service.py` — 70.34% (381 stmts — the
  largest file in this batch).
- `backend/utils/quest_tracker.py` — 70.42% (71 stmts).

## 2. Root cause

`guest_notification_service.py` already had a companion test file
(`test_guest_sms.py`, 5 tests) pinning the PII-safe-logging contract and the
two most common paths (new booking SMS, app-holder push), but every guard
clause's *positive* branch and every crash/exception path were untested:
`_send_guest_sms`'s crash-not-just-failure path, `_guest_recipient`'s three
guard clauses (no rider_id / user not found / no phone), `_company_name`'s
no-id and DB-exception fallbacks, `_ensure_tracking_token`'s reuse-existing-
token and mint-failure branches, the scheduled-ride SMS body, the no-phone
guard in `notify_guest_booking_created`, and — most notably —
`notify_guest_driver_arrived` and `notify_guest_cancelled` only ever had
their early-return guard exercised (via `test_claimed_account_stops_sms`),
never their actual SMS-send body.

`driver_import_service.py`'s two existing test files
(`test_driver_import_service.py`, `test_admin_driver_import.py`, 22 tests
combined) covered `build_plan`'s prefetch/resume/web-flow-rejection
semantics and the admin HTTP endpoints thoroughly, but: none of the small
pure helpers (`parse_bool`, `parse_date`, `date_is_ambiguous`, `split_name`,
`normalize_phone`, `canonical_requirement_key`, `work_auth_status`,
`regulatory_authority_defaults`) were exercised directly branch-by-branch;
`storage_signed_url`/`encrypt_pii` had no test touching `supabase.storage`/
`supabase.rpc` at all; `get_service_area`'s by-id and multiple-match/no-match
branches were never called (only the by-name single-match path, indirectly,
via the admin route); `build_plan`'s duplicate-`old_driver_id`,
wrong-service-area, unparseable-DOB, and ambiguous-date-warning branches were
untested; and the **entire CLI document-row pipeline** (`build_plan` called
with `files_root` set, and all of `commit_plan`'s file-upload/document-insert
logic, and `print_report`) had zero coverage — every existing test either
used `files_root=None` (web flow, which just rejects document rows outright)
or never called `commit_plan` directly (the admin-route tests exercise it
indirectly but only the empty-`drivers_to_update`/no-documents happy path).

`quest_tracker.py` had a companion `TestQuestTrackerOnRideComplete` class in
`test_quests.py` (4 tests) covering the `ride_count` happy path and the
`peak_rides` local-timezone math, but: the progress-fetch DB-error guard, a
missing/inactive quest, an expired quest, the `earnings_target` quest type,
the `service_areas` timezone lookup (both success and its swallowed-exception
fallback — the existing peak-hour tests deliberately kept `service_area_id:
None` to avoid `test_quests.py`'s shared `make_mock_db()` raising on an
unconfigured `service_areas` sub-mock), an invalid area timezone, a naive
(no-tzinfo) `completed_at`, no usable completion timestamp at all, and the
per-progress exception guard (one bad quest row must not abort the batch)
were all untested.

## 3. Fix / remediation

Test-only change across three new files, no application code modified:

- `backend/tests/test_guest_notification_service_coverage.py` (17 tests) —
  the crash path, all three `_guest_recipient` guards, `_company_name`'s
  no-id/exception paths, `_ensure_tracking_token`'s reuse/mint-failure
  branches, the no-phone guard and scheduled-ride body (including
  `_local_time`'s naive-timestamp and unparseable-timestamp branches) in
  `notify_guest_booking_created`, and full send-path tests for
  `notify_guest_driver_arrived`/`notify_guest_cancelled` (previously only
  their guards were covered).
- `backend/tests/test_driver_import_service_coverage.py` (65 tests) — every
  pure helper directly (including the CSV-parsing helpers
  `parse_csv_rows`/`read_csv`/`read_csv_text`), `storage_signed_url`/
  `encrypt_pii` against a new fake `supabase.storage`/`supabase.rpc`,
  `get_service_area`'s by-id/no-match/multiple-match branches,
  `vehicle_type_map`'s skip-row-without-id defensive branch, `build_plan`'s
  duplicate-old-id/wrong-area/bad-DOB/ambiguous-date/empty-CSV branches, the
  full CLI document-row pipeline (happy path, disallowed requirement key,
  invalid status, file-not-found, resumed-driver-skips-existing-doc,
  ambiguous/unparseable expiry), `commit_plan` end-to-end (refuses on
  errors, inserts users/drivers/vehicle-updates/documents, uploads files,
  encrypts `license_number` while leaving VIN plaintext, skips a
  no-op update defensively), and `print_report`'s console summary.
- `backend/tests/test_quest_tracker_coverage.py` (14 tests) — the
  progress-fetch DB error, missing/inactive quest, expired quest,
  `earnings_target` quest type (including the `driver_earnings`-missing
  default-to-zero branch), the per-progress exception guard (one bad row
  doesn't abort the batch — a broken quest row missing `type`/
  `target_value` raises `KeyError` mid-loop, caught and logged, next row
  still processed), and every `_ride_local_completion_hour` branch (no
  timestamp, unparseable timestamp, naive timestamp, area-timezone lookup
  success/exception/missing-field, and an invalid area timezone string).

**No bugs found in the reviewed application code.** One observation, not a
bug: `driver_import_service.py`'s `parse_date`/`date_is_ambiguous` both
contain a manual "`if parsed.year < 100: parsed = parsed.replace(year=...+
2000)`" adjustment after a `%y`-format `strptime` — this appears to be dead
code, since Python's own `%y` parsing already pivots two-digit years into
the 1969-2068 range before the manual check runs (confirmed empirically: a
`%d-%b-%y` parse of a 2-digit year already returns a 4-digit year). Coverage
on those two lines (197, 226) could not be closed by any input because the
branch appears genuinely unreachable given current stdlib behavior. Left
as-is (out of scope for a test-only pass to "fix" application logic) but
flagged here for whoever next touches this file.

## 4. Risk & impact on existing functionality

**Blast radius: test-only, zero application code touched in any of the three
target files.** Every new test file was run standalone and then combined
with every pre-existing test file that touches the same module — see §9.

- `guest_notification_service.py` — callers are `routes/rides/matching.py`
  (`notify_guest_booking_created`) and `routes/drivers/ride_flow.py`
  (driver-assigned/arrived/cancelled notifications), both `spawn()`-ing these
  helpers off the hot path per the module's own docstring contract ("never
  raise into their caller"). No caller was touched; the new crash-path test
  confirms that contract still holds. PIPEDA: every new test that logs
  anything asserts the log stays free of full phone/name/address/OTP, same
  discipline as the pre-existing `test_logs_stay_pii_clean_on_sms_failure`.
- `driver_import_service.py` — callers are `scripts/import_saskatoon_drivers.py`
  (CLI) and `routes/admin/driver_import.py` (admin HTTP flow, already tested
  end-to-end in `test_admin_driver_import.py`, re-run unchanged in §9 to
  confirm no collision with the new fake-supabase helpers). The new
  `commit_plan` tests write through a from-scratch fake `supabase` object
  local to this file (not shared/mutated global state) so there is no
  interaction with the real `driver_import_token.py` validation-token flow
  the admin route layers on top.
- `quest_tracker.py` — sole caller is the ride-completion flow
  (`drivers.py::complete_ride`, `rides.py::rider_complete_ride`, per the
  module docstring and the existing `TestQuestTrackerOnRideComplete` class
  header comment). Not modified; new tests patch `utils.quest_tracker.db`
  the same way the existing suite does (`monkeypatch.setattr` instead of
  `unittest.mock.patch`, functionally identical). The new mock db
  (`_mock_db()` in the new file) is intentionally *not* the shared
  `make_mock_db()` from `test_quests.py` — that helper's bare
  `MagicMock()`-per-table dispatch has no `service_areas` sub-mock
  configured, so awaiting an unconfigured attribute there raises; the new
  local helper uses plain `AsyncMock`s for `get_rows`/`find_one`/`update_one`
  so a peak_rides test can freely set `service_area_id` to something
  non-`None` without touching `test_quests.py`'s fixture.

## 5. User-experience effect

None — test-only change, no rider/driver/corporate-admin/internal-admin
facing behavior change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_guest_notification_service_coverage.py` | New file — 17 tests | Close the coverage gap on `services/guest_notification_service.py` (70.34% → 96%) |
| `backend/tests/test_driver_import_service_coverage.py` | New file — 65 tests | Close the coverage gap on `services/driver_import_service.py` (70.34% → 99%) |
| `backend/tests/test_quest_tracker_coverage.py` | New file — 14 tests | Close the coverage gap on `utils/quest_tracker.py` (70.42% → 99%) |
| `ACTION_ITEMS.md` | A1c Sub-tier C — marked Batch 4 closed with before/after numbers | Track progress per the existing series format |
| `docs/change-log/2026-08-03-a1c-subtier-c-batch-guest-driverimport-quest-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (corporate guest bookings, driver onboarding, driver quests) |

## 7. Before / after

Not applicable — purely additive test files; no existing application-code
behavior-changing diff to show.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, no feature flag needed.

## 9. Verification performed

- [x] New test files run alone:
  - `pytest tests/test_guest_notification_service_coverage.py -q --no-cov` → **17 passed** (two of the seventeen were added in a follow-up pass for `_local_time`'s naive-timestamp/unparseable-timestamp branches once the first combined coverage run showed them still missing).
  - `pytest tests/test_driver_import_service_coverage.py -q --no-cov` → **65 passed**.
  - `pytest tests/test_quest_tracker_coverage.py -q --no-cov` → **14 passed**.
- [x] Run together with every pre-existing test file touching each module
  (confirmed no collisions):
  - `pytest tests/test_guest_sms.py tests/test_guest_notification_service_coverage.py --cov=services.guest_notification_service --cov-report=term-missing` → **22 passed**, `services/guest_notification_service.py` **70.34% → 96%** (118 stmts, 5 lines still missing — all in the dual-import `ImportError` fallback, see §"What was NOT verified").
  - `pytest tests/test_driver_import_service.py tests/test_admin_driver_import.py tests/test_driver_import_service_coverage.py --cov=services.driver_import_service --cov-report=term-missing` → **87 passed**, `services/driver_import_service.py` **70.34% → 99%** (381 stmts, 4 lines still missing: the import fallback, the two dead `year<100` branches noted in §3, and one defensive empty-batch guard in `_select_in` that current call sites never trigger).
  - `pytest tests/test_quests.py tests/test_quest_tracker_coverage.py --cov=utils.quest_tracker --cov-report=term-missing` → **46 passed**, `utils/quest_tracker.py` **70.42% → 99%** (71 stmts, 1 line still missing — the import fallback).
  - All six files together (`test_guest_sms.py`, `test_guest_notification_service_coverage.py`, `test_driver_import_service.py`, `test_admin_driver_import.py`, `test_driver_import_service_coverage.py`, `test_quests.py`, `test_quest_tracker_coverage.py`) in one run, three `--cov` targets at once → **155 passed**, same three coverage numbers, no cross-file collisions.
- [x] Ruff lint on the three new files: `ruff check tests/test_guest_notification_service_coverage.py tests/test_driver_import_service_coverage.py tests/test_quest_tracker_coverage.py` → **All checks passed!**
- [x] Blast-radius grep performed — see §4; every real caller of each target
  module enumerated (`routes/rides/matching.py`, `routes/drivers/ride_flow.py`
  for guest notifications; `scripts/import_saskatoon_drivers.py`,
  `routes/admin/driver_import.py` for driver import; `routes/drivers.py`,
  `routes/rides.py` ride-completion handlers for quest tracker). Also
  grepped `git branch -r | grep a1c-subtier-c` and `ACTION_ITEMS.md` and
  used `mcp__github__list_pull_requests` before starting to confirm no
  concurrent session had already closed or was actively closing these three
  files (none had).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — dual-import
  pattern respected (not simplified away; its untestable fallback branches
  are the only remaining coverage gaps, called out explicitly rather than
  silently accepted), "do not silently swallow errors" (every new
  crash/exception-path test asserts the *existing* code's documented
  swallow-and-log behavior for these best-effort/spawn()ed helpers — no new
  swallow point was introduced), patch-target convention (`services.
  driver_import_service.supabase` and `utils.quest_tracker.db` — the module
  that defines the function under test, matching the two existing
  `driver_import_service` test files' own established pattern).
- [ ] Full backend suite — **explicitly deferred** per this batch's
  instructions ("the user has explicitly asked to defer full-suite/CI
  verification to a later consolidated pass across all in-flight batches, to
  conserve tokens"). Only the standalone/combined runs listed above were
  run.
- [ ] Manual repro against real Supabase/Twilio — not applicable; every
  DB/SMS/push/storage call is mocked throughout, matching this test tier's
  existing convention for all three target files' companion test suites.
- [ ] Feature-flagged — not applicable; test-only, no deployable behavior
  difference.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — test-only, every touched/added
  file enumerated in §6, every real caller of each target module enumerated
  in §4
- [x] No silent behavior change to an already-shipped flow — zero
  application code modified in this pass

## What was NOT verified

- **Full backend suite was not run** for this batch, per explicit
  instruction to defer full-suite/CI verification to a later consolidated
  pass across all in-flight A1c batches. Only the new files' standalone runs
  and combined runs with each target module's pre-existing companion test
  file(s) were verified (§9). A prior same-day batch (Batch 11, same series)
  did run the full suite and found a pre-existing `sys.modules` pollution
  issue unrelated to its own changes — that risk class (cross-test global
  state leaking between unrelated test files) has not been re-checked for
  this batch's three new files specifically; they were, however, run
  together in one process (§9) with no observed interaction.
- Not exercised against real Supabase, Twilio, or Stripe — every test mocks
  the relevant client/SDK call or a from-scratch fake `supabase` object,
  consistent with this repo's existing convention for this whole test tier
  (unit, not integration).
- No visual/UI surface is touched by this change (backend test-only), so the
  standing "no automated visual regression tooling" gap noted elsewhere in
  this backlog does not apply here.
- `driver_import_service.py`'s CLI entry point
  (`scripts/import_saskatoon_drivers.py`, which calls `read_csv`/`build_plan`/
  `commit_plan`/`print_report` together end-to-end from argv) was not run as
  a subprocess — only its constituent functions were tested directly, same
  boundary the two pre-existing companion test files already accept.
- The four coverage lines left uncovered on `driver_import_service.py` (the
  import fallback, the two dead `year<100` branches, and the defensive
  empty-batch guard in `_select_in`) were investigated, not just abandoned —
  see §3 and §9 for why each is believed genuinely unreachable rather than a
  gap this pass failed to close.
