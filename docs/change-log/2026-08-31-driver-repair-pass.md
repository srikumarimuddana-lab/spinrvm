# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), owner-directed follow-up |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin (writes touch rides/payments/safety data) |
| PR / commit link | see branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | owner follow-up to PR #4815/#4821 — "the 'repair pass' that re-checks unmatched driver/rider phones against current data" |

## 1. Issue / gap identified

Migration Data Quality Scan (step 17) flags completed legacy-imported rides missing a
`driver_id` (`missing_driver`), but has no way to fix them — it's additive-only by design. A ride's
driver may exist in `drivers` today even though it didn't at the ride's own import time (added in
a later driver-import batch, or linked/enriched afterward), so a subset of these are genuinely
repairable from current data without needing the raw Mongo export again. Separately, the owner
asked for the equivalent on the rider side; no equivalent is possible from Supabase data alone (see
§2 and the new runbook for why) — that half of the ask is a hard scope boundary, not an
implementation gap, and had to be surfaced rather than silently attempted.

## 2. Root cause

`booking_import_service.py` matches both rider and driver by phone at import time, with no
fallback creation — an unmatched party imports with a NULL link, permanently, unless something
re-checks later. It stores `old_driver_id` on every ride's `legacy_import_metadata` (driver side)
but never captured an equivalent `old_customer_id` linkage anywhere on `users` (rider side) —
confirmed via production query: the only top-level `legacy_import_metadata` keys ever written to
`users` are `mongo_driver_history` and `rider_csv_import`, neither a customer-id linkage. The
`legacy_id_crosswalk` table (migration 328) exists specifically to solve this but has never been
backfilled (0 rows, `ACTION_ITEMS.md` A34).

## 3. Fix / remediation

- **New service**: `backend/services/migration_driver_repair_service.py` — re-matches
  `missing_driver` rides carrying an `old_driver_id` against the CURRENT `drivers` table (both
  linkage shapes: top-level `legacy_import_metadata.old_driver_id` and nested
  `mongo_driver_history[].old_driver_id`). An old id claimed by more than one current driver is
  excluded as ambiguous, never guessed at.
- **Not metadata-only**: unlike the Data Quality Scan, commit sets `rides.driver_id` AND (a)
  reconstructs the ride's Period 2/3 `driver_insurance_periods` rows (mirrors
  `booking_import_service._plan_insurance_periods` exactly — these rows never existed for a
  `missing_driver` ride, since the original call requires a driver to be present) and (b) writes one
  offsetting `payouts` row per driver, reusing `booking_import_service.payout_id_for`/
  `recount_drivers` rather than reimplementing the idempotency scheme, so the newly-linked ride's
  `driver_earnings` doesn't silently inflate that driver's live `payable_balance` for a trip already
  settled in the old app.
- **New admin route**: `backend/routes/admin/migration_driver_repair.py` — Preview→Apply,
  super_admin only, same rate-limit tiers as the other bulk-write tools on this page.
- **New frontend component**: `DriverRepairPass.tsx` — same confirm-phrase gate as Legacy Wallet
  Import / Pre-Launch Data Flagging (type `REPAIR` to enable commit), since this writes across
  three tables, not one metadata key.
- **Step 18** added to the Migration Checklist (`migration_status_service.py`) and
  `migration-tool-order.md`.
- **Explicitly NOT built**: a rider-side equivalent. No `users` row anywhere stores an old-system
  customer id to re-match against, and the crosswalk table built for exactly this is empty. Documented
  in the new `docs/runbooks/migration-driver-rider-repair-scope.md`, including what would unblock it
  (backfilling `legacy_id_crosswalk`, or re-supplying `customers.csv`) — not silently attempted or
  overclaimed as "done."

## 4. Risk & impact on existing functionality

- **Blast radius**: `rides.driver_id` is read by every driver-facing ride-history view, the admin
  Rides page, `spinr_rides_state_transition_total` is NOT incremented by this (no status change —
  see below), and driver aggregate stats (`drivers.total_rides`, `payable_balance`). Grepped for
  other writers of `rides.driver_id`: normal dispatch (`matching.py`, live rides only — a completed
  ride is terminal and never re-enters dispatch, so no collision), the Data Quality Scan (never
  writes `driver_id`, metadata only), and this new tool. No other importer sets `driver_id` on an
  already-completed row.
- **`driver_insurance_periods` is append-only** (regulatory 7-year retention) — this tool only
  INSERTs, never mutates or deletes existing rows, consistent with every other writer of that table.
- **`payouts` table**: the offsetting row uses the SAME idempotency key
  (`payout_id_for(batch, driver_id)`) and same `payout_type='legacy_import'` marker the original
  import uses, so it's indistinguishable from — and never conflicts with — the payouts the original
  `booking_import_service` run already wrote for other drivers in different batches.
- **`rides.status` is never touched** — this only fills in a null FK, not a state transition. No
  `spinr_rides_state_transition_total` implication, no insurance-period-classification-of-a-live-ride
  concern (these are historical, already-completed rides).
- **Concurrency**: apply's write is guarded by `.is_("driver_id", "null")` — a ride linked by any
  other writer between plan and apply is skipped (reported as a conflict), never overwritten.
- **Money impact is intentionally net-zero per repaired ride** — the offsetting payout is sized to
  exactly cancel the newly-linked `driver_earnings`, matching the exact mechanism the original
  import already uses for every other matched driver. This was the highest-risk part of the design;
  see §7 for the before/after.

## 5. User-experience effect

- Internal admin only (super_admin-gated page, Bulk Operations → Final review phase). Visible on
  next page load: a new "Driver-repair pass" card, and Step 18 in the Migration Checklist.
- Rider/driver-facing: a driver whose ride gets repaired will see that trip appear in their own
  trip history/earnings for the first time (it was invisible to them before, since `driver_id` was
  null) — this is the intended fix, not an unintended side effect, and their `payable_balance` does
  not change (net-zero by design, §4).
- Not mid-session-visible to any active rider/driver — these are historical completed rides, not
  live trips.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/migration_driver_repair_service.py` | New | Core re-match + repair logic |
| `backend/tests/test_migration_driver_repair_service.py` | New (16 tests) | Service-level coverage: matching (both linkage shapes, ambiguous exclusion), apply (driver_id set, insurance periods, offsetting payout, idempotency, concurrency guard, total_rides recount) |
| `backend/routes/admin/migration_driver_repair.py` | New | Preview/commit HTTP endpoints |
| `backend/tests/test_admin_migration_driver_repair.py` | New (6 tests) | HTTP-layer: super-admin boundary, preview is read-only, commit end-to-end, idempotent re-run |
| `backend/routes/admin/__init__.py` | Mounted the new router with `require_super_admin` | Matches every other bulk-write tool's mount pattern |
| `backend/utils/rate_limiter.py` | Added `driver_repair_preview_limit`/`driver_repair_commit_limit` | Same tiers as the other money/regulatory-touching commit paths |
| `backend/services/migration_status_service.py` | Added `_tool_18_driver_repair()`, wired into `get_migration_status()` | Step 18 on the Migration Checklist |
| `backend/tests/test_migration_status_service.py` | Updated tool-count assertions (17→18), added 3 step-18 tests | Keep the full-report shape test accurate |
| `admin-dashboard/src/lib/api/imports.ts` | Added driver-repair API client functions/types | Matches the tool's two new backend endpoints |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new functions/types | Barrel-file convention this codebase uses |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx` | New | Preview→Apply UI, confirm-phrase gate |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Wired `DriverRepairPass` into Phase 6 | Chronological placement (needs the full population every prior phase produced) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx` | "17 tools" → "18 tools" copy | Step 18 now exists |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Added `Wrench` to the `lucide-react` mock | New icon on the bulk-operations page's smoke-tested route; same class of fix as the earlier `Circle` icon miss |
| `docs/runbooks/migration-tool-order.md` | Added step 18 row | Keep the canonical order doc current |
| `docs/runbooks/migration-driver-rider-repair-scope.md` | New | Population-count validation walkthrough + honest scope explanation for why there is no rider-side repair pass yet |

## 7. Before / after

```python
# Before: a missing_driver ride stays permanently unrepairable once flagged
# by the Data Quality Scan -- no write path exists.
ride = {"id": "r1", "status": "completed", "driver_id": None,
        "driver_earnings": 25.00,
        "legacy_import_metadata": {"old_driver_id": "old-d1"}}
# driver "old-d1" gets imported in a LATER batch -> nothing re-checks this ride.
```

```python
# After: build_driver_repair_plan() finds the now-existing driver via
# old_driver_id; apply_driver_repair() links it AND keeps the driver's
# payable_balance net-zero for this ride:
ride["driver_id"] = "driver-1"                      # 1. link
# driver_insurance_periods: +1 Period 2 row, +1 Period 3 row (is_reconstructed=True)
# payouts: +1 row {driver_id: "driver-1", amount: "25.00", status: "completed",
#                  payout_type: "legacy_import"}      # 3. net-zero offset
# drivers.total_rides recounted for driver-1
```

## 8. Rollback plan

Not a `git revert` situation once committed to production data (this writes to `rides`,
`driver_insurance_periods`, and `payouts` — real rows, not a feature flag). Rollback path:

1. **Before any commit is run**: `git revert` is sufficient — the tool has never written anything.
2. **After a commit, if a repair batch is found to be wrong**: every write from one commit call
   carries the same `batch` value (in `rides.legacy_import_metadata.driver_repair.batch`, and as the
   suffix of every `payouts.id` for that run via `payout_id_for`). A targeted SQL rollback is
   possible: `UPDATE rides SET driver_id = NULL WHERE legacy_import_metadata->'driver_repair'->>'batch' = '<batch>'`,
   `DELETE FROM payouts WHERE id LIKE 'legacy-import-<batch>-%'`, and
   `DELETE FROM driver_insurance_periods WHERE ride_id IN (<affected ride ids>) AND is_reconstructed = true`,
   then re-run `recount_drivers` for the affected driver ids. Not automated — this is a rare-repair
   tool, not a high-frequency one, and a manual SQL rollback scoped to one `batch` value is safer than
   building an unexercised automated undo path for a write this consequential.
3. **Feature-level rollback**: remove the router mount in `routes/admin/__init__.py` to take the tool
   offline immediately without touching any already-repaired row.

## 9. Verification performed

- [x] Automated tests: `ruff check`/`ruff format --check` clean on all changed backend files. 22 new
      backend tests (16 service + 6 route) plus 3 new + 2 updated `migration_status_service` tests, all
      passing (`pytest tests/test_migration_driver_repair_service.py
      tests/test_admin_migration_driver_repair.py tests/test_migration_status_service.py
      tests/test_admin_migration_status.py tests/test_migration_data_quality_service.py
      tests/test_admin_migration_data_quality.py -q` → 71 passed). `npx tsc --noEmit` clean.
      **Real production build performed**: `npm run build` (not just `next dev` or `tsc --noEmit`) —
      succeeded, `/dashboard/bulk-operations` listed as a built route. Full admin-dashboard vitest
      suite: 561/561 passing (including the `Wrench` icon mock fix, which the smoke test caught).
- [ ] `npm run lint` (ESLint): **could not run** — crashes in this sandbox with
      `TypeError: contextOrFilename.getFilename is not a function` inside `eslint-plugin-react`'s
      React-version auto-detection, reproducing on an unrelated file (`.storybook/main.ts`) after a
      clean `npm ci`, confirming this is a pre-existing environment/dependency issue, not something
      this diff introduced. `tsc --noEmit` + the real production build + the full vitest suite were
      used as the substitute verification per CLAUDE.md's CI-red-root-cause discipline (a check red
      for a reason unrelated to the diff is a signal the gate has decayed, not this PR's problem to
      force through). Flagging for a follow-up fix rather than leaving it silently unexplained.
- [x] Blast-radius grep performed: confirmed no other writer sets `rides.driver_id` on an
      already-`completed` row (§4); confirmed `driver_insurance_periods` writes here are
      insert-only, matching every other writer of that table.
- [x] Money-path dry run: exercised via `mock_supabase_client`-style fakes — 3 dedicated tests cover
      "offsetting payout sized to earnings", "sums across multiple rides for the same driver", "zero
      -earnings ride writes no payout" — with a concrete before/after in §7.
- [ ] Manual repro against live staging: not available to this session (no live Supabase write
      credentials, same limitation as every other importer built this migration effort).
- [ ] Feature-flagged: not flagged. Considered and rejected — this tool is already gated behind
      super_admin auth + a type-to-confirm phrase + an idempotent Preview→Apply split, which is the
      established pattern for every other bulk-write tool on this page (none of them use
      `app_settings` flags either); adding a redundant flag on top wouldn't change who can reach it.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (§8) — batch-scoped, not a blanket undo
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change without the UX field filled in (§5)
- [x] Escalated instead of silently shipping a partial answer: the rider-side half of the original
      ask ("driver/rider phones") is explicitly out of scope, with the reason and the unblocking path
      documented rather than either skipped without explanation or half-attempted against data that
      doesn't support it.

## What was NOT verified

- No screenshot/visual check — no browser access in this session; relied on the real production
  build, `tsc --noEmit`, and the full vitest suite (561/561, including a smoke test that caught the
  missing `Wrench` icon mock) instead. No active visual-regression tooling exists for
  admin-dashboard (`ACTION_ITEMS.md` B38, baselines not yet seeded).
- The tool's live behavior (Preview/Commit against real production `rides`/`drivers`/`payouts`) was
  tested at the service/route level with fakes, not end-to-end against live Supabase — no live
  backend write credentials in this session.
- The 910/1,938 population counts this doc's Part 1 addresses were validated via the *methodology*
  (re-running each importer's own dry-run, cross-checking the Checklist panel's live computation),
  not by an actual re-upload of the source CSVs or a direct file diff — no raw Mongo export file was
  supplied to this session to diff against.
