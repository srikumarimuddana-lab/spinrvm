# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (agent) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers / dispatch / safety (regulatory) |
| PR / commit link | branch `claude/driver-license-work-auth-edit-n748bk` |
| Related issue or gap ID | Operator question: "what state is a driver in when they delete their account, and how do we tell SGI they stopped working?" |

## 1. Issue / gap identified

Driver account deletion (`DELETE /users/account`, called from `driver-app/app/driver/settings.tsx`) wrote `drivers.deleted_at` and nothing else. Investigating that surfaced four distinct gaps:

1. **A deleted driver stayed dispatchable.** There is no `deleted` value in the driver status set, so a tombstoned row kept `status='active'`, `is_online=true`, `is_available=true`. `dispatch_service.find_candidate_drivers` filters exactly those four columns and reads `drivers` directly — it never sees the users row's `status='pending_deletion'`.
2. **The insurance period never closed.** A driver who deleted while online left an open Period 1 row in `driver_insurance_periods`, the append-only 7-year SGI/insurance audit trail.
3. **They still counted as Active** in the admin drivers list, the driver stat counts, and the compliance driver-roster report that goes to Knight Archer / regulator consumers.
4. **Nothing told the regulator.** SGI keeps listing a driver as an active passenger-for-hire driver until the D00032 "remove" row is filed (and their vehicle until D00033). The forms exist and support `action=remove`, but nothing triggered, recorded, or surfaced the filing.

Separately, `settings.tsx` tells drivers deletion can be rejected for "active ride, unsettled balance, pending payout" and renders the backend's reason — **no such check existed**.

## 2. Root cause

1–3 share one root cause: **`deleted_at` is a column only some read paths respect.** `get_driver_by_id` / `get_driver_by_user_id_cached` filter `deleted_at IS NULL`; the generic `get_rows` does not, and every bulk read (dispatch, admin list, stats, roster) goes through `get_rows`. Soft-delete was introduced as a per-row-lookup concept and never propagated to the set-based queries. Two mitigations existed but neither is a correct primary path: the Redis presence filter (explicitly **fail-open** — a Redis outage or empty presence set falls back to the raw DB query) and the `stale_intent` reconciler loop (**4-hour** default, and it is a reconciler for unreachable apps, not for a driver who explicitly left).

4 is a missing feature rather than a bug: the SGI form generator was built as an on-demand tool with no queue or audit trail, and its `effective_date` was hardcoded to `_today_iso()` — right for an add/change, wrong for a removal filed days after the fact.

The deletion-guard gap is a client/server contract drift: the guard was described in the app's comment and error handling but never implemented server-side.

## 3. Fix / remediation

Four scoped commits:

- **Take the driver row out of service on deletion.** A shared `_tombstone_driver_row()` (used by both `/users/account` and `/users/profile`) clears `is_online`/`is_available`, stamps `went_offline_at`, closes the insurance period (Period 0), evicts both driver cache keys, and queues the regulator removal. Dispatch independently filters `deleted_at IS NULL`.
- **Enforce the deletion guards.** `_assert_deletable()` returns 409 with an actionable reason for an active ride on either side, a pending payout, or a positive payable balance — before any write.
- **Stop counting deleted drivers as active.** Roster report excludes them by default (with `include_deleted` for auditing); admin stats give them a `deleted` bucket; the list and slideout badge them Deleted and force the presence badge Offline.
- **SGI removal queue + audit.** Migration 275 adds `regulator_removal_required`, `regulator_removal_effective_date`, per-form `regulator_removal_driver_form_at` / `regulator_removal_vehicle_form_at`, and `regulator_removal_reported_by`. Deletion queues the removal for drivers who were actually filed; generating a `remove` form stamps the matching per-form column; `GET /data-transfer/sgi-forms/removal-queue` surfaces the backlog and the SGI Forms tab renders it with a one-click "generate removal forms" action.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, and it reaches dispatch — the highest-consequence path in the product.** Enumerated by grepping every `get_rows("drivers"` / `deleted_at` / `record_period_transition` / `regulator_removal` consumer:

| Changed thing | Every other reader/writer | Effect |
|---|---|---|
| `dispatch_service.find_candidate_drivers` filter | `routes/rides/matching.py` (primary path), the cascade branch, `test_dispatch_*` suites | One added predicate; the four pre-existing gates are unchanged. **The risk that matters is the inverse of the bug**: if `deleted_at` were mis-compiled to `= NULL` instead of `is.null` it would match zero rows and take the entire fleet out of dispatch. `repositories/_base.py::_apply_filters` maps a `None` value to `.is_(k, "null")` — pinned by a dedicated test asserting the filter value is `None`, plus the two pre-existing exact-dict assertions in `tests/services/test_dispatch_service.py` which were updated. |
| `drivers.is_online` / `is_available` written on deletion | `routes/drivers/status.py` (go online/offline), `stale_intent_reconciler`, `spinr_pass`, `set_driver_available`, `claim_driver_atomic`, admin status/action routes | Writing `false` on deletion cannot violate the `is_available ⇒ is_online` invariant (both go false together). The stale-intent reconciler's atomic claim re-asserts `is_online: True`, so it simply never matches a deleted row — no double-write, no fight. |
| `record_period_transition(driver_id, 0)` | `routes/drivers/status.py`, `stale_intent_reconciler`, `spinr_pass`, `corporate_member_offboarding_service`, `corporate_suspension_service`, `routes/admin/rides.py` | The RPC is idempotent by design (partial unique index on one open row per driver; a repeat returns `status="noop"`). Non-raising by contract, so a failure cannot block the deletion. |
| `_driver_roster_rows` | Only `get_driver_roster` | Behaviour change to an outgoing report — see UX below. |
| Driver stats `status` classification | `admin-dashboard` drivers page tabs (`statusCounts`), `admin_get_drivers`'s own `status` query filter | The `deleted` classification is derived **for display/counting only**; the stored `status` column is untouched, so the list endpoint's `status=` filter and every status-transition route are unaffected. |
| `sgi_field_maps` effective date | `routes/admin/sgi_forms.py` only | `add`/`change` still stamp today — only `remove` is backdated. |
| Migration 275 columns | New; only the three call sites added here | Purely additive. |

Regression risks and how they are handled:

- **Deletion now returns 409 where it always returned 200.** This is the intended fix, but it is a new failure mode on a live-tested flow. The app already handles it (`getApiErrorMessage` renders the backend's detail), and the gate runs before any write, so a rejected deletion leaves the account byte-identical.
- **`_assert_deletable` calls `get_driver_balance`, which reads up to 10k rides + 5k payouts + 10k bonuses.** Acceptable: deletion is once-per-account, not a hot path, and it is not on any SLA path in `CLAUDE.md`. Reusing it rather than reimplementing the Decimal math is deliberate — a second implementation would drift from the number the driver sees in the app.
- **Migration 275 backfills existing soft-deleted, regulator-approved drivers into the queue.** Intentional: those are exactly the rows SGI still lists, and starting the queue empty would leave the real backlog invisible. It is an `UPDATE` over a small set (soft-deleted drivers only) guarded by `regulator_removal_required = FALSE`, so it is re-runnable and cannot double-apply.
- **Background loops**: none added, none modified.
- **Money**: no fare, wallet, Stripe, or payout code path changed. `_assert_deletable` only *reads* a balance.

## 5. User-experience effect

- **Driver (driver-app)**: deletion can now be **rejected**. A driver mid-ride, with a pending payout, or holding a positive balance sees an actionable message ("You have $42.50 in unpaid earnings. Please withdraw them before deleting your account.") instead of silently losing access to money they were owed. No app-side code change was needed — the app already displayed the backend's reason. Not visible mid-ride to anyone not attempting deletion.
- **Rider**: no change. One indirect improvement: a deleted driver can no longer be dispatched during a Redis outage.
- **Internal admin**: deleted drivers now badge "Deleted" (grey) in the drivers list and slideout, always read "Offline", carry an "Account Deleted" date in the detail panel, and land in a new `deleted` stat bucket instead of inflating Active. They deliberately remain **in** the list — an admin still has to find them to file the SGI removal. The SGI Forms tab gains an amber queue panel listing drivers who left but are still filed, with a one-click "Generate removal forms for N".
- **Outgoing report (insurer/regulator)**: the driver-roster report **no longer includes deleted drivers by default**. This changes what an external party receives, so the page subtitle now states "excluding deleted accounts" (or "including") explicitly rather than leaving the reader to guess what the count covers.
- Drivers CSV export gains an `Account Deleted At` column.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/users.py` | `_tombstone_driver_row()` + `_assert_deletable()`; both deletion endpoints call them | Take the row out of service; enforce the promised guards |
| `backend/services/dispatch_service.py` | `deleted_at: None` in the candidate filter | Durable guard independent of the intent flags |
| `backend/routes/admin/compliance.py` | `_driver_roster_rows` excludes deleted by default, `include_deleted` param, renders `deleted` status, subtitle states the scope | Stop describing departed drivers as onboarded to an insurer |
| `backend/routes/admin/drivers.py` | `deleted` status classification + count in stats; `account_deleted` on list rows | Stop counting them as Active |
| `backend/routes/admin/rides.py` | `deleted_at` in the drivers CSV export | Same, for the export |
| `backend/routes/admin/sgi_forms.py` | Per-form removal stamping; `GET /removal-queue` | Record and surface the filing |
| `backend/services/data_transfer/sgi_field_maps.py` | `_effective_date()`; removals use the driver's stop date | SGI needs when they stopped, not when the form was made |
| `backend/migrations/275_drivers_regulator_removal_queue.sql` | Removal-queue columns + partial index + backfill | Storage for the queue and its audit trail |
| `backend/tests/test_driver_deletion_tombstone.py` | New — 17 cases | Tombstone writes, period close, cache eviction, guards, dispatch filter |
| `backend/tests/test_sgi_field_maps.py` | New — 8 cases | Removal effective-date + row-shape guard |
| `backend/tests/test_sgi_forms_route.py` | +8 cases | Per-form stamping, stamp-failure tolerance, queue filtering |
| `backend/tests/test_compliance_reports_http.py` | +3 cases, 1 updated | Roster exclusion |
| `backend/tests/services/test_dispatch_service.py` | 2 exact-dict assertions updated | New filter key |
| `admin-dashboard/src/lib/api/data-transfer.ts`, `src/lib/api.ts` | `getSgiRemovalQueue` + types | Client for the queue |
| `admin-dashboard/.../data-transfer/SgiFormsTab.tsx` | Queue panel + one-click removal generation; `runGenerate` takes forms explicitly | Surface the backlog |
| `admin-dashboard/.../drivers/page.tsx` | Deleted badge, forced-Offline presence, Account Deleted field, export column | Distinguish deleted drivers |

## 7. Before / after

**Dispatch candidate query (`backend/services/dispatch_service.py`)**

```python
# Before — four gates, none of which a deleted driver fails.
driver_filter = {
    "is_online": True, "is_available": True, "is_verified": True,
    "status": "active", "vehicle_type_id": ride["vehicle_type_id"],
}
```

```python
# After — `None` compiles to PostgREST `is.null` via _apply_filters.
driver_filter = {
    "is_online": True, "is_available": True, "is_verified": True,
    "status": "active", "deleted_at": None,
    "vehicle_type_id": ride["vehicle_type_id"],
}
```

**Deletion write (`backend/routes/users.py`)**

```python
# Before — the tombstone, and nothing else. status/is_online/is_available
# keep their pre-deletion values; the open insurance period stays open.
await db_supabase.update_one("drivers", {"user_id": user_id}, {"deleted_at": now})
```

```python
# After (via _tombstone_driver_row)
updates = {"deleted_at": now, "is_online": False, "is_available": False, "went_offline_at": now}
if driver.get("is_verified") or driver.get("regulatory_authority_approved"):
    updates["regulator_removal_required"] = True
    updates["regulator_removal_effective_date"] = now[:10]
await db_supabase.update_one("drivers", {"id": driver["id"]}, updates)
await db_supabase.invalidate_driver_cache(driver_id=driver["id"], user_id=user_id)
await record_period_transition(driver["id"], 0)
```

**SGI removal effective date (`backend/services/data_transfer/sgi_field_maps.py`)**

```python
# Before — a removal filed three weeks late told SGI the driver stopped today.
"effective_date": _today_iso(),
```

```python
# After
"effective_date": _effective_date(driver, action),   # remove -> the date they stopped
```

## 8. Rollback plan

Rollback differs per commit; the first three need no data step, the fourth does.

- **Deletion hardening / guards / admin-visibility**: `git revert` + redeploy backend and admin-dashboard. No schema change, no irreversible data change. Rows already tombstoned keep `is_online=false` — which is the correct state either way, and is exactly what the stale-intent reconciler would have written anyway.
- **Migration 275**: the down-migration is in the file's top comment, per `backend/migrations/CLAUDE.md`:
  ```sql
  ALTER TABLE public.drivers
    DROP COLUMN IF EXISTS regulator_removal_required,
    DROP COLUMN IF EXISTS regulator_removal_effective_date,
    DROP COLUMN IF EXISTS regulator_removal_driver_form_at,
    DROP COLUMN IF EXISTS regulator_removal_vehicle_form_at,
    DROP COLUMN IF EXISTS regulator_removal_reported_by;
  DROP INDEX IF EXISTS idx_drivers_regulator_removal_pending;
  ```
  Dropping the columns loses the record of which removals were filed. If the queue has been used before a rollback, export it first (`GET /data-transfer/sgi-forms/removal-queue?include_filed=true`) — the SGI submissions themselves are external and cannot be undone by a revert.
- **Reverting the code but keeping the migration is safe** and is the preferred order: the columns are additive and unread by the old code.
- **Not feature-flagged.** The deletion-state fix and the dispatch filter are correctness fixes on a regulatory/insurance surface where the pre-fix behaviour is the thing to be avoided — shipping them dark would mean deliberately leaving deleted drivers dispatchable. The admin/queue UI is internal-only. Per gate 3 the flag requirement targets user-visible, non-trivial changes and shared components used by 3+ pages; the only user-visible change is the deletion 409, which is behaviour the client already implemented.

## 9. Verification performed

- [x] **Automated tests** — 36 new/updated cases across 5 files. Targeted run of the new suites: 44 passed. Wider run `pytest -m "not slow" -k "sgi or deletion or driver or admin or compliance or data_transfer"`: **1547 passed, 1 skipped, 0 failed**.
- [x] **State-machine / money dry run (gate 4)** — every new test runs against `mock_supabase_client`-style fixtures. Concrete before/after scenarios are in §7. Insurance-period transitions are asserted directly (`period.assert_awaited_once_with("drv-1", 0)`), and the guard's balance rejection is asserted against a concrete `$42.50` and a negative-balance non-rejection.
- [x] **Real production build run** — `npm run build` (`next build`) in `admin-dashboard`, exit 0. Not `tsc --noEmit` alone (which was also run and is clean for every changed file).
- [x] `ruff check` + `ruff format --check` clean on all changed backend files. (`ruff check tests/` reports 10 pre-existing findings in unrelated test files — none in the files touched here.)
- [x] **Blast-radius grep performed (gate 1)** — searched `get_rows("drivers"`, `deleted_at`, `record_period_transition`, `is_online`/`is_available` writers, `regulator_removal*`, `spinr_approved`, `_driver_roster_rows`, `sgi_field_maps` consumers across `backend/`, `admin-dashboard/`, `driver-app/`, `docs/`, and `backend/migrations/`. Every consumer is enumerated in §4.
- [x] **Additive-over-destructive (gate 2)** — new columns rather than repurposing `drivers.status`; the `deleted` classification is derived for display and never written to the status column, so no migration + dual-read window is needed and no in-flight session observes a changed column meaning.
- [x] **Migration conventions** — next free prefix (274; highest was 273), rollback SQL in the top comment, additive/nullable columns. Reviewed by the `spinr-migration-reviewer` agent, which returned **three blockers, all valid and all fixed**:
  1. `CREATE INDEX` was not `CONCURRENTLY` on `drivers`, a hot table — a plain build takes a SHARE lock blocking every write for the duration. Now `CONCURRENTLY`, matching migration 55.
  2. The partial index predicate (`… AND (driver_form_at IS NULL OR vehicle_form_at IS NULL)`) was **not implied by** the query `sgi_removal_queue` actually issues (which filters on `regulator_removal_required` alone and decides outstanding-ness in Python, because `include_filed=true` deliberately wants filed rows). Postgres could never have used it, so the endpoint would sequential-scan `drivers`. Predicate narrowed to the one column the query filters on.
  3. The header comment claimed "No backfill of existing soft-deleted rows" while the SQL below it backfilled exactly those. The comment was stale from an earlier draft; corrected, since it is the text an on-call engineer reads to judge whether a re-run or rollback is safe.
- [x] **Migration apply simulated, not assumed** — reimplemented `scripts/migrate.py::_apply_migration_autocommit`'s splitter and ran migration 275 through it. **This caught a defect the review did not**: because the file contains `CONCURRENTLY`, the runner splits it on semicolons, and two *mid-line* semicolons inside prose comments were producing bogus statements (`a plain build blocks every write to it…`) that Postgres would have rejected — after the `ALTER TABLE` had already committed under autocommit. Comments reworded; the simulation now yields exactly 8 clean statements. Pinned by `tests/test_migration_concurrently_splitting.py`.
- [x] **CLAUDE.md conventions reviewed** — insurance Period 0–3 table (deletion is a Period 1→0 transition, append-only, non-raising); "do not silently swallow errors" (the SGI stamp failure logs at `error` and is explained inline; the queue's unresolvable-entity count is logged at `error` rather than dropped); PIPEDA (no new PII in logs — the removal queue returns name/plate to an authed admin only, same as the existing forms flow); dual-import pattern honoured in `routes/users.py`.
- [ ] **Manual repro in staging** — not performed.

## 9b. Merge with `main` (2026-08-02)

The branch was 102 commits behind `main` and had one real conflict plus one silent collision:

- **Conflict — `backend/routes/admin/compliance.py`.** `main` (#3130) removed report emailing wholesale: `_require_spinr_ca`, `_deliver_report`, the `send_transactional_email` import, and the `email_to` query param on every Compliance endpoint. This branch had edited `get_driver_roster`'s signature and docstring in the same region. Resolved by keeping the `include_deleted` work and dropping the `email_to` plumbing to match `main` — the two changes are orthogonal, so nothing was lost on either side. Verified no stale `_require_spinr_ca` / `_deliver_report` / `email_to` / `emailDriverRoster` references remain anywhere in the backend, tests, or the admin API client.
- **Silent collision — migration number.** `main` merged `274_data_transfer_export_jobs_admin_id_no_fk.sql` while this branch held `274_drivers_regulator_removal_queue.sql`. Git auto-merges this without complaint (two different filenames), but it violates the prefix-uniqueness rule and would have failed the CI check. Per `backend/migrations/CLAUDE.md` the second one renames: **this migration is now `275_drivers_regulator_removal_queue.sql`**. Renaming is safe precisely because it has not been applied anywhere — the runner keys idempotency on the full filename, so an already-applied migration could not have been renamed. References updated in this log and in `ACTION_ITEMS.md`; the earlier commit message still says 274.

Post-merge verification: **2336 backend tests pass, 0 failures** (`-k "sgi or deletion or driver or admin or compliance or migration or dispatch or users"`), and a real `npm run build` on `admin-dashboard` exits 0. Note the pre-existing `test_two_drivers_accepting_same_ride_one_wins` failure reported in §9 no longer reproduces — `main` fixed it in the interim.

## 10. What was NOT verified

- **Nothing was exercised against a live Supabase.** All DB access is mocked. In particular, the `deleted_at IS NULL` predicate reaching PostgREST as `is.null` is verified at the filter-dict layer and by reading `_apply_filters`, **not** by an actual query against a real table. This is the single highest-consequence line in the change — if it compiled wrong, dispatch would return zero drivers fleet-wide — so it should be smoke-tested against staging (book one ride, confirm a driver is offered) before this reaches production.
- **Migration 275 has not been applied anywhere.** The backfill's row count in production is unknown; it should be previewed with `python scripts/migrate.py --dry-run` and the `UPDATE`'s effect sanity-checked against the real count of soft-deleted approved drivers before applying.
- **No staging click-through of the admin UI.** The SGI Forms queue panel and the drivers-list Deleted badge were verified by a passing production build and typecheck, not by opening the page. **There is no automated visual/snapshot regression tooling for `admin-dashboard`**, so the layout impact of the new amber queue panel was reasoned about, not screenshotted — a standing repo-wide gap.
- **No end-to-end SGI submission.** The generated D00032/D00033 PDFs were not opened or submitted; the tests assert the row-dict values and the DB stamping, and `fill_driver_details_form` is mocked in the route tests. Whether SGI accepts a backdated `effective_date` on a removal is an assumption based on the form's semantics, **not confirmed with SGI**.
- **Known pre-existing gap left in place**: `driver_to_vehicle_details_row` may emit VIN ciphertext on D00033 — `_decrypt_driver_pii` only covers `license_number`. Already flagged in that function's docstring; not touched here, and it now affects removal forms as well as adds.
- **`verified_at` staleness (carried over from the previous change on this branch)** — `/drivers/{id}/verify` and `/drivers/{id}/status` still write `is_verified` without stamping `verified_at`. Not addressed here.
- **The deletion guard's balance check trusts `get_driver_balance`.** If that function is wrong (e.g. the `driver_bonuses` table is missing and it 503s), deletion fails closed with a 503 rather than a 409. That is the safe direction, but it means a bonuses-table outage blocks account deletion entirely — not tested against that scenario.

- **Pre-existing runner defect found, deliberately NOT fixed here — 34 merged migrations would fail on a fresh apply.** Simulating the migration runner (see §9) exposed that `scripts/migrate.py::apply_migration` routes a file to the no-transaction `split(";")` path whenever the string `CONCURRENTLY` appears **anywhere in its text, including a comment**. That splitter breaks on (a) a mid-line semicolon in a prose comment and (b) any `$$`-quoted function body — 34 already-merged migrations hit one or the other, including `55_drivers_dispatch_partial_index.sql` and `196_wallet_apply_credit.sql` (which contains no concurrent index at all and is routed there purely by the word appearing in its rollback comment). It has not bitten because those migrations are recorded as applied and never re-run; it bites on the next fresh-database apply — a new staging project, a DR rebuild, a new province's Supabase project — failing partway through after earlier statements have already committed under autocommit.

  Not fixed here per CLAUDE.md gate 9: correcting it means rewriting the statement splitter that governs how *every* future migration applies, which should not happen as a side effect of an unrelated change. Frozen as `_KNOWN_UNSPLITTABLE` in `tests/test_migration_concurrently_splitting.py` (so the debt is visible in code and the test stays green rather than becoming a permanently-red gate — gate 8) and filed as **ACTION_ITEMS.md B0** with the approach and acceptance criteria. Migration 275 itself is clean and verified.
