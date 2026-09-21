# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Author | Claude (spinr-migration-reviewer finding, applied same session) |
| Surface(s) | backend (ops script, not a request-path route) |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5508 |
| Related issue or gap ID | Found by spinr-migration-reviewer's first run under its 2026-09-19 widened scope (legacy backfill/import scripts) |

## 1. Issue / gap identified

`backend/scripts/backfill_imported_ride_routes.py` (backfills OSRM road
distance + route polyline for the 224 imported legacy rides) has never
successfully updated a single ride since it was written. Every `--apply` run
would report "Updated 0/224 imported rides."

## 2. Root cause

The update loop called `db_supabase.update_one("rides", r["id"], update_data)`
— passing the bare ride-id string as the `filters` argument. `update_one`'s
real signature is `update_one(table, filters: Dict, update: Dict)`; its
sibling script `backfill_imported_ride_snapshots.py` correctly passes
`{"id": ride_id}`. `repositories/_base.py`'s `_apply_filters` explicitly
raises `TypeError` on a non-dict filter (added specifically to catch this
mistake class instead of surfacing a supabase-py `AttributeError`), so every
call in this script threw, was caught by the loop's own broad
`except Exception`, logged at `error` level per-row, and the run finished
reporting a 0/224 tally.

## 3. Fix / remediation

One-line fix: `update_one("rides", {"id": r["id"]}, update_data)`. Added a
dedicated test file (none existed for this script before) that pins the
call shape and confirms the fix — verified failing against the pre-fix code
and passing against the fix.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — a manually-invoked ops script, not reachable
  from any request path, background loop, or scheduled job. Grepped for
  other callers/importers of this script: none (it's a standalone CLI tool
  per its own docstring, `python scripts/backfill_imported_ride_routes.py`).
- No interaction with the ride state machine, dispatch, money, or insurance
  periods — it only ever writes `rides.distance_km` and
  `rides.planned_route_polyline` on rows matching
  `legacy_import_metadata != '{}'` (the 224 already-imported legacy rides,
  not live/active rides).
- Because the script has never actually written anything (100% failure
  rate before this fix), there is no risk of this fix changing data that
  something else already depends on being in its current (unset) state —
  this only makes a previously-inert script start doing what it always
  claimed to do.
- The fix does not change the script's OSRM-fetch logic, dedup-by-coordinate
  caching, dry-run branch, or the route-polyline `[[lat,lng],...]` shape
  documented at the top of the update loop (migration 313's format
  requirement) — only the malformed `update_one` call.

## 5. User-experience effect

None immediately — this script is not invoked automatically, so nothing
changes until an operator runs `--apply`. Once run, admin/rider ride-detail
map views for the 224 imported legacy rides would start showing an
OSRM-derived road route/distance instead of whatever placeholder value (or
absence) currently exists on those rows — a one-time, operator-triggered
data-quality improvement, not a live-session-visible change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/backfill_imported_ride_routes.py` | `update_one("rides", r["id"], ...)` → `update_one("rides", {"id": r["id"]}, ...)` | Match the real `update_one(table, filters: Dict, update: Dict)` signature |
| `backend/tests/test_backfill_imported_ride_routes.py` (new) | 3 tests: dict-filter regression, failed-update-is-counted, dry-run-never-writes | No test existed for this script; the missing coverage is exactly why the bug shipped and stayed unnoticed |

## 7. Before / after

```python
# Before
await db_supabase.update_one("rides", r["id"], update_data)
```

```python
# After
await db_supabase.update_one("rides", {"id": r["id"]}, update_data)
```

## 8. Rollback plan

Plain `git revert` — this is a standalone, manually-invoked script with no
automatic trigger. If the fix is reverted, the script simply returns to its
previous (already-shipped, already-inert) no-op behavior; no data has been
written under the old code that would need separate remediation.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_backfill_imported_ride_routes.py` (3 passed)
- [x] Confirmed the key regression test fails against the pre-fix code (`git stash` the fix, re-run — failed asserting `'r1' == {'id': 'r1'}`) and passes with the fix restored
- [x] `ruff check` clean on both changed/added files
- [ ] Manual repro steps followed in staging — **not verified against real OSRM/Supabase**; this is a mocked unit test only. The script itself has never been run against production per its own history (0/224 every time), so there is no live behavior to compare against; an operator should still dry-run (`--dry-run`, the default-safe path per the script's own flag) against staging before a real `--apply`
- [x] Blast-radius grep performed: no other callers/importers of this script found
- [x] Reviewed against relevant CLAUDE.md convention: this is the exact `_apply_filters` non-dict-filter foot-gun the DB layer's own type check exists to catch — confirms the layer's defensive check is working as designed, not a gap in it
- [ ] Not feature-flagged — not applicable; this is an operator-invoked CLI script, not a request-path feature

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no live data was ever written under the buggy version)
- [x] Blast radius is stated: isolated to one standalone, non-automated ops script
- [x] No silent behavior change to an already-shipped flow — the script was already shipped but inert (0% success); this fix makes it functional as originally intended, not a change to working behavior
