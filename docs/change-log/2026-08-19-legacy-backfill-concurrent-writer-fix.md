# Change Impact & Risk Log — Concurrent-writer hardening for the `rides.legacy_import_metadata` backfills

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (backend agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | local worktree branch `worktree-agent-a22d8e949dce3ddc5` (fast-forwarded from `claude/spinr-mongodb-migration-u9y6iz`) — not pushed |
| Related issue or gap ID | `docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md` §4, "Concurrent-writer risk (new finding...)" — the follow-up flagged there |

**This entry is code-level hardening only — nothing here was applied to any database, live or otherwise.** `apply_duration_estimated_backfill` (and the CLI script that calls it, `backfill_legacy_ride_duration_estimated.py`) remain dry-run-by-default and were never invoked with `--apply` against any environment this session. `legacy_gst_backfill_service.py` still has no write/commit path at all (unchanged by this session — see §3 below).

## 1. Issue / gap identified

Two independent, manually-invoked CLI backfill scripts both read-merge-write the same `rides.legacy_import_metadata` JSONB column: `booking_import_service.apply_duration_estimated_backfill` (adds a `duration_estimated` key, dry-run-only so far) and `legacy_gst_backfill_service.py` (plans an `old_payout_gst_amount` key, but — confirmed by re-reading the file in full this session — has **no commit/apply function at all**; its own docstring calls this out explicitly: "No commit path... Inserting the actual UPDATE is a separate, later step"). If a commit path is ever added to `legacy_gst_backfill_service.py` and both scripts are ever run with `--apply` close together (or literally concurrently) against overlapping rows, the existing write-time guard in `apply_duration_estimated_backfill` would not have caught it: it only re-checks *its own* key (`duration_estimated`) for a null-guard before writing, then blindly overwrites the **entire** `legacy_import_metadata` column with a locally-merged dict built from an earlier read. Any key some *other* writer added to that same column in between this function's read and its write would be silently dropped — a real lost-update bug, not yet exploitable in practice only because the second writer (GST) doesn't have a write path yet.

## 2. Root cause

`apply_duration_estimated_backfill`'s guard was designed to protect against **another run of itself** (its own docstring: "a concurrent run of this same script"), not against an *arbitrary* second writer to the same JSONB blob. The pattern is: read current `legacy_import_metadata` → merge two new keys into a local Python dict → write the whole dict back, guarded only by `legacy_import_metadata->>duration_estimated IS NULL`. That guard is necessary but not sufficient: it proves nobody else set `duration_estimated` between read and write, but says nothing about any *other* key. Since the write always sends the full column value (not a partial JSONB patch), a second writer's freshly-added key that isn't present in this function's stale local snapshot gets overwritten out of existence the moment this function's write succeeds — even though, from this function's own narrow point of view, "nothing changed" (its own key really was still null).

## 3. Fix / remediation

Added a second, whole-column optimistic-concurrency guard to `apply_duration_estimated_backfill`: alongside the existing `.filter("legacy_import_metadata->>duration_estimated", "is", "null")`, the update now also carries `.filter("legacy_import_metadata", "eq", json.dumps(<the exact dict just read>, sort_keys=True, default=str))`. The write only succeeds if the column's current value at write time is byte-for-byte (value-)equal to what this function read moments earlier — i.e. nothing else, including a hypothetical future `legacy_gst_backfill_service.py` apply path, touched *any* key on that row in between. A mismatch is reported as a conflict through the exact same path the existing guard already uses (0 rows updated → `conflicts.append(item.id)`), never silently dropped, and is safe to retry on the next run since `plan_duration_estimated_backfill` always re-reads current state.

**Options considered and why this one was chosen:**
- **Postgres advisory lock (`pg_advisory_lock`/`pg_try_advisory_lock`)** — the task's own preferred option, but genuinely doesn't fit this codebase's patterns and was rejected: `backend/supabase_client.py` only exposes the `supabase-py`/PostgREST REST client, not a raw psycopg connection (confirmed by reading it and grepping for `psycopg`/`pg_advisory` across `backend/` — the only direct-Postgres connection in this repo is `run_migrations.py`'s, used for exactly this reason per `CLAUDE.md`). PostgREST issues each `.execute()` as an independent HTTP request against a pooled connection, so a session-level advisory lock taken in one request cannot reliably still be held by the time a later request (the write) runs. Wrapping the lock in a transaction-scoped RPC function (`pg_advisory_xact_lock`) would require a new Postgres function — i.e. a migration — which the task scoped this fix to avoid.
- **New lock table / other heavier mechanism** — rejected per the task's own instruction not to invent a third mechanism once the first two don't cleanly apply.
- **Chosen: whole-column optimistic-concurrency guard**, applied to the one function that actually writes today. This is the fallback the task described (narrower per-row write-time guard), adapted to the codebase's actual state: `legacy_gst_backfill_service.py` has no write path to harden yet, so the fix hardens `apply_duration_estimated_backfill` to be safe against *any* future second writer (not presuming which one), and documents the required pattern in `legacy_gst_backfill_service.py`'s own docstring for whoever adds its commit path later.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `apply_duration_estimated_backfill`.** Grepped every caller of this function (unchanged from the prior entry's finding): only `backend/scripts/backfill_legacy_ride_duration_estimated.py` (the CLI wrapper, never run with `--apply` against any environment) and `backend/tests/test_legacy_duration_estimated_backfill_service.py` (the test file). No other module calls it. `plan_duration_estimated_backfill`, `build_plan`/`commit_plan` (the live importer path), and `legacy_gst_backfill_service.py`'s existing `build_backfill_plan`/`print_report` (both read-only, unchanged) are untouched by this diff.

**What else reads/writes `rides.legacy_import_metadata`** — same list as the prior entry (§4 of `2026-08-19-legacy-duration-estimated-backfill.md`), re-verified unaffected here since this fix adds an additional filter to an existing update call site rather than changing what gets written or who calls it: `services/data_transfer/entity_import_service.py`, `services/rider_import_service.py`, `services/driver_import_service.py`, `services/stripe_mapping_import_service.py` (different tables), `services/legacy_payout_correction_service.py` (read-only), `services/legacy_gst_backfill_service.py` (still read-only — no commit path added here), `utils/legacy_rides.py`, `routes/rides/rating.py`, `routes/rides/payments.py`, `routes/drivers/payouts.py`, `routes/drivers/status.py`, `routes/promotions.py`, `utils/dual_run_monitor.py`, `utils/decal_pdf.py`, `routes/admin/rides.py`, `routes/admin/users.py`, `scripts/backfill_imported_ride_snapshots.py`, `scripts/backfill_imported_ride_routes.py`. None of these are affected: they either don't touch this column, only read it, or write a different table entirely.

**Behavior when run alone (no concurrent writer) is unchanged.** The new filter only adds a *stricter* precondition on the same update call — if nothing else touches the row between this function's read and write (the normal case, and the only case exercised by the pre-existing 12 tests, all of which still pass unmodified), the column's live value at write time is by definition identical to what was just read, so the new filter always matches and the write proceeds exactly as before. It can only ever turn a write that previously *would have silently clobbered another writer's key* into a reported conflict — it cannot cause a write that previously succeeded to now fail in a single-writer scenario, and it cannot cause a new write to happen that wasn't already being attempted.

**No interaction with ride state transitions, dispatch, wallet/allowance deltas, or Stripe flows.** This backfill never reads or writes `rides.status`, `duration_minutes`, or any money field. No background loop (`backend/core/lifespan.py`) touches this code path.

## 5. User-experience effect

None. Both backfills are offline CLI operator tools with no runtime code path reachable from the rider, driver, or admin apps. `legacy_gst_backfill_service.py`'s docstring note is not user-facing documentation. Not visible mid-session to anyone using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | `apply_duration_estimated_backfill` gained a second `.filter("legacy_import_metadata", "eq", <json snapshot>)` optimistic-concurrency guard alongside the existing `duration_estimated IS NULL` guard; added a banner-comment note above the backfill section explaining the concurrent-writer risk and the guard's purpose; added `import json` | Close the lost-update race: protect the whole column, not just this backfill's own key |
| `backend/services/legacy_gst_backfill_service.py` | Added a docstring section, "Concurrent-writer requirement for whoever adds the commit path," specifying the same whole-column guard pattern must be used once this file grows a write path, and recording why an advisory lock was rejected | No commit path exists here yet (confirmed by re-reading the file) — nothing to harden directly, so the requirement is documented for the future author instead |
| `backend/tests/test_legacy_duration_estimated_backfill_service.py` | Added `test_apply_does_not_clobber_an_unrelated_key_added_by_a_concurrent_writer`; extended the fake Supabase harness with a one-shot `on_next_select` hook (to simulate a write landing in the real read-then-write race window) and whole-column JSONB equality filter support; SELECT results are now `copy.deepcopy`'d to match real PostgREST's snapshot-not-live-reference semantics | Regression coverage for the fix; without the deep-copy and hook, the fake couldn't distinguish "race before apply() starts" from "race inside apply()'s own read/write window," which is the actual bug being fixed |

## 7. Before / after

```python
# Before — guards only this function's own key; blindly overwrites the
# whole column with a stale local snapshot
existing = supabase.table("rides").select("legacy_import_metadata").eq("id", item.id).execute().data
meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
if "duration_estimated" in meta or DURATION_ESTIMATED_BACKFILL_MARKER in meta:
    conflicts.append(item.id)
    continue

meta["duration_estimated"] = item.duration_estimated
meta[DURATION_ESTIMATED_BACKFILL_MARKER] = {"batch": batch, "backfilled_at": now_iso}

res = (
    supabase.table("rides")
    .update({"legacy_import_metadata": meta, "updated_at": now_iso})
    .eq("id", item.id)
    .filter("legacy_import_metadata->>duration_estimated", "is", "null")
    .execute()
)
```

```python
# After — same read and same merge, but the write also carries a
# whole-column snapshot-equality guard so ANY concurrent writer is caught,
# not just another run of this same backfill
existing = supabase.table("rides").select("legacy_import_metadata").eq("id", item.id).execute().data
read_meta = dict((existing[0].get("legacy_import_metadata") or {}) if existing else {})
if "duration_estimated" in read_meta or DURATION_ESTIMATED_BACKFILL_MARKER in read_meta:
    conflicts.append(item.id)
    continue

meta = dict(read_meta)
meta["duration_estimated"] = item.duration_estimated
meta[DURATION_ESTIMATED_BACKFILL_MARKER] = {"batch": batch, "backfilled_at": now_iso}

res = (
    supabase.table("rides")
    .update({"legacy_import_metadata": meta, "updated_at": now_iso})
    .eq("id", item.id)
    .filter("legacy_import_metadata->>duration_estimated", "is", "null")
    .filter("legacy_import_metadata", "eq", json.dumps(read_meta, sort_keys=True, default=str))
    .execute()
)
```

## 8. Rollback plan

**Nothing to roll back against live data — neither backfill has ever been applied.** If this code needs reverting: a plain `git revert` of this session's commit is sufficient. It only tightens a write guard on a function that has never run with `--apply`; reverting it returns to the prior (weaker but still functioning) single-key guard, with no data-level cleanup required in either direction.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_legacy_duration_estimated_backfill_service.py -q --no-cov` — **13 passed** (12 pre-existing + 1 new). Also ran together with `test_booking_import_service.py` (40 tests), `test_legacy_sin_dob_import_service.py` (22 tests), and `test_legacy_gst_backfill_service.py` (4 tests) to confirm no regression: **79 passed, 0 failed**.
- [x] `ruff check` run on all three touched files (`backend/services/booking_import_service.py`, `backend/services/legacy_gst_backfill_service.py`, `backend/tests/test_legacy_duration_estimated_backfill_service.py`) — clean, no findings.
- [x] Blast-radius grep performed for every caller of `apply_duration_estimated_backfill` (two: the CLI script and the test file) and every reader/writer of `rides.legacy_import_metadata` (listed in full in §4).
- [x] Confirmed `legacy_gst_backfill_service.py` has no commit/apply function by reading the file in full (only `build_backfill_plan`/`print_report` exist) and grepping the repo for any other caller of `build_backfill_plan` or a hypothetical apply function — none found.
- [x] Confirmed no raw-SQL/psycopg access is available to these scripts (grepped `backend/` for `psycopg`/`pg_advisory`; only `run_migrations.py`/`verify_restore.py`/test fixtures use `psycopg`, none of which these backfill scripts import), supporting the decision to reject the advisory-lock approach.
- [x] Reviewed against relevant `CLAUDE.md` conventions: additive-over-destructive (a stricter filter, no new write, no schema change), do-not-silently-swallow-errors (a guard mismatch is reported as a conflict, never dropped), PIPEDA (no PII in the new test/docstring content — ride ids and counts only).
- [ ] **`--apply` was never run, against any environment — mocked, staging, or production**, for either backfill script, this session or any prior one covered by this fix.
- [ ] Feature-flagged: not applicable — offline CLI operator tooling, no runtime path a flag would gate.

## 10. What was NOT verified / deferred

- **Not tested against a real Supabase/PostgREST instance.** The new `.filter("legacy_import_metadata", "eq", <json>)` guard's wire-level correctness rests on PostgREST's documented behavior of casting a JSON-literal filter value against a `jsonb` column for equality (the same mechanism `postgrest-py`'s `contains`/`contained_by` filters already rely on via `json.dumps()` in this same client library — see `postgrest/base_request_builder.py`). This session verified the pattern only against a local fake Supabase client built to mirror PostgREST semantics as understood from reading the client library source; it was not exercised against a live Supabase project.
- **The underlying rollout decision — if/when `legacy_gst_backfill_service.py` gets a commit path, and if/when either backfill is ever run with `--apply` — remains explicitly out of scope for this fix**, same as the prior entry. See `docs/runbooks/legacy-backfill-scripts-rollout.md` (new, this session) for the recommended procedure once product-owner sign-off is obtained.
- **No live concurrent-run scenario was exercised** (e.g. two real script invocations racing against a real database) — only the simulated race in the new unit test, run against the fake client.
