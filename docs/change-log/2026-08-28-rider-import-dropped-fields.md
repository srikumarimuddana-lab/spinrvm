# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code session (spinr migration work) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (rider account data) |
| PR / commit link | #4633 (`claude/migration-batch-readiness-wicr1d`) |
| Related issue or gap ID | Found via `ruff check` during the riders gap-analysis track (parallel worktree subagent), surfaced to the user, sent back into this session as a task |

## 1. Issue / gap identified

`backend/services/rider_import_service.py`'s `build_plan()` parses three CSV columns —
`ratings`, `temp_email`, `timezone` — into local variables (`ratings_raw`, `temp_email`, `tz`)
that were never read anywhere afterward. `ruff check` confirmed with 3 F841 (unused local
variable) findings. Every rider-import CSV row's ratings/temp_email/timezone data was silently
parsed and then discarded, on both the create and update paths.

## 2. Root cause

Checked, not assumed: no `users` column exists for any of the three. `total_ratings` exists on
`drivers` (migration 61), not `users`. `timezone` exists on `service_areas` and corporate tables
(migrations 105, 27), not `users`. No `temp_email`-shaped column exists anywhere. Grepped every
other code path for a rider-side write to any of these three field names — none found. So this
wasn't a "forgot to wire it up" bug in the usual sense — there was never a live column to wire it
to. Additionally, `ratings` is genuinely ambiguous in the source data:
`HEADER_ALIASES` maps both `no_of_rides` and `rating` onto the same `ratings` key, meaning the old
export itself doesn't distinguish a star rating from a ride count — writing it to a column named
"rating" would have asserted a meaning the data doesn't actually confirm.

## 3. Fix / remediation

Rather than either (a) inventing a new speculative `users` column with no demonstrated live
consumer, or (b) silently deleting real historical data, these three values are now preserved
read-only under `legacy_import_metadata.rider_csv_import` (the same provenance sub-key this file
already stamps on every row it touches) — the identical "history, not a live field" pattern
`driver_import_service.py` already uses for `was_deleted_in_source`/`was_blocked_in_source`/
`incomplete_profile_in_source`. New `_rider_csv_import_entry()` helper builds this dict once,
used by both the create and update paths; only non-empty values are included, matching this
file's existing conditional-inclusion style. The update path's trigger condition was also
broadened: previously an update was only queued when a live field changed
(`len(update_fields) > 1`); now it also queues when there's new history to record even if
nothing live changed, so temp_email/timezone data on an otherwise-unchanged existing account
isn't silently dropped either.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `rider_import_service.py`'s `build_plan()`.** Grepped for other
  readers of `legacy_import_metadata.rider_csv_import` before changing its shape — the only
  consumer is `docs/change-log/2026-08-17-rider-provenance-backfill-executed.md`'s one-time SQL
  UPDATE (already run, doesn't re-read this shape) and this file's own resume/dup-detection
  logic, which reads `legacy_import_metadata` as a whole dict (`existing_user.get(
  "legacy_import_metadata") or {}`) and merges onto it — never assumes a fixed set of keys inside
  `rider_csv_import`, so adding three new optional keys doesn't break anything reading it.
- No live `users` column is touched — this is purely additive to a JSONB metadata column already
  used for exactly this purpose.
- The update-trigger broadening (queuing an update for history-only changes) means a CSV row
  whose only new information is `temp_email`/`timezone` now produces a real `UPDATE` where it
  previously produced none. Checked: `commit_plan`'s update loop (`for upd in
  plan.users_to_update: ... supabase.table("users").update(upd).eq("id", upd["id"]).execute()`)
  handles a metadata-only update payload identically to any other — no special-casing needed,
  confirmed by the new tests actually exercising this path end-to-end through the real endpoint.
- `email` and `temp_email` values now sit side-by-side in the same JSONB column on the same row.
  This is not a new PII-exposure surface: the live `email` column already stores the same class
  of data; nothing new is added to logs, Sentry, or analytics (this importer's report/print
  functions already carry only row numbers/field/message, unchanged).

## 5. User-experience effect

None — backend-only, no route contract change (the same request/response shape), no admin-facing
behavior change beyond what an operator can see by inspecting `legacy_import_metadata` directly
(not surfaced in any UI).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/rider_import_service.py` | New `_rider_csv_import_entry()` helper; both `build_plan()` write paths now call it instead of a hardcoded 3-key dict; update-path trigger condition broadened to include history-only changes; explanatory comment on why these three fields have no live column | Stop silently dropping real parsed data |
| `backend/tests/test_admin_rider_import.py` | Two new tests: create-path preserves all three as history and never promotes them to a live column; update-path triggers on history-only change | Cover the new behavior end-to-end through the real HTTP endpoint |

## 7. Before / after

```python
# Before
ratings_raw = (row.get("ratings") or "").strip()
temp_email = (row.get("temp_email") or "").strip() or None
tz = (row.get("timezone") or "").strip() or None
# ... (never read again)
user_row["legacy_import_metadata"] = {
    "rider_csv_import": {"batch": batch, "source": IMPORT_SOURCE, "imported_at": now_iso}
}
```

```python
# After
ratings_raw = (row.get("ratings") or "").strip() or None
temp_email = (row.get("temp_email") or "").strip() or None
tz = (row.get("timezone") or "").strip() or None
# ...
user_row["legacy_import_metadata"] = {
    "rider_csv_import": _rider_csv_import_entry(
        batch, now_iso, ratings_raw=ratings_raw, temp_email=temp_email, tz=tz
    )
}
```

## 8. Rollback plan

No feature flag exists or is needed. `git revert` is sufficient and complete: this importer has
no confirmed production run of `commit_plan` (see the riders gap-analysis finding, same date —
only a one-time manual SQL UPDATE has ever touched `rider_csv_import`, never this code path), so
there is no live data in the new shape to clean up. If it is later run and needs reversal, the
three new sub-keys can be stripped from `legacy_import_metadata.rider_csv_import` per affected
row without touching any other field — nothing else was written by this change.

## 9. Verification performed

- [x] `ruff check backend/services/rider_import_service.py` — clean (the 3 F841 findings that
      motivated this fix are gone).
- [x] `ruff format --check` — clean on both changed files.
- [x] `pytest backend/tests/test_admin_rider_import.py` — 21/21 pass (19 existing + 2 new),
      zero regression on any existing test.
- [x] Blast-radius grep performed: confirmed `legacy_import_metadata.rider_csv_import`'s only
      other reader (the one-time SQL backfill doc) doesn't re-read this shape, and this file's
      own merge logic doesn't assume a fixed key set.
- [x] Schema check performed, not assumed: grepped `backend/migrations/*.sql` for `ratings`,
      `temp_email`, and `timezone` columns on `users` before deciding there was no live target —
      documented in §2.

**Not verified:** this importer has still never been run against production (confirmed by the
riders gap-analysis track, same date) — the new behavior is unit/HTTP-layer tested against the
fake-Supabase harness only, not exercised against a real database.

## 10. Sign-off

- [x] Rollback plan is concrete: no live data in the new shape exists yet; `git revert` is
      complete.
- [x] Blast radius stated: grepped, not assumed.
- [x] No silent behavior change to an already-shipped flow — this importer has never run against
      production, so there is no live behavior being changed underneath anyone.
