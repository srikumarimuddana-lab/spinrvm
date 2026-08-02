# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #08 |

## 1. Issue / gap identified

The scheduled-ride dispatcher's per-tick candidate query (`check_scheduled_rides()`)
sorts by `scheduled_time` with no supporting index, and silently defers anything
past a flat 100-row cap with no signal that it happened.

## 2. Root cause

`idx_rides_scheduled (is_scheduled, status)` covers the filter but not the
`ORDER BY scheduled_time`. At current volume the filtered set is small enough
that the unindexed sort is cheap, so this was never a correctness bug — just a
scale ceiling with no instrumentation in front of it.

## 3. Fix / remediation

1. New migration `276_rides_scheduled_time_index.sql` adds a plain btree index
   on `rides.scheduled_time`.
2. `check_scheduled_rides()` now logs a warning and increments
   `spinr_dispatch_scheduled_candidates_capped_total` whenever a tick returns
   the full `_SCHEDULED_RIDES_TICK_LIMIT` (100) rows, so hitting the cap is
   visible instead of silent.

## 4. Risk & impact on existing functionality

- **Index add**: `CREATE INDEX IF NOT EXISTS`, additive, no table rewrite,
  safe against in-flight traffic per the migration's own rollback note. No
  other query path was found reading `rides.scheduled_time` outside this
  dispatcher and the admin rides list/export (`backend/routes/admin/rides.py`),
  both of which only benefit from the new index.
- **Cap-alert add**: touches only `check_scheduled_rides()` in
  `backend/utils/scheduled_rides.py` — no change to the claim, dispatch, or
  reminder logic, no change to what gets dispatched or when. Blast radius:
  isolated to this one function, observability-only.
- No interaction with money, the ride state machine's transition rules, or
  any other of the 17 background loops.

## 5. User-experience effect

None. Backend-only; no rider/driver/admin-visible behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/276_rides_scheduled_time_index.sql` | New migration, adds `idx_rides_scheduled_time` | Support the dispatcher's `ORDER BY scheduled_time` as volume grows |
| `backend/utils/scheduled_rides.py` | Named the tick-limit constant; log + metric when a tick hits it | Make a previously-silent scale ceiling visible |

## 7. Before / after

```python
# Before
scheduled = await db.get_rows(
    "rides", {...}, limit=100, order="scheduled_time", ...
)
except Exception as e:
    ...
    return

for ride in scheduled:
```

```python
# After
scheduled = await db.get_rows(
    "rides", {...}, limit=_SCHEDULED_RIDES_TICK_LIMIT, order="scheduled_time", ...
)
except Exception as e:
    ...
    return

if len(scheduled) >= _SCHEDULED_RIDES_TICK_LIMIT:
    logger.warning(...)
    _metric_inc("spinr_dispatch_scheduled_candidates_capped_total")

for ride in scheduled:
```

## 8. Rollback plan

- Metric/log addition: revert the commit — no data was written, nothing to
  unwind.
- Index: `DROP INDEX IF EXISTS idx_rides_scheduled_time;` (stated in the
  migration's own rollback comment). Safe at any time; nothing depends on the
  index existing for correctness, only for query cost.

## 9. Verification performed

- [x] `python3 -m ast` parse check + `ruff check` on the modified file (clean)
- [ ] Manual repro steps followed in staging — **not performed**; no staging
      environment access from this session
- [x] Blast-radius grep performed — searched for other readers of
      `rides.scheduled_time` and other callers of `check_scheduled_rides`;
      none found beyond the admin rides list/export (index-only benefit, no
      behavior change there)
- [x] Reviewed against relevant CLAUDE.md convention (background-loop replay
      safety: unaffected, no claim/write logic touched; migration index rule:
      satisfied)
- [x] Not user-visible — feature flag not applicable

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — isolated to this function + a
      new index with no other dependents
- [x] No silent behavior change — nothing here changes what dispatches, only
      what's observable

## What was NOT verified

Not tested against a live/staging Supabase instance — the migration was
reviewed for syntax and convention (`IF NOT EXISTS`, additive, matches the
style of migration 114) but not actually applied and measured for index-build
time against production-scale data. No automated test exercises the new
cap-alert branch specifically (it's a straightforward `len(...) >= N` check on
already-tested query logic, judged low-risk enough to not warrant a dedicated
test, but flagging that call rather than leaving it implied).
