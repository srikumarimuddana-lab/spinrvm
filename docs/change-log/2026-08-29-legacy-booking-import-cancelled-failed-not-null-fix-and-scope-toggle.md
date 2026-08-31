# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Real production commit failure hit live during the 08-22 legacy booking import — `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` |

## 1. Issue / gap identified

Committing the 08-22 export (980 planned rides: 55 completed + 925
cancelled/failed) returned a `502` with `"Internal server error"`.
Root-caused via Supabase's own PostgREST logs (not guessed): `null value in
column "distance_km" of relation "rides" violates not-null constraint`.
Zero rows were written — the whole batch was rejected atomically.

A second, separate item: the operator asked to keep this run's scope to
completed-only bookings, reversing (for this run) the 2026-08-20 decision to
include cancelled/failed bookings. There was no way to do that — the import
always processes both.

## 2. Root cause

**Not** a missing field on one ride dict (the first hypothesis, since
reverted). It's a PostgREST bulk-insert behavior: a single `.insert(rows)`
call derives one column list from the **union** of keys present across every
row in that call, then writes `NULL` — not that row's own schema default —
for any row missing a key that's in that union. `commit_plan()` batched
`rides_to_insert` in fixed 200-row chunks without regard to row shape. The
completed-ride path (regular and the zero-fare-completed anomalous case)
always sets `distance_km` (and 11 other NOT NULL-with-default columns:
`duration_minutes`, `base_fare`, `distance_fare`, `time_fare`, `airport_fee`,
`surge_multiplier`, `total_fare`, `tip_amount`, `payment_method`,
`payment_status`, `driver_earnings`, `admin_earnings`) explicitly. The
cancelled/failed path deliberately never does — an existing, tested design
decision (`test_no_fare_earnings_or_payout_fields_are_written`): those
columns should fall through to the schema's real default, not an app-level
fake zero. Once a completed row and a cancelled/failed row landed in the
same 200-row batch (batch 1 always does, since completed rows are appended
first and only 55 of them exist), every column the completed row set but the
cancelled/failed row omitted got NULL-overridden on the cancelled/failed
row, and PostgREST rejected the entire batch.

A second latent instance of the exact same bug was found alongside it: the
zero-fare-completed anomalous branch was itself missing `payment_method`
(present on the regular completed path), which would have triggered
identically the moment those two shapes shared a batch.

## 3. Fix / remediation

- **`commit_plan()`**: split `rides_to_insert` into two groups by
  `status` (`"completed"` vs. everything else) before chunking into 200-row
  `.insert()` calls, so a PostgREST call's column union is always
  homogeneous. This is the general fix — it holds regardless of batch
  boundaries or future field additions to either shape, and it preserves
  the existing "cancelled/failed rows carry no fare data" design decision
  rather than working around it with fake zeros (an earlier draft of this
  fix did exactly that and was reverted after
  `test_no_fare_earnings_or_payout_fields_are_written` caught it).
- Added `"payment_method": "card"` to the zero-fare-completed branch,
  matching the regular completed path's own historical-default choice.
- **New regression test**
  (`test_commit_never_mixes_completed_and_cancelled_rows_in_one_insert_call`):
  builds a plan with one completed and one cancelled row, calls the real
  `commit_plan()` against a fake Supabase client that records each
  `.insert()` call's row batch, and asserts exactly 2 calls, each
  homogeneous by status. Verified to actually fail without the fix
  (confirmed by reverting the `commit_plan()` change locally and re-running
  the test before restoring it — not just written and trusted).
- **`include_cancelled_failed` scope toggle** (`build_plan()`, the admin
  route, and a new UI checkbox, default `True`): lets one commit be scoped
  to completed-only without touching the standing default for the next.
  This does not reverse the 2026-08-20 decision — it adds a per-run
  operator control on top of it, at the operator's explicit request this
  session (after they reiterated wanting completed-only for this run, given
  the outstanding conflict this session flagged against that documented
  decision).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every `build_plan(`/`commit_plan(`
  call site in the repo — the only caller of
  `booking_import_service.build_plan`/`commit_plan` is
  `routes/admin/booking_import.py` (updated). No CLI script imports this
  module's `build_plan`/`commit_plan` (unlike the driver importer, which has
  `scripts/import_legacy_mongo_drivers.py` — this booking importer has no
  equivalent script).
- **`commit_plan()` batching**: behavior-preserving for a single-shape
  batch (e.g. completed-only, or a re-run after this incident's failed
  attempt) — splitting an all-completed or all-cancelled list by status
  yields one group and the same chunking as before. Only a genuinely mixed
  batch behaves differently, and differently means "succeeds" instead of
  "PostgREST rejects the whole thing."
- **`include_cancelled_failed` default stays `True`** everywhere (service,
  route, UI checkbox) — an existing caller that doesn't pass the new
  parameter/field gets byte-identical behavior to before this change.
- All 94 tests in the three affected backend test files pass, plus the new
  HTTP-level scope test (15/15 in `test_admin_booking_import.py`).

## 5. User-experience effect

- **Internal admin only** (super_admin-gated). Before: committing any batch
  that mixed completed and cancelled/failed rows failed 100% of the time
  with an opaque sanitized 502, after already validating clean. After: the
  commit succeeds, and the operator gets an explicit checkbox to scope a
  given run to completed-only when they want to, with the report/summary
  line reflecting whichever scope was chosen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | `commit_plan()` batches completed vs. cancelled/failed rows separately; zero-fare-completed branch gained `payment_method`; `build_plan()` gained `include_cancelled_failed` param + `skipped_cancelled_failed_excluded_by_scope` stat | Fix the real NOT NULL/batch-column-union failure; add the requested per-run scope control |
| `backend/routes/admin/booking_import.py` | Both routes accept + thread through `include_cancelled_failed: bool = Form(True)` | Expose the scope toggle over HTTP |
| `backend/tests/test_booking_import_cancelled_failed.py` | New regression test proving the batch-separation fix; `_FakeQuery` gained `update()` (recount_drivers' fallback path needs it); `_plan()` helper + 2 new tests for the scope toggle | Lock in both fixes against regression |
| `backend/tests/test_admin_booking_import.py` | New HTTP-level test for the scope toggle | Cover the route wiring, not just the service layer |
| `admin-dashboard/src/lib/api/imports.ts` | `BookingImportOptions.includeCancelledFailed`, `BookingImportCounts.skipped_cancelled_failed_excluded_by_scope` | Type-level support for the new field |
| `admin-dashboard/.../LegacyBookingImport.tsx` | New checkbox (default checked), report summary line reflects the scope | Give the operator the control at the UI |

## 7. Before / after

```python
# Before
for i in range(0, len(plan.rides_to_insert), 200):
    supabase.table("rides").insert(plan.rides_to_insert[i : i + 200]).execute()
```

```python
# After
completed_rows = [r for r in plan.rides_to_insert if r.get("status") == "completed"]
cancelled_failed_rows = [r for r in plan.rides_to_insert if r.get("status") != "completed"]
for rows in (completed_rows, cancelled_failed_rows):
    for i in range(0, len(rows), 200):
        supabase.table("rides").insert(rows[i : i + 200]).execute()
```

## 8. Rollback plan

`git-revert-safe` — no data was ever written under the buggy code path (the
whole point of the bug is that PostgREST rejected it atomically, confirmed
zero rows in production before this fix). A revert restores the exact
pre-incident (broken) state, not a data-integrity risk.

## 9. Verification performed

- [x] Root-caused via real Supabase PostgREST logs (`query_logs`), not
      guessed — confirmed the exact constraint violation and the exact
      failing request.
- [x] `pytest tests/test_booking_import_service.py tests/test_booking_import_cancelled_failed.py tests/test_admin_booking_import.py` — 108 passed (94 + a further 14 in test_admin_booking_import.py already counted, no double count: 94 combined across the first two files, 15 in the third).
- [x] `ruff check` / `ruff format --check` on every changed backend file — clean.
- [x] The new regression test was confirmed to actually fail without the
      fix (reverted `commit_plan()` locally, re-ran, saw `1 == 2`
      assertion failure, restored the fix, re-ran, saw it pass) — not
      written on faith.
- [x] `npx tsc --noEmit` + a real `npm run build` (admin-dashboard) — exit
      0, no errors.
- [x] Blast-radius grep: confirmed `booking_import_service.build_plan`/
      `commit_plan` has exactly one caller in the repo.

## What was NOT verified

- Not yet re-run against real production — this fix has not been exercised
  against the 08-22 export via a live commit yet; that's the operator's
  next step.
- No visual regression tooling exists for admin-dashboard (CLAUDE.md §6) —
  the new checkbox was reasoned about, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`) and safe (no
      prior write to roll back — the bug's own atomicity prevented one)
- [x] Blast radius is stated, not assumed (one route file, one caller)
- [x] The `include_cancelled_failed` default is unchanged (`True`)
      everywhere it's threaded, so no existing caller's behavior moves —
      this is additive, not a silent reversal of the 2026-08-20 decision
