# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend, migrations, admin-dashboard (indirect — data only, no UI file changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | P0-B, `docs/audit/2026-08-11-driver-rider-migration-audit.md`; also surfaced A26 (new CRITICAL finding, filed not fixed) |

## 1. Issue / gap identified

Three admin financial-dashboard surfaces double-count legacy-imported ride
earnings (money the previous app already paid out, re-imported into `rides`
for trip-history continuity with an offsetting `payouts` row so the
driver's *balance* stays correct):

1. `admin_ride_money_rollup` (migration 161) — powers `/admin/stats`,
   `/rides/stats`, `/rides/financials`, `/rides/earnings` — summed every
   completed ride, no legacy exclusion.
2. `admin_payouts_overview_aggregates` (migration 159) — its T4A
   year-to-date snapshot (`t4a_ytd_gross`, `t4a_under_500`…`t4a_over_30k`)
   summed the same unfiltered set, so it can disagree with the actual T4A
   slips `utils/t4a_annual_job.py` issues (which are correct).
3. `/admin/earnings/rides` CSV export (`routes/admin/rides.py`) — the
   per-ride finance-reconciliation export had no legacy exclusion at all; a
   legacy row with no real Stripe charge was indistinguishable from a real
   charge line.

## 2. Root cause

The legacy-exclusion pattern (`utils/legacy_rides.py`) was applied
consistently to every **driver-facing** earnings/statement/T4A surface but
never retrofitted onto these three **admin-facing aggregate** surfaces when
they were built.

**Also found while fixing this (not part of the original P0-B scope):**
`utils/legacy_rides.py`'s `EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata":
None}` compiles to a real SQL `column IS NULL` filter (via
`repositories/_base.py`), but `legacy_import_metadata` on `rides`/`users`/
`drivers` is `NOT NULL DEFAULT '{}'::jsonb` on all three tables — no row can
ever be SQL NULL there, so that predicate is unsatisfiable wherever it's
used as a `get_rows()` filter (as opposed to the safe post-fetch
`drop_legacy_rides()` helper). This is now filed as **A26** in
`ACTION_ITEMS.md` (CRITICAL, open, needs live-DB verification) rather than
fixed broadly in this change — see that entry for the full 9+-call-site
list and why it's out of scope here. This change does not touch any of
those 9+ existing call sites.

## 3. Fix / remediation

- New migration `302_ride_money_rollup_exclude_legacy.sql`: adds
  `legacy_import_metadata = '{}'::jsonb` to `admin_ride_money_rollup`'s
  WHERE clause (unconditional — this function has no payout-pairing
  concept to preserve).
- New migration `303_payouts_overview_ytd_exclude_legacy.sql`: adds the same
  predicate **only** to `admin_payouts_overview_aggregates`'s `ytd` CTE
  (the T4A snapshot). `earned_up_to_end`, `earned_up_to_prev`, and
  `blocked_outstanding` are deliberately left unfiltered — they're paired
  with the offsetting `payouts` rows and are already arithmetically correct
  as-is; filtering only the rides half there would move a driver's reported
  payable balance.
- `routes/admin/rides.py`'s `/earnings/rides` CSV export now calls
  `drop_legacy_rides()` (post-fetch, Python-truthy check) instead of adding
  a server-side `EXCLUDE_LEGACY_RIDES` filter, specifically because that
  constant is the broken pattern described above — using it here would have
  shipped a *second* instance of the same bug into new code, silently
  returning zero rows for this export instead of just excluding legacy
  rows.
- Both new migration predicates (`= '{}'::jsonb`) were verified against live
  psql semantics by an independent review pass: jsonb equality compares
  canonicalized binary form (whitespace/key-order irrelevant), and an empty
  object has no keys to reorder — `legacy_import_metadata = '{}'::jsonb`
  reliably and exclusively matches non-legacy rows.

## 4. Risk & impact on existing functionality

- Blast radius: `admin_ride_money_rollup` has 4 call sites, all in
  `routes/admin/rides.py` (`/stats`, `/rides/stats`, `/rides/financials`,
  `/rides/earnings` period-aggregate); all get the fix consistently since
  it's inside the one shared SQL function. `admin_payouts_overview_aggregates`
  has 1 call site (`/admin/payouts/overview`). `/earnings/rides` CSV export
  is its own isolated endpoint.
- No driver-facing surface is touched by this change at all — the fix is
  entirely on admin/CFO-facing aggregate dashboards.
- Numbers on these three surfaces will change (decrease) for any report
  window containing legacy-imported rides' original `ride_completed_at`
  dates. Per the audit: "no evidence of driver double-payout, wallet fund
  loss, or rider overcharge — every finding here is a reporting/display-layer
  inconsistency, not a money-movement bug," so this is a correction to a
  displayed number, not a change to any ledger/balance.

## 5. User-experience effect

Internal-admin/CFO-facing only. Three dashboard numbers change (get more
accurate, generally lower) for any window containing legacy-imported rides.
Not a mid-session change — these are periodic/on-demand admin reports, not
live state a rider/driver is watching.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/302_ride_money_rollup_exclude_legacy.sql` | New — adds legacy-ride exclusion to `admin_ride_money_rollup` | Fix gross revenue/earnings overstatement |
| `backend/migrations/303_payouts_overview_ytd_exclude_legacy.sql` | New — adds legacy-ride exclusion to the `ytd`/T4A CTE only in `admin_payouts_overview_aggregates` | Fix T4A snapshot disagreeing with actual T4A slips, without disturbing the correctly-paired balance sums |
| `backend/routes/admin/rides.py` | `/earnings/rides` now calls `drop_legacy_rides()` post-fetch, in a loop that widens the raw fetch until `offset + limit` real rows are collected or the source is exhausted (capped by `_EXPORT_MAX_ROWS`) | Exclude legacy rows from the finance reconciliation CSV export without the broken `EXCLUDE_LEGACY_RIDES` server-filter pattern, and without silently under-filling the page/total when legacy rows sort inside a fixed-size fetch window (money-auditor review finding, fixed pre-merge) |
| `backend/tests/test_admin_rides_read_endpoints_coverage.py` | 2 new tests: legacy row present in fetch result is excluded from the response; legacy rows crowding the initial fetch window no longer silently under-fill `total`/page | Regression coverage |
| `ACTION_ITEMS.md` | P0-B closed; new A26 (CRITICAL, open) filed for the `EXCLUDE_LEGACY_RIDES` predicate bug | Track the larger finding surfaced while fixing P0-B |

## 7. Before / after

```sql
-- Before (admin_ride_money_rollup)
FROM rides
WHERE status = 'completed'
  AND ride_completed_at >= p_start
  AND (p_end IS NULL OR ride_completed_at < p_end)
```

```sql
-- After
FROM rides
WHERE status = 'completed'
  AND ride_completed_at >= p_start
  AND (p_end IS NULL OR ride_completed_at < p_end)
  AND legacy_import_metadata = '{}'::jsonb
```

```python
# Before (routes/admin/rides.py, /earnings/rides)
rides = await db_supabase.get_rows("rides", filters, ...)
page = rides[offset : offset + limit]
```

```python
# After
rides = await db_supabase.get_rows("rides", filters, ...)
rides = drop_legacy_rides(rides)   # post-fetch, not a broken server filter
page = rides[offset : offset + limit]
```

## 8. Rollback plan

- Migrations: re-run `161_ride_money_rollup_fn.sql` / `159_payouts_overview_
  aggregates_fn.sql`'s original `CREATE OR REPLACE FUNCTION` body to restore
  the unfiltered versions (documented in each new migration's own rollback
  comment). No new column/index to drop.
- Code: `git revert` — pure logic change, no destructive data mutation.

## 9. Verification performed

- [x] `pytest backend/tests/test_admin_rides_read_endpoints_coverage.py backend/tests/test_admin_rides_coverage.py -q --no-cov` → 128 passed
- [x] Two independent `spinr-migration-reviewer` passes: first caught the
  `IS NULL`-is-unsatisfiable bug before merge (would have zeroed both
  dashboards entirely); second, after the fix, verified the corrected
  `= '{}'::jsonb` predicate against live psql (`SELECT '{}'::jsonb = '{}'::jsonb`
  etc.) — verdict SAFE TO APPLY.
- [x] `spinr-money-auditor` pass on the full diff: migrations 302/303
  verified money-correct (including confirming 303's unfiltered
  earned/outstanding sums are intentional, not an oversight); caught a real
  bug in the CSV-export fix — a fixed-size over-fetch followed by
  post-fetch legacy-row dropping could silently under-fill `total`/the page
  whenever legacy rows sorted inside that fetch window, undercounting the
  very export finance uses to reconcile against Stripe. Fixed pre-merge:
  the fetch now loops, widening until enough real rows are collected or the
  source is exhausted (capped by `_EXPORT_MAX_ROWS`), with a new regression
  test proving the interleaved-legacy-rows case is handled correctly.
- [x] Blast-radius grep for all `admin_ride_money_rollup` /
  `admin_payouts_overview_aggregates` call sites (4 + 1, both in
  `routes/admin/rides.py`)
- [ ] Manual repro in staging — not available in this sandbox (no live
  Supabase access); the audit itself notes actual dollar-magnitude
  verification "requires live DB."

## What was NOT verified

- Not run against a live Supabase/Postgres instance — the corrected
  predicate was verified via an independent live-psql equality check
  (`SELECT '{}'::jsonb = '{}'::jsonb`), not by applying the migration itself
  against production or staging data.
- No visual regression tooling exists for admin-dashboard; this change has
  no frontend diff (the dashboards read these numbers via existing API
  responses, unchanged shape) so nothing to screenshot.
- A26 (the `EXCLUDE_LEGACY_RIDES` predicate bug) is filed, not fixed or
  live-verified — see `ACTION_ITEMS.md` for what's needed before anyone
  touches its 9+ call sites.
