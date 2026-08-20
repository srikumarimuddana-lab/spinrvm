# Change Impact & Risk Log — Historical `duration_estimated` marker backfill (dry-run-only script)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (backend agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | local worktree branch `worktree-agent-a4c94f6a2ef307b21` (fast-forwarded from `claude/spinr-mongodb-migration-u9y6iz`) — not pushed |
| Related issue or gap ID | `docs/change-log/2026-08-19-legacy-migration-transparency-backend.md` §10, "Historical `duration_minutes` backfill is explicitly deferred, not done" — the follow-up flagged there |

**This entry is unusual: nothing here was applied to any database, live or otherwise.** The deliverable is a dry-run-only CLI backfill script plus its plan/apply service functions and tests. `--apply` was never passed to the script this session, against any environment. See §9/§10 below and the explicit instruction this task was given: never run `--apply`.

## 1. Issue / gap identified

The 2026-08-19 backend track fixed `booking_import_service.build_plan()` so every *future* legacy booking import stamps `legacy_import_metadata.duration_estimated = true/false` on the row it writes, based on whether the source row had a `start_ride_at` timestamp. That fix is import-code-path-only: the ~186 rides already committed by the original 2026-07-29 import (and any other legacy import that ran before this fix existed) have no such marker. A consumer of `duration_minutes` on those older rows (e.g. the driver Activity screen's "Total Duration" stat, or any future analytics that sums duration across legacy rides) cannot tell an estimated duration apart from a measured one for those specific rows, even though it now can for anything imported after 2026-08-19.

## 2. Root cause

`legacy_import_metadata.duration_estimated` is written only at `INSERT` time, inside `build_plan()`'s per-row loop. It was never a column with a default or a computed/generated expression — it is a plain key inside a JSONB blob assembled once per row at import time. Adding the key to the code that writes *new* rows does nothing to rows that were written before the code changed; JSONB values are not retroactively recomputed by a later code deploy. Fixing already-committed rows requires a separate, explicit write to those rows — which is exactly the kind of already-live-data write this repo's CLAUDE.md requires extra caution (dry-run-default, explicit `--apply`, write-time guard) around, and which the original fix's own "What was NOT verified / deferred" section explicitly called out as a separate decision rather than something to do silently as a side effect.

## 3. Fix / remediation

Built, but **did not run**, a dry-run-by-default CLI backfill mirroring `backfill_legacy_driver_sin_dob.py`'s discipline exactly:

- `backend/services/booking_import_service.py` gained `plan_duration_estimated_backfill()` / `apply_duration_estimated_backfill()` / `print_duration_estimated_backfill_report()`, plus the `DurationEstimatedBackfillPlan`/`DurationEstimatedBackfillItem` dataclasses and a `DURATION_ESTIMATED_BACKFILL_MARKER` constant.
- `backend/scripts/backfill_legacy_ride_duration_estimated.py` — a thin CLI wrapper, dry-run by default, `--apply` required to write, printing a full report (counts, skips, errors) before any write is even attempted.
- `backend/tests/test_legacy_duration_estimated_backfill_service.py` — 12 unit tests against a local fake Supabase client (no real DB).

**Where the plan/apply pair lives, and why:** extended `booking_import_service.py` itself, rather than creating a new module. Three reasons: (1) the detection condition this backfill needs — "does this row have `ride_started_at`?" — is not a new piece of domain knowledge; it is the *exact same condition* `build_plan()` already encodes (`duration_estimated = not bool(started_at)`), so keeping both in one file means a future change to that condition (unlikely, but possible) can't silently drift between the importer and the backfill because they're plainly next to each other in the same file. (2) `IMPORT_SOURCE` (the constant identifying which rides this importer wrote) already lives in this module and both the fetch-already-imported logic and this backfill's fetch need it — a new module would either duplicate the constant or import it, adding indirection for no isolation benefit. (3) This mirrors the established precedent in this exact codebase: `driver_import_service.py` already contains its own "legacy SIN/DOB backfill" as a clearly-delineated, separately-commented section appended after its main `build_plan`/`commit_plan` pair (see that file's `# ─── Legacy SIN + date-of-birth backfill ───` banner comment) rather than being split into its own file. This backfill follows that same in-file-section convention, banner comment included.

## 4. Risk & impact on existing functionality

**Blast radius of the code added this session: isolated.** `plan_duration_estimated_backfill`, `apply_duration_estimated_backfill`, `DurationEstimatedBackfillPlan`, `DurationEstimatedBackfillItem`, `DURATION_ESTIMATED_BACKFILL_MARKER`, and `_fetch_legacy_rides_for_duration_backfill` are all brand-new names with no prior callers anywhere in the codebase (grepped). The only caller of the new plan/apply functions is the new CLI script and the new test file. Nothing in `build_plan()`/`commit_plan()` (the live importer path) was touched or reordered — this is a pure addition appended after `print_report()`.

**What else reads/writes `rides.legacy_import_metadata`** (grepped every `.py` reference, excluding tests):

- `services/data_transfer/entity_import_service.py`, `services/rider_import_service.py`, `services/driver_import_service.py`, `services/stripe_mapping_import_service.py` — different importers, different tables (`drivers`/`users`), not `rides`.
- `services/legacy_payout_correction_service.py` — reads `legacy_import_metadata->>old_booking_id` on `rides`, read-only, one specific key.
- `services/legacy_gst_backfill_service.py` — **also writes** `rides.legacy_import_metadata` (adds `old_payout_gst_amount`), via the same read-merge-write pattern this new backfill uses. See "concurrent-writer risk" below.
- `utils/legacy_rides.py` — `EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata": {"$eq": {}}}` does a whole-dict equality check against `{}`. Unaffected: this backfill only ever touches rows that already have a non-empty dict (`source`, `old_booking_id`, etc. already present — the same conclusion the original 2026-08-19 entry reached for the importer-side fix, and it still holds here since the backfill never clears or replaces the dict, only merges two new keys in).
- `routes/rides/rating.py`, `routes/rides/payments.py`, `routes/drivers/payouts.py`, `routes/drivers/status.py`, `routes/promotions.py`, `utils/dual_run_monitor.py`, `utils/decal_pdf.py` — all check truthiness/presence of the dict as a whole, or read one specific, differently-named key (`rider_csv_import`, `dual_run_hold`-adjacent checks, etc.). None assume a closed key set; a new key added alongside existing ones is invisible to all of them.
- `routes/admin/rides.py`, `scripts/backfill_imported_ride_snapshots.py`, `scripts/backfill_imported_ride_routes.py` — filter on `legacy_import_metadata IS NOT NULL` / `!= '{}'`, unaffected by adding keys to an already-non-empty dict.
- `routes/admin/users.py` — projects `legacy_import_metadata` in a couple of column lists (admin-facing rider/ride display); a new key becomes visible there once stamped, same as the importer-side fix already made true for new rows — no admin-dashboard rendering exists for it yet (unchanged from the prior entry's finding, still out of scope here).

**Concurrent-writer risk (new finding, worth naming explicitly since it did not exist before this backfill script did):** `legacy_gst_backfill_service.py` is a *different* manual CLI backfill that also does read-current-row → merge a new key into `legacy_import_metadata` → write, on the same `rides` rows this backfill targets. Both scripts are single-operator, manually-invoked CLI tools — neither is a background loop, and nothing in `backend/core/lifespan.py`'s 18 loops touches this column. If both scripts were ever run with `--apply` at literally the same time against the same rows, there is a narrow lost-update window: each script's write-time guard (`.filter("legacy_import_metadata->>duration_estimated", "is", "null")` here; an analogous "field not yet present" check there) only protects against *its own* key being clobbered, not the other script's key being lost if both read-merge-write the same row within the same round-trip window. This is a pre-existing risk pattern with `legacy_gst_backfill_service.py` (also never `--apply`'d in production per that file's own documentation), not something this session introduced into a previously-safe column — but it is a real, stated risk of two independently-authored one-off scripts sharing a JSONB blob rather than each owning a dedicated column. Mitigation is operational, not code: whoever runs either script with `--apply` should not run both concurrently against overlapping rows. Not fixed here — flagging it is the correct scope for a script that is never actually run this session; a code-level fix (e.g. a Postgres-side JSONB merge function) would be over-engineering for two manual, sequentially-run one-off tools and is not proposed.

**Cross-cutting:** does not touch ride state transitions (`status` is never read or written by this backfill), wallet/allowance deltas, dispatch, Stripe flows, or `duration_minutes` itself. No background loop reads or writes any of the touched names.

## 5. User-experience effect

**None — this script was never run.** Even if it were, this is exactly the same read-path-only, internal/admin-facing shape as the original importer-side fix: no rider, driver, or corporate-admin-facing surface renders `legacy_import_metadata.duration_estimated` today (admin-dashboard rendering remains a separately-tracked, out-of-scope frontend gap, same as before). Not visible mid-session to anyone using the rider/driver apps.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | Added `DurationEstimatedBackfillPlan`/`DurationEstimatedBackfillItem` dataclasses, `DURATION_ESTIMATED_BACKFILL_MARKER` constant, `_fetch_legacy_rides_for_duration_backfill`, `plan_duration_estimated_backfill`, `apply_duration_estimated_backfill`, `print_duration_estimated_backfill_report` | Plan/apply pair for the historical marker backfill, in-file section mirroring `driver_import_service.py`'s legacy SIN/DOB backfill convention |
| `backend/scripts/backfill_legacy_ride_duration_estimated.py` | New file — dry-run-by-default CLI wrapper, `--apply` required to write | The follow-up script itself |
| `backend/tests/test_legacy_duration_estimated_backfill_service.py` | New file — 12 unit tests | Plan correctness, apply correctness, never-clobber write-time guard, idempotency across repeated dry runs and after a successful apply, already-marked skip case |

## 7. Before / after

Pure additive code — no existing function's behavior changed. `build_plan()`/`commit_plan()` (the live importer) are byte-for-byte unchanged by this session's commits; nothing here is a behavior-changing diff to already-shipped code, so there is no meaningful before/after snippet to show for existing behavior. The new functions' own "before" state is "did not exist."

## 8. Rollback plan

**Nothing to roll back — this script was never applied.** If it is applied in the future by someone else and needs reverting: every updated ride's `id` is printed by the CLI at apply time (see the script's own docstring, "Rollback for an applied run"). Reverting means, per printed id, removing exactly the `duration_estimated` and `legacy_duration_estimated_backfill` keys from that row's `legacy_import_metadata` (leaving every other key, including any `old_payout_gst_amount` key `legacy_gst_backfill_service.py` may have separately added, untouched). No cascading state — no fare recompute, no payout, no Stripe call, no WebSocket event — is triggered by this write in either direction, and `duration_minutes` itself is never written by this script in the first place, so there is nothing to revert there. A plain `git revert` of this session's commits is sufficient to remove the *script and its plan/apply functions* themselves (they have no runtime footprint until someone invokes the CLI with `--apply`).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_legacy_duration_estimated_backfill_service.py -q --no-cov` — **12 passed**. Also ran together with the pre-existing `test_booking_import_service.py` (40 tests) and `test_legacy_sin_dob_import_service.py` (22 tests) to confirm no regression: **74 passed, 0 failed**.
- [x] `ruff check` run on all three touched/created files (`backend/services/booking_import_service.py`, `backend/scripts/backfill_legacy_ride_duration_estimated.py`, `backend/tests/test_legacy_duration_estimated_backfill_service.py`) — clean, no findings.
- [x] `python3 -m py_compile` on the service module and the CLI script — clean.
- [x] `python3 backend/scripts/backfill_legacy_ride_duration_estimated.py --help` run — argparse output renders correctly and the process exits before importing the Supabase client (confirms the CLI's argument wiring is correct without touching any network or database).
- [x] Blast-radius grep performed for every reader/writer of `rides.legacy_import_metadata` (listed in full in §4 above), including a second manual backfill script (`legacy_gst_backfill_service.py`) discovered to write the same JSONB column by a different read-merge-write path.
- [x] Reviewed against relevant CLAUDE.md conventions: dry-run-by-default + explicit `--apply` (mirrors `backfill_legacy_driver_sin_dob.py`), additive-over-destructive (new JSONB keys only, no column, no migration), write-time guard rather than plan-time-snapshot-only (the exact bug class the SIN/DOB backfill was fixed for earlier this session), do-not-silently-swallow-errors (plan/apply both surface a missing-id defensive error rather than crashing or skipping silently), PIPEDA (reports/logs carry only ride ids — internal UUIDs — and counts, never addresses/names/any other ride PII).
- [ ] **`--apply` was never run, against any environment — mocked, staging, or production.** This is by explicit instruction for this task, not an oversight. No real Supabase connection was ever made by this session in service of this backfill.
- [ ] Feature-flagged: not applicable — this is an offline CLI operator tool, not a live-tested in-app flow; there is no runtime code path a flag would gate.

## 10. What was NOT verified / deferred

- **The backfill was never run against staging or production, with or without `--apply`.** All verification is against a local, in-memory fake Supabase client (`backend/tests/test_legacy_duration_estimated_backfill_service.py`'s own fake, mirroring `test_legacy_sin_dob_import_service.py`'s harness) — no live Supabase access this session, per this repo's existing test-tier convention and per this task's explicit instruction to never run `--apply` against anything.
- **No `npm run build`** — this session touched backend Python only.
- **The rollout decision — who runs `--apply`, when, and against which environment (staging first? straight to production? both Railway and Fly, or does it matter since this only touches Supabase directly?) — is explicitly NOT this session's decision.** This script is deliberately built to the point of "ready to run" and no further: the actual production run needs its own sign-off (an ops/admin person with production Supabase credentials, presumably after a staging dry-run confirms the reported counts match expectations), same as `backfill_legacy_driver_sin_dob.py` has never been run in production per that script's own documentation. Whoever runs it should re-verify the row counts this backfill's dry-run report shows against a fresh read of the 186-legacy-ride population before trusting the plan output blindly, since time has passed between this session and whenever `--apply` is eventually considered (more legacy rides may have been imported in the interim, e.g. via the Oct 30 final cutover, in which case most/all of the newer ones will already carry the marker from the importer-side fix and simply show up as "already marked, skipped").
- **The concurrent-writer risk with `legacy_gst_backfill_service.py`** (§4) is named and reasoned about, not tested against an actual concurrent-run scenario — there is no automated test simulating two scripts racing on the same row, since neither script has ever run against live data to race in the first place.
