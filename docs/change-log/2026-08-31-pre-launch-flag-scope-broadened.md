# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-30-pre-launch-flag-tool.md` and `2026-08-30-pre-launch-filter-drivers-rides.md` |

## 1. Issue / gap identified

The original pre-launch flag tool only flagged 310 of 909 legacy-imported drivers.
599 legacy-imported drivers were left unflagged and fully visible in the default
admin Drivers list — including 272 of the 487 "Unnamed Legacy Driver" placeholder
rows (themselves already confirmed to have zero ride linkage). The owner asked to
flag all dormant legacy-imported drivers, not just the source+date-gated subset.

## 2. Root cause

The original criterion required BOTH a top-level `legacy_import_metadata.source`
key AND `created_at` before Spinr's 2026-03-30 launch. Two things narrowed it more
than intended:

- A driver the mongo importer **linked** to an existing rider account or
  **enriched** onto an existing driver (rather than net-new-inserting) carries
  `mongo_driver_history` but no top-level `source` key — 25 such rows were silently
  ineligible.
- Legacy rows preserve their *original* old-app signup date. The old app kept
  accepting real signups past Spinr's own launch (it isn't decommissioned until
  Oct 31), so a large share of genuinely-dormant legacy rows have a post-launch
  `created_at` and were excluded by the date gate for a reason unrelated to
  whether they're real activity.

## 3. Fix / remediation

`backend/services/pre_launch_flag_service.py`'s `_fetch_pre_launch_driver_candidates`:

- "Legacy-imported" now means carrying **either** a top-level `source` key **or**
  a `mongo_driver_history` key — fetched as two separate single-key JSONB-path
  queries and unioned by id (matches this codebase's existing "resolve ids from
  N queries, then combine" convention; avoids a fragile `.or_()` + JSONB-path
  PostgREST call). Confirmed against production this is the complete partition:
  of 910 total driver rows, 697+187 carry `source`, 25 carry only
  `mongo_driver_history`, and exactly 1 (the lone organic signup) carries neither.
- The `created_at < LAUNCH_DATE` filter is removed entirely.
- **Unchanged, deliberately**: the zero-activity guard (zero rides ever driven,
  zero `driver_insurance_periods` rows ever) still applies unconditionally to
  every candidate under both marker-key shapes. Broadening what counts as
  "legacy-imported" must never widen who this guard excludes.

Rides candidacy (`_fetch_pre_launch_ride_candidates`) is untouched — it was never
date *or* source restricted beyond `created_at < LAUNCH_DATE`, and this change
doesn't touch it.

## 4. Risk & impact on existing functionality

- **Blast radius, checked directly**: `_fetch_pre_launch_driver_candidates` has
  exactly one caller (`build_pre_launch_flag_plan`), which has exactly one caller
  (the admin route pair `routes/admin/pre_launch_flag.py`'s preview/commit
  endpoints). `fetch_pre_launch_flagged_ids` (the separate read used by the
  drivers/rides list filter shipped in the prior change) is untouched — it reads
  `pre_launch_test = true` directly and has no dependency on how a row became
  eligible to be flagged.
- **Real, live-verified preview of this change's effect** (read-only SQL against
  production, same predicate the new code runs): 599 legacy-imported drivers are
  currently unflagged. Of those, 65 are correctly excluded by the (unchanged)
  activity guard — real drivers with a ride or insurance-period history. **534
  would newly become flag candidates** on the next Preview run. Combined with the
  310 already flagged, that's 844/909 legacy-imported drivers flagged, leaving 65
  genuinely-active legacy-sourced drivers + 1 organic driver visible by default —
  the intended outcome.
- **Still additive-only, still a Preview→Apply flow.** This change only widens
  which rows `build_pre_launch_flag_plan` *offers* as candidates; it does not
  touch `apply_pre_launch_flags`, the write path, the guard/idempotency logic, or
  the admin routes. Nothing here writes to production by itself — the owner still
  runs Preview then Apply via the admin dashboard, same as before.
- **No change to ride candidacy, the filter UI, or any other consumer of
  `legacy_import_metadata`.**

## 5. User-experience effect

Admin-facing only. Once re-run, 534 more drivers move from "hidden by nothing" to
"hidden by the pre-launch filter's default" — an admin using the existing
Drivers-list filter (shipped in the prior change) will see far fewer dormant
legacy rows by default. No rider/driver-facing change; a flagged driver's own
account, status, or dispatch eligibility is never touched (same additive-only
guarantee as the original tool).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/pre_launch_flag_service.py` | Broadened driver-candidacy predicate (two marker keys, no date gate); module docstring updated | Cover the 599 currently-unflagged legacy drivers |
| `backend/tests/test_pre_launch_flag_service.py` | Replaced the now-inverted date-gate test; added 3 new tests for the `mongo_driver_history`-only shape and union-safety | Lock in the new behavior and the still-unconditional activity guard |

## 7. Before / after

```python
# Before
legacy_pre_launch = (
    supabase.table("drivers")
    .select("id,legacy_import_metadata")
    .filter("legacy_import_metadata->>source", "not.is", "null")
    .filter("legacy_import_metadata->>pre_launch_test", "is", "null")
    .lt("created_at", LAUNCH_DATE)
    .execute()
    .data
    or []
)
```

```python
# After
_LEGACY_IMPORT_MARKER_KEYS = ("source", "mongo_driver_history")

def _fetch_drivers_with_metadata_key(key: str) -> list[dict[str, Any]]:
    return (
        supabase.table("drivers")
        .select("id,legacy_import_metadata")
        .filter(f"legacy_import_metadata->>{key}", "not.is", "null")
        .filter("legacy_import_metadata->>pre_launch_test", "is", "null")
        .execute()
        .data
        or []
    )

by_id: dict[str, dict[str, Any]] = {}
for key in _LEGACY_IMPORT_MARKER_KEYS:
    for row in _fetch_drivers_with_metadata_key(key):
        if row.get("id"):
            by_id[row["id"]] = row
legacy_pre_launch = list(by_id.values())
```

## 8. Rollback plan

`git revert` on the code change — purely a candidacy-predicate change, no data
change, no schema change. The 534 newly-flagged drivers (once a future Preview→
Apply run flags them) are themselves reversible independently: `pre_launch_test`/
`pre_launch_flag` are additive JSONB keys on `legacy_import_metadata`, removable
with a targeted `UPDATE ... SET legacy_import_metadata = legacy_import_metadata -
'pre_launch_test' - 'pre_launch_flag' WHERE legacy_import_metadata->'pre_launch_flag'->>'batch' = '<batch>'`
— the same per-batch rollback the original tool's design already supports.

## 9. Verification performed

- [x] `pytest tests/test_pre_launch_flag_service.py tests/test_admin_pre_launch_flag.py` — 23 passed, 0 regressions.
- [x] `ruff check` / `ruff format --check` on both touched files — clean.
- [x] Live read-only SQL against production, running the exact new predicate
  (both marker keys, activity guard, no date filter): 599 unflagged, 65 correctly
  excluded for activity, 534 would be newly flagged. Not an estimate — the real
  expected Preview output.
- [x] Blast-radius grep: `_fetch_pre_launch_driver_candidates` has exactly one
  caller; the drivers/rides list filter's own flagged-id lookup
  (`fetch_pre_launch_flagged_ids`) is independent and unaffected.

## What was NOT verified

- The actual `build_pre_launch_flag_plan()` / `apply_pre_launch_flags()` run
  against production was not executed from this session — no live write
  credentials exist here, matching every other importer/backfill in this
  migration effort. The 534 figure is a live-data-verified prediction of what
  Preview will show, not a confirmed post-Apply count.
- Whether any of the 65 activity-guard-excluded drivers should themselves be
  reviewed for other legacy backfill work (SIN/DOB, vehicle history) is out of
  scope for this change — they were already real, active drivers before this
  change and remain so.
